#!/usr/bin/env python3
"""Process YouTube transcripts using Groq gpt-oss-120b API.

Segments transcripts into topics, extracts claims, classifies audiences/tags.
Uses async httpx for full concurrency control with rate limiting.

Resume-safe: skips videos that already have output JSON.

Usage:
    CLASSIFY_API_KEY=gsk_xxx \
    CLASSIFY_API_BASE=https://api.groq.com/openai/v1 \
    python3 process_yt_groq.py \
      --input-dir /path/to/5-youtube-transcripts/ \
      --output-dir youtube_processed/ \
      --concurrency 3 \
      --max-rpm 40 \
      --model openai/gpt-oss-120b
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections import deque
from difflib import SequenceMatcher
from typing import Iterator


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE = os.environ.get("CLASSIFY_API_BASE", "https://api.groq.com/openai/v1")
API_ENDPOINT = f"{API_BASE.rstrip('/')}/chat/completions"

SYSTEM_MESSAGE = (
    "Output ONLY valid JSON arrays. No explanation, no markdown fences, no thinking."
)

AUDIENCES = (
    "general_fitness,bodybuilding,weight_loss,muscle_gain,strength_training,"
    "endurance_athletes,combat_sports,calisthenics,women_fitness,menopause,pcos,"
    "postpartum,pregnancy,seniors,youth_fitness,adhd,mental_health,"
    "eating_disorder_recovery,glp1_users,diabetes,prediabetes,metabolic_health,"
    "thyroid,autoimmune,gut_health,vegan_plant_based,injury_rehab,cardiac_rehab,"
    "addiction_recovery,sleep_optimization,coaches,coach_business,beginners,"
    "intermediate,advanced"
)

CONTEXT_TAGS = (
    "protein,creatine,supplements,meal_prep,macros,carbs,fats,micronutrients,"
    "hydration,resistance_training,hiit,cardio,mobility,hypertrophy,fat_loss,"
    "muscle_building,body_recomposition,cutting,bulking,testosterone,estrogen,"
    "cortisol,insulin,sleep,stress_management,inflammation,recovery,"
    "injury_prevention,motivation,habit_building,body_image,gut_microbiome,"
    "evidence_based,myth_busting,recipe,meal_plan,program_design"
)

CATEGORIES = (
    "training,nutrition,supplements,recovery,mental_health,womens_health,"
    "medical_conditions,coaching_business,scientific_research,lifestyle,"
    "recipes_meal_planning"
)

SUBCATEGORIES = (
    "hypertrophy,strength,endurance,mobility,calisthenics,protein,carbs,fats,"
    "micronutrients,meal_prep,creatine,caffeine,vitamin_d,omega3,sleep,stress,"
    "injury_prevention,rehab,depression,anxiety,body_image,adhd_management,"
    "menopause,pcos,pregnancy,postpartum,thyroid,autoimmune,glp1,"
    "diabetes_management,metabolic_syndrome,pricing,client_acquisition,"
    "programming,systematic_review,rct,meta_analysis,weight_loss,muscle_gain,"
    "body_recomposition"
)


# ---------------------------------------------------------------------------
# Rate Limiter (same pattern as classify_with_llm.py)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Async rate limiter that enforces a maximum requests per minute."""

    def __init__(self, max_rpm: int = 40):
        self.max_rpm = max_rpm
        self.timestamps: deque = deque()

    async def acquire(self):
        now = time.monotonic()
        while self.timestamps and self.timestamps[0] < now - 60:
            self.timestamps.popleft()
        if len(self.timestamps) >= self.max_rpm:
            sleep_time = 60.0 - (now - self.timestamps[0])
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            now = time.monotonic()
            while self.timestamps and self.timestamps[0] < now - 60:
                self.timestamps.popleft()
        self.timestamps.append(time.monotonic())


# ---------------------------------------------------------------------------
# Stats tracking
# ---------------------------------------------------------------------------

