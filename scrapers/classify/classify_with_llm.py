#!/usr/bin/env python3
"""Classify articles using Together API with extreme async parallelism.

Uses asyncio + httpx for full concurrency control. Sends sub-batches of
articles to the LLM, with a semaphore-controlled concurrency limit and
a per-minute rate limiter.

Resume-safe: skips chunk files that already have a _classified.json output.

Usage:
    CLASSIFY_API_KEY=tgp_v1_xxx \
    python3 classify_with_llm.py \
      --input-dir classify_chunks/ \
      --output-dir classified_chunks/ \
      --batch-size 50 \
      --concurrency 50 \
      --max-rpm 600 \
      --model Qwen/Qwen3-235B-A22B-Instruct-2507-tput
"""

import argparse
import asyncio
import glob as glob_mod
import json
import os
import re
import sys
import time
from collections import deque
from typing import Iterator


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE = os.environ.get("CLASSIFY_API_BASE", "https://api.together.xyz/v1")
API_ENDPOINT = f"{API_BASE.rstrip('/')}/chat/completions"

PROMPT_TEMPLATE_PATH = os.environ.get("CLASSIFY_PROMPT_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "CLASSIFY-PROMPT.md",
)

SYSTEM_MESSAGE = (
    "Output ONLY valid JSON arrays. No explanation, no markdown fences, no thinking."
)


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Async rate limiter that enforces a maximum requests per minute."""

    def __init__(self, max_rpm: int = 600):
        self.max_rpm = max_rpm
        self.timestamps: deque = deque()

    async def acquire(self):
        """Wait until a request slot is available within the RPM window."""
        now = time.monotonic()

        # Remove timestamps older than 60 seconds
        while self.timestamps and self.timestamps[0] < now - 60:
            self.timestamps.popleft()

        # If at limit, sleep until the oldest timestamp expires
        if len(self.timestamps) >= self.max_rpm:
            sleep_time = 60.0 - (now - self.timestamps[0])
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            # Re-prune after sleeping
            now = time.monotonic()
            while self.timestamps and self.timestamps[0] < now - 60:
                self.timestamps.popleft()

        self.timestamps.append(time.monotonic())


# ---------------------------------------------------------------------------
# Stats tracking
# ---------------------------------------------------------------------------

class Stats:
    """Thread-safe statistics tracker for the classification run."""

    def __init__(self, total_articles: int = 0, total_chunks: int = 0):
        self.total_articles = total_articles
        self.total_chunks = total_chunks
        self.classified = 0
        self.failed = 0
        self.retries = 0
        self.chunks_done = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.start_time = time.time()

    def cost_estimate(self) -> float:
        """Estimate cost based on Together API pricing for Qwen3-235B.

        Together pricing (as of 2025):
        - Input: $0.30 per 1M tokens
        - Output: $0.50 per 1M tokens
        """
        input_cost = (self.input_tokens / 1_000_000) * 0.30
        output_cost = (self.output_tokens / 1_000_000) * 0.50
        return input_cost + output_cost

    def elapsed(self) -> float:
        return time.time() - self.start_time

    def progress_line(self) -> str:
        elapsed = self.elapsed()
        rate = self.chunks_done / elapsed if elapsed > 0 else 0
        remaining_chunks = self.total_chunks - self.chunks_done
        est_remaining = remaining_chunks / rate if rate > 0 else 0
        cost = self.cost_estimate()

        return (
            f"[{self.chunks_done}/{self.total_chunks} chunks] "
            f"{self.classified:,}/{self.total_articles:,} classified | "
            f"{self.failed:,} failed | "
            f"{rate:.1f} chunks/s | "
            f"est. {est_remaining:.0f}s remaining | "
            f"~${cost:.2f} spent"
        )


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

def should_retry(status_code: int, attempt: int, max_retries: int) -> bool:
    """Determine if an HTTP error should be retried.

    - 429 (rate limit): retry up to max_retries (default 5)
    - 500/502/503: retry up to max_retries (default 3)
    - Other status codes: do not retry
    """
    if attempt >= max_retries - 1:
        return False

    if status_code == 429:
        return True

    if status_code in (500, 502, 503):
        return True

    return False


def backoff_time(attempt: int, is_rate_limit: bool = False) -> float:
    """Calculate exponential backoff time.

    For rate limits (429), use a longer base.
    """
    if is_rate_limit:
        # Longer backoff for rate limits: 5, 10, 20, 40, ...
        return 5.0 * (2 ** attempt)
    else:
        # Standard backoff: 1, 2, 4, 8, ...
        return float(2 ** attempt)


# ---------------------------------------------------------------------------
# Pure functions (no side effects, fully testable)
# ---------------------------------------------------------------------------

def load_chunk_file(filepath: str) -> dict:
    """Load a chunk JSON file. Raises on invalid JSON."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def sub_batch_articles(
    articles: list[dict], batch_size: int = 50
) -> Iterator[list[dict]]:
    """Split articles into sub-batches of batch_size for LLM calls."""
    for i in range(0, len(articles), batch_size):
        yield articles[i : i + batch_size]