class Stats:
    def __init__(self, total_videos: int = 0):
        self.total_videos = total_videos
        self.processed = 0
        self.failed = 0
        self.retries = 0
        self.chunks_sent = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.start_time = time.time()

    def cost_estimate(self) -> float:
        """Groq pricing estimate."""
        input_cost = (self.input_tokens / 1_000_000) * 0.30
        output_cost = (self.output_tokens / 1_000_000) * 0.90
        return input_cost + output_cost

    def elapsed(self) -> float:
        return time.time() - self.start_time

    def progress_line(self) -> str:
        elapsed = self.elapsed()
        rate = self.processed / elapsed if elapsed > 0 else 0
        return (
            f"[{self.processed}/{self.total_videos}] "
            f"{self.failed} failed | "
            f"{rate:.1f} vids/s | "
            f"~${self.cost_estimate():.2f} spent"
        )


# ---------------------------------------------------------------------------
# Pure functions: transcript parsing
# ---------------------------------------------------------------------------

def parse_transcript(content: str) -> dict:
    """Parse a transcript .md file with YAML frontmatter + body text.

    Returns dict with keys: source_id, title, channel, author, word_count,
    duration_seconds, body (the raw transcript text).
    """
    content = content.strip()

    # Check for YAML frontmatter
    if not content.startswith("---"):
        return {
            "source_id": "",
            "title": "",
            "channel": "",
            "author": "",
            "word_count": 0,
            "duration_seconds": 0,
            "body": content,
        }

    # Find the closing ---
    second_fence = content.find("---", 3)
    if second_fence == -1:
        return {
            "source_id": "",
            "title": "",
            "channel": "",
            "author": "",
            "word_count": 0,
            "duration_seconds": 0,
            "body": content,
        }

    yaml_block = content[3:second_fence].strip()
    body = content[second_fence + 3:].strip()

    # Simple YAML parsing (no pyyaml dependency)
    meta = {}
    for line in yaml_block.split("\n"):
        line = line.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if val == "null" or val == "":
            val = None
        elif val == "[]":
            val = []
        else:
            # Try numeric
            try:
                val = int(val)
            except (ValueError, TypeError):
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    pass
        meta[key] = val

    return {
        "source_id": meta.get("source_id", "") or "",
        "title": meta.get("title", "") or "",
        "channel": meta.get("channel", "") or "",
        "author": meta.get("author", "") or "",
        "word_count": meta.get("word_count") or 0,
        "duration_seconds": meta.get("duration_seconds") or 0,
        "body": body,
    }


# ---------------------------------------------------------------------------
# Pure functions: text trimming
# ---------------------------------------------------------------------------

def trim_transcript_text(
    text: str, skip_start: int = 500, skip_end: int = 200
) -> str:
    """Trim intro and outro words from transcript text.

    Args:
        text: Raw transcript text
        skip_start: Number of words to skip at the beginning (intro/sponsor)
        skip_end: Number of words to skip at the end (outro)

    Returns:
        Trimmed text. If text is shorter than skip_start, returns all text.
        If after skipping start there are fewer words than skip_end, returns empty.
    """
    words = text.split()
    total = len(words)

    if total <= skip_start:
        # Short transcript: return everything
        return text

    if total <= skip_start + skip_end:
        # After trimming intro, not enough for outro trim => return empty
        return ""

    trimmed = words[skip_start: total - skip_end]
    return " ".join(trimmed)


# ---------------------------------------------------------------------------
# Pure functions: word-based chunking
# ---------------------------------------------------------------------------

def chunk_text(
    text: str, chunk_size: int = 2500, overlap: int = 200
) -> list[dict]:
    """Split text into chunks of approximately chunk_size words with overlap.

    Returns list of dicts with keys: chunk_index, text, word_range.
    word_range is [start_word_index, end_word_index] (exclusive end).
    """
    if not text.strip():
        return []

    words = text.split()
    total = len(words)

    if total <= chunk_size:
        return [
            {
                "chunk_index": 0,
                "text": text,
                "word_range": [0, total],
            }
        ]

    chunks = []
    start = 0
    chunk_idx = 0

    while start < total:
        end = min(start + chunk_size, total)
        chunk_words = words[start:end]
        chunks.append({
            "chunk_index": chunk_idx,
            "text": " ".join(chunk_words),
            "word_range": [start, end],
        })
        chunk_idx += 1

        if end >= total:
            break

        # Next chunk starts overlap words before the end of this chunk
        start = end - overlap

    return chunks


# ---------------------------------------------------------------------------
# Pure functions: prompt building
# ---------------------------------------------------------------------------

def build_segment_prompt(
    text: str, title: str = "", channel: str = ""
) -> str:
    """Build the LLM prompt for segment extraction from a transcript chunk.

    Compact prompt following CLASSIFY-PROMPT-COMPACT.md style.
    """
    return f"""Segment this transcript into topics. For each segment return JSON:
{{"segment_id":N,"title":"topic title","summary":"2-3 sentences","claims":["claim1"],"audiences":[],"context_tags":[],"category":"","subcategory":"","expertise_level":""}}

Video: "{title}" by {channel}

audiences: {AUDIENCES}
context_tags (3-8): {CONTEXT_TAGS}
category (one): {CATEGORIES}
subcategory (one): {SUBCATEGORIES}
expertise_level (one): beginner,intermediate,advanced,professional,scientific

Rules:
- Skip intro/sponsor sections
- Focus on substantive fitness/nutrition content
- claims: factual assertions made in the segment
- Return ONLY a JSON array of segments

Transcript:
{text}"""


# ---------------------------------------------------------------------------
# Pure functions: response parsing
# ---------------------------------------------------------------------------

def parse_segment_response(response_text: str) -> list[dict]:
    """Parse LLM response text into a list of segment dicts.

    Handles: clean JSON, markdown fences, thinking tags, surrounding text.
    Returns empty list on parse failure.
    """
    text = response_text.strip()

    # Strip <think>...</think> tags
    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
    text = text.strip()

    # Try direct parse
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        return []
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code fence
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        try:
            data = json.loads(fence_match.group(1).strip())
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    # Try finding a JSON array in the text
    bracket_match = re.search(r"\[.*\]", text, re.DOTALL)
    if bracket_match:
        try:
            data = json.loads(bracket_match.group(0))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    return []


# ---------------------------------------------------------------------------
# Pure functions: segment merging
# ---------------------------------------------------------------------------