def build_llm_prompt(articles: list[dict]) -> str:
    """Build the full LLM prompt from the template + article data.

    Loads CLASSIFY-PROMPT.md and appends the articles JSON.
    """
    # Load the prompt template
    prompt_path = PROMPT_TEMPLATE_PATH
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            template = f.read()
    else:
        # Fallback inline prompt
        template = _fallback_prompt_template()

    # Format articles as a compact JSON block for the LLM
    articles_json = json.dumps(articles, indent=2, ensure_ascii=False)

    prompt = f"{template}\n\n## Articles to Classify\n\n```json\n{articles_json}\n```"
    return prompt


def _fallback_prompt_template() -> str:
    """Inline fallback in case CLASSIFY-PROMPT.md is missing."""
    return """# Content Classification Task

Classify each article below. For each article, return:

- **audiences**: array of strings - who benefits from this content
- **context_tags**: array of 3-8 topic tags
- **category**: primary category string
- **subcategory**: more specific category string
- **expertise_level**: one of beginner, intermediate, advanced, professional, scientific

Return a JSON array with one object per article, each containing:
`id`, `audiences`, `context_tags`, `category`, `subcategory`, `expertise_level`

Return ONLY the JSON array, no other text."""


def parse_llm_response(response_text: str) -> list[dict]:
    """Parse LLM response text into a list of classification dicts.

    Handles:
    - Clean JSON arrays
    - JSON wrapped in markdown code fences
    - JSON with surrounding explanatory text
    - JSON preceded by <think>...</think> tags (Qwen3 models)
    - Returns empty list on parse failure
    """
    text = response_text.strip()

    # Strip <think>...</think> tags (Qwen3 reasoning)
    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
    text = text.strip()

    # Try direct parse first
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


def classified_output_name(chunk_filename: str) -> str:
    """Convert a chunk filename to its classified output name.

    chunk_001.json -> chunk_001_classified.json
    """
    base, ext = os.path.splitext(chunk_filename)
    return f"{base}_classified{ext}"


def is_chunk_already_classified(
    chunk_filename: str, output_dir: str
) -> bool:
    """Check if a classified output file already exists for this chunk."""
    out_name = classified_output_name(chunk_filename)
    return os.path.exists(os.path.join(output_dir, out_name))


def write_classified_output(
    output_dir: str,
    chunk_filename: str,
    classifications: list[dict],
) -> str:
    """Write classified results to the output directory.

    Returns the path to the written file.
    """
    os.makedirs(output_dir, exist_ok=True)

    out_name = classified_output_name(chunk_filename)
    filepath = os.path.join(output_dir, out_name)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(classifications, f, ensure_ascii=False, indent=2)

    return filepath