def _title_similarity(a: str, b: str) -> float:
    """Compute similarity ratio between two segment titles."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def merge_segments(chunk_segments: list[list[dict]]) -> list[dict]:
    """Merge and deduplicate segments from overlapping chunks.

    When two adjacent chunks produce segments with very similar titles
    (similarity > 0.7), they are merged: the longer summary is kept,
    claims are unioned (deduplicated).

    Args:
        chunk_segments: List of segment lists, one per chunk.

    Returns:
        Flat list of merged segments with renumbered segment_ids.
    """
    if not chunk_segments:
        return []

    # Start with the first chunk's segments
    merged = list(chunk_segments[0])

    for chunk_segs in chunk_segments[1:]:
        if not chunk_segs:
            continue
        if not merged:
            merged.extend(chunk_segs)
            continue

        # Check if the first segment(s) of the new chunk overlap with
        # the last segment(s) of the current merged list
        matched_indices = set()
        for new_seg in chunk_segs:
            best_match = -1
            best_sim = 0.0
            for i in range(max(0, len(merged) - 3), len(merged)):
                sim = _title_similarity(merged[i]["title"], new_seg["title"])
                if sim > best_sim:
                    best_sim = sim
                    best_match = i

            if best_sim > 0.7 and best_match not in matched_indices:
                # Merge: keep longer summary, union claims
                existing = merged[best_match]
                if len(new_seg.get("summary", "")) > len(existing.get("summary", "")):
                    existing["summary"] = new_seg["summary"]
                # Union claims
                existing_claims = set(existing.get("claims", []))
                new_claims = set(new_seg.get("claims", []))
                existing["claims"] = list(existing_claims | new_claims)
                # Extend word_range
                if "word_range" in new_seg and "word_range" in existing:
                    existing["word_range"] = [
                        min(existing["word_range"][0], new_seg["word_range"][0]),
                        max(existing["word_range"][1], new_seg["word_range"][1]),
                    ]
                matched_indices.add(best_match)
            else:
                merged.append(new_seg)

    # Renumber segment IDs
    for i, seg in enumerate(merged):
        seg["segment_id"] = i + 1

    return merged


# ---------------------------------------------------------------------------
# Pure functions: output paths and resume logic
# ---------------------------------------------------------------------------

def output_path(output_dir: str, channel: str, video_id: str) -> str:
    """Generate output file path for a processed video."""
    return os.path.join(output_dir, channel, f"{video_id}.json")


def is_already_processed(output_dir: str, channel: str, video_id: str) -> bool:
    """Check if a video has already been processed (output JSON exists)."""
    return os.path.exists(output_path(output_dir, channel, video_id))


def build_output_document(
    video_id: str,
    channel: str,
    title: str,
    total_words: int,
    segments: list[dict],
) -> dict:
    """Build the final output JSON document for a processed video."""
    return {
        "video_id": video_id,
        "channel": channel,
        "title": title,
        "total_words": total_words,
        "segments": segments,
    }


def write_output(
    output_dir: str, channel: str, video_id: str, document: dict
) -> str:
    """Write processed output to disk. Returns the file path."""
    fpath = output_path(output_dir, channel, video_id)
    os.makedirs(os.path.dirname(fpath), exist_ok=True)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(document, f, ensure_ascii=False, indent=2)
    return fpath


# ---------------------------------------------------------------------------
# Pure functions: request payload
# ---------------------------------------------------------------------------

def build_request_payload(prompt: str, model: str) -> dict:
    """Build the HTTP JSON payload for the Groq API chat/completions."""
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 8192,
    }


# ---------------------------------------------------------------------------
# Pure functions: discover transcripts
# ---------------------------------------------------------------------------

def discover_transcripts(input_dir: str) -> list[tuple[str, str, str]]:
    """Discover all transcript .md files in the input directory.

    Expects structure: input_dir/CHANNEL/articles/VIDEO_ID.md

    Returns list of (channel, video_id, filepath) tuples.
    """
    results = []
    if not os.path.isdir(input_dir):
        return results

    for entry in sorted(os.listdir(input_dir)):
        articles_dir = os.path.join(input_dir, entry, "articles")
        if not os.path.isdir(articles_dir):
            continue
        channel = entry
        for fname in sorted(os.listdir(articles_dir)):
            if not fname.endswith(".md"):
                continue
            video_id = fname[:-3]  # strip .md
            filepath = os.path.join(articles_dir, fname)
            results.append((channel, video_id, filepath))

    return results


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Process YouTube transcripts using Groq gpt-oss-120b API"
    )
    parser.add_argument(
        "--input-dir",
        type=str,
        default=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "content", "5-youtube-transcripts",
        ),
        help="Directory containing channel/articles/ transcript files",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="youtube_processed/",
        help="Directory for processed output (default: youtube_processed/)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Max concurrent API requests (default: 3)",
    )
    parser.add_argument(
        "--max-rpm",
        type=int,
        default=40,
        help="Max requests per minute (default: 40)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="openai/gpt-oss-120b",
        help="Model name (default: openai/gpt-oss-120b)",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

def should_retry(status_code: int, attempt: int, max_retries: int) -> bool:
    if attempt >= max_retries - 1:
        return False
    if status_code == 429:
        return True
    if status_code in (500, 502, 503):
        return True
    return False


def backoff_time(attempt: int, is_rate_limit: bool = False) -> float:
    if is_rate_limit:
        return 5.0 * (2 ** attempt)
    return float(2 ** attempt)


# ---------------------------------------------------------------------------
# Async API interaction
# ---------------------------------------------------------------------------

async def process_single_chunk(
    client,
    semaphore: asyncio.Semaphore,
    rate_limiter: RateLimiter,
    chunk: dict,
    title: str,
    channel: str,
    model: str,
    stats: Stats,
) -> list[dict]:
    """Process a single text chunk through the Groq API.

    Returns a list of segment dicts for this chunk.
    """
    import httpx

    prompt = build_segment_prompt(chunk["text"], title=title, channel=channel)
    payload = build_request_payload(prompt, model)
    api_key = os.environ.get("CLASSIFY_API_KEY", "")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    max_retries_429 = 5
    max_retries_server = 3

    async with semaphore:
        for attempt in range(max(max_retries_429, max_retries_server)):
            await rate_limiter.acquire()

            try:
                response = await client.post(
                    API_ENDPOINT,
                    json=payload,
                    headers=headers,
                    timeout=120.0,
                )

                if response.status_code != 200:
                    status = response.status_code
                    if status == 429:
                        effective_max = max_retries_429
                        is_rl = True
                    elif status in (500, 502, 503):
                        effective_max = max_retries_server
                        is_rl = False
                    else:
                        print(f"    chunk {chunk['chunk_index']} ERROR: HTTP {status}")
                        return []

                    if should_retry(status, attempt, effective_max):
                        wait = backoff_time(attempt, is_rate_limit=is_rl)
                        stats.retries += 1
                        print(f"    chunk {chunk['chunk_index']} HTTP {status}, "
                              f"retry {attempt + 1}/{effective_max} in {wait:.1f}s")
                        await asyncio.sleep(wait)
                        continue
                    else:
                        print(f"    chunk {chunk['chunk_index']} ERROR: HTTP {status} "
                              f"after {attempt + 1} attempts")
                        return []

                resp_json = response.json()
                usage = resp_json.get("usage", {})
                stats.input_tokens += usage.get("prompt_tokens", 0)
                stats.output_tokens += usage.get("completion_tokens", 0)
                stats.chunks_sent += 1

                content = (
                    resp_json.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )

                segments = parse_segment_response(content)

                # Adjust word_range based on chunk offset
                base_offset = chunk["word_range"][0]
                for seg in segments:
                    if "word_range" not in seg:
                        seg["word_range"] = [base_offset, chunk["word_range"][1]]

                return segments

            except Exception as e:
                if attempt < max_retries_server - 1:
                    stats.retries += 1
                    wait = backoff_time(attempt)
                    print(f"    chunk {chunk['chunk_index']} exception: {e}, "
                          f"retry in {wait:.1f}s")
                    await asyncio.sleep(wait)
                    continue
                print(f"    chunk {chunk['chunk_index']} FAILED: {e}")
                return []

    return []


async def process_video(
    client,
    semaphore: asyncio.Semaphore,
    rate_limiter: RateLimiter,
    channel: str,
    video_id: str,
    filepath: str,
    model: str,
    output_dir: str,
    stats: Stats,
) -> bool:
    """Process a single video transcript end-to-end.

    Returns True on success, False on failure.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        meta = parse_transcript(content)
        body = meta["body"]
        title = meta["title"]
        total_words = len(body.split())

        # Trim intro/outro
        trimmed = trim_transcript_text(body, skip_start=500, skip_end=200)
        if not trimmed.strip():
            # Too short to process
            print(f"  SKIP {channel}/{video_id}: too short after trimming")
            stats.processed += 1
            return True

        # Chunk the trimmed text
        chunks = chunk_text(trimmed, chunk_size=2500, overlap=200)

        # Process each chunk
        chunk_results = []
        for chunk in chunks:
            segments = await process_single_chunk(
                client, semaphore, rate_limiter, chunk,
                title=title, channel=channel, model=model, stats=stats,
            )
            chunk_results.append(segments)

        # Merge segments across chunks
        merged = merge_segments(chunk_results)

        # Build output document
        doc = build_output_document(
            video_id=video_id,
            channel=channel,
            title=title,
            total_words=total_words,
            segments=merged,
        )

        # Write to disk
        write_output(output_dir, channel, video_id, doc)
        stats.processed += 1
        print(f"  OK {channel}/{video_id}: {len(merged)} segments")
        return True

    except Exception as e:
        stats.failed += 1
        print(f"  FAIL {channel}/{video_id}: {e}")
        return False


async def main_async(args: argparse.Namespace):
    """Main async entry point."""
    import httpx

    input_dir = args.input_dir
    output_dir = args.output_dir
    model = args.model

    print(f"Discovering transcripts in {input_dir}...")
    all_transcripts = discover_transcripts(input_dir)
    print(f"Found {len(all_transcripts)} transcripts")

    # Filter already processed
    pending = [
        (ch, vid, fp) for ch, vid, fp in all_transcripts
        if not is_already_processed(output_dir, ch, vid)
    ]
    print(f"{len(all_transcripts) - len(pending)} already processed, "
          f"{len(pending)} remaining")

    if not pending:
        print("Nothing to do!")
        return

    stats = Stats(total_videos=len(pending))
    semaphore = asyncio.Semaphore(args.concurrency)
    rate_limiter = RateLimiter(max_rpm=args.max_rpm)

    async with httpx.AsyncClient(timeout=120.0) as client:
        for ch, vid, fp in pending:
            await process_video(
                client, semaphore, rate_limiter,
                ch, vid, fp, model, output_dir, stats,
            )
            if stats.processed % 10 == 0:
                print(f"  {stats.progress_line()}")

    print(f"\nDone! {stats.progress_line()}")


def main():
    args = parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