def build_request_payload(prompt: str, model: str) -> dict:
    """Build the raw HTTP JSON payload for the Together API chat/completions."""
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.6,
        "max_tokens": 16384,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Classify articles using Together API with async parallelism"
    )
    parser.add_argument(
        "--input-dir",
        type=str,
        default="classify_chunks/",
        help="Directory containing chunk JSON files (default: classify_chunks/)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="classified_chunks/",
        help="Directory for classified output (default: classified_chunks/)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Articles per LLM call (default: 50)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=50,
        help="Max concurrent API requests (default: 50)",
    )
    parser.add_argument(
        "--max-rpm",
        type=int,
        default=600,
        help="Max requests per minute (default: 600)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="Qwen/Qwen3-235B-A22B-Instruct-2507-tput",
        help="Model name (default: Qwen/Qwen3-235B-A22B-Instruct-2507-tput)",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Async API interaction
# ---------------------------------------------------------------------------

async def classify_batch(
    client,
    semaphore: asyncio.Semaphore,
    rate_limiter: RateLimiter,
    articles: list[dict],
    model: str,
    stats: Stats,
    batch_label: str = "",
) -> list[dict]:
    """Classify a sub-batch of articles via the Together API.

    Uses the semaphore for concurrency control and rate_limiter for RPM control.
    Implements retry logic for 429, 500/502/503, and JSON parse failures.
    """
    import httpx

    prompt = build_llm_prompt(articles)
    payload = build_request_payload(prompt, model)
    api_key = os.environ.get("CLASSIFY_API_KEY", "")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    max_retries_429 = 5
    max_retries_server = 3
    json_retry_done = False

    async with semaphore:
        for attempt in range(max(max_retries_429, max_retries_server)):
            await rate_limiter.acquire()

            try:
                response = await client.post(
                    API_ENDPOINT,
                    json=payload,
                    headers=headers,
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
                        print(f"    {batch_label} ERROR: HTTP {status} - {response.text[:200]}")
                        return []

                    if should_retry(status, attempt, effective_max):
                        wait = backoff_time(attempt, is_rate_limit=is_rl)
                        stats.retries += 1
                        print(f"    {batch_label} HTTP {status}, retry {attempt + 1}/{effective_max} in {wait:.1f}s")
                        await asyncio.sleep(wait)
                        continue
                    else:
                        print(f"    {batch_label} ERROR: HTTP {status} after {attempt + 1} attempts")
                        return []

                resp_json = response.json()
                usage = resp_json.get("usage", {})
                stats.input_tokens += usage.get("prompt_tokens", 0)
                stats.output_tokens += usage.get("completion_tokens", 0)

                content = (
                    resp_json.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )

                classifications = parse_llm_response(content)

                if not classifications:
                    if not json_retry_done:
                        json_retry_done = True
                        stats.retries += 1
                        print(f"    {batch_label} JSON parse failed, retrying once...")
                        continue
                    else:
                        print(f"    {batch_label} WARNING: Empty/unparseable response after retry")
                        return []

                stats.classified += len(classifications)
                stats.chunks_done_batches = getattr(stats, 'chunks_done_batches', 0) + 1
                print(f"    {batch_label} OK: {len(classifications)} classified ({stats.classified}/{stats.total_articles} total)", flush=True)
                return classifications

            except httpx.TimeoutException:
                stats.retries += 1
                if attempt < max_retries_server - 1:
                    wait = backoff_time(attempt)
                    print(f"    {batch_label} TIMEOUT, retry {attempt + 1}/{max_retries_server} in {wait:.1f}s")
                    await asyncio.sleep(wait)
                    continue
                else:
                    print(f"    {batch_label} ERROR: Timeout after {attempt + 1} attempts")
                    return []

            except Exception as e:
                print(f"    {batch_label} ERROR: {type(e).__name__}: {e}")
                return []

    return []


async def process_chunk(
    client,
    semaphore: asyncio.Semaphore,
    rate_limiter: RateLimiter,
    chunk_path: str,
    output_dir: str,
    batch_size: int,
    model: str,
    stats: Stats,
) -> None:
    """Process a single chunk file: load, split, classify all sub-batches, write output."""
    chunk_basename = os.path.basename(chunk_path)

    # Resume support: skip already-classified chunks
    if is_chunk_already_classified(chunk_basename, output_dir):
        stats.chunks_done += 1
        return

    chunk_data = load_chunk_file(chunk_path)
    articles = chunk_data.get("articles", [])

    if not articles:
        stats.chunks_done += 1
        return

    # Split into sub-batches and classify them concurrently
    batches = list(sub_batch_articles(articles, batch_size))

    tasks = []
    for batch_idx, batch in enumerate(batches):
        label = f"{chunk_basename}[{batch_idx + 1}/{len(batches)}]"
        task = classify_batch(
            client, semaphore, rate_limiter,
            batch, model, stats, batch_label=label,
        )
        tasks.append(task)

    # Run all sub-batches for this chunk concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect classifications
    all_classifications = []
    for result in results:
        if isinstance(result, Exception):
            print(f"    {chunk_basename} sub-batch exception: {result}")
            stats.failed += batch_size  # approximate
        elif isinstance(result, list):
            all_classifications.extend(result)
        # Empty list means failure already logged

    # Count failures for batches that returned empty
    classified_count = len(all_classifications)
    expected_count = len(articles)
    if classified_count < expected_count:
        stats.failed += expected_count - classified_count

    # Write output
    if all_classifications:
        write_classified_output(output_dir, chunk_basename, all_classifications)
        stats.classified += classified_count

    stats.chunks_done += 1

    # Progress reporting every 10 chunks
    if stats.chunks_done % 10 == 0 or stats.chunks_done == stats.total_chunks:
        print(f"  {stats.progress_line()}")


async def async_main(args: argparse.Namespace) -> None:
    """Async entry point: discover chunks, launch parallel classification."""
    import httpx

    api_key = os.environ.get("CLASSIFY_API_KEY")
    if not api_key:
        print("ERROR: CLASSIFY_API_KEY environment variable is required")
        sys.exit(1)

    model = args.model

    # Find input chunk files
    pattern = os.path.join(args.input_dir, "chunk_*.json")
    chunk_files = sorted(glob_mod.glob(pattern))
    # Exclude already-classified files from input
    chunk_files = [f for f in chunk_files if "_classified" not in f]

    if not chunk_files:
        print(f"No chunk files found in {args.input_dir}")
        return

    # Count total articles across all unclassified chunks
    total_articles = 0
    unclassified_files = []
    for cf in chunk_files:
        basename = os.path.basename(cf)
        if not is_chunk_already_classified(basename, args.output_dir):
            data = load_chunk_file(cf)
            total_articles += len(data.get("articles", []))
            unclassified_files.append(cf)
        else:
            # Already done
            pass

    already_done = len(chunk_files) - len(unclassified_files)

    print(f"Found {len(chunk_files)} chunk files in {args.input_dir}")
    if already_done > 0:
        print(f"  {already_done} already classified (skipping)")
    print(f"  {len(unclassified_files)} to classify ({total_articles:,} articles)")
    print(f"Model: {model}")
    print(f"Batch size: {args.batch_size} articles per request")
    print(f"Concurrency: {args.concurrency} parallel requests")
    print(f"Rate limit: {args.max_rpm} requests/min")
    print()

    if not unclassified_files:
        print("All chunks already classified. Nothing to do.")
        return

    os.makedirs(args.output_dir, exist_ok=True)

    # Initialize concurrency controls
    semaphore = asyncio.Semaphore(args.concurrency)
    rate_limiter = RateLimiter(max_rpm=args.max_rpm)
    stats = Stats(
        total_articles=total_articles,
        total_chunks=len(unclassified_files),
    )

    # Process ALL chunks concurrently (semaphore controls actual concurrency)
    timeout = httpx.Timeout(120.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        tasks = [
            process_chunk(
                client, semaphore, rate_limiter,
                chunk_path, args.output_dir,
                args.batch_size, model, stats,
            )
            for chunk_path in unclassified_files
        ]

        await asyncio.gather(*tasks, return_exceptions=True)

    # Final report
    elapsed = stats.elapsed()
    cost = stats.cost_estimate()
    print()
    print("=" * 60)
    print("Classification complete:")
    print(f"  Classified: {stats.classified:,}")
    print(f"  Failed:     {stats.failed:,}")
    print(f"  Retries:    {stats.retries:,}")
    print(f"  Time:       {elapsed:.1f}s")
    if stats.classified > 0:
        print(f"  Rate:       {stats.classified / elapsed:.0f} articles/sec")
    print(f"  Tokens:     {stats.input_tokens:,} in / {stats.output_tokens:,} out")
    print(f"  Est. cost:  ${cost:.2f}")
    print("=" * 60)


def main():
    args = parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
