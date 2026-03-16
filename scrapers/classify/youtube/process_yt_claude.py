#!/usr/bin/env python3
"""Process YouTube transcripts using Claude Code Haiku agents.

Exports transcripts as JSON batches for processing by Claude Code /fast mode,
then imports the results back into the standard youtube_processed/ format.

Export: reads transcripts, trims/truncates, groups into batches of 5.
Import: reads Claude Code results, writes individual video JSONs.

Usage:
    # Export transcripts to processable batches
    python3 process_yt_claude.py export \
      --input-dir /path/to/5-youtube-transcripts/ \
      --output-dir youtube_batches/ \
      --batch-size 5

    # After Claude Code processes them, import results
    python3 process_yt_claude.py import \
      --input-dir youtube_results/ \
      --output-dir youtube_processed/
"""

import argparse
import json
import os
import sys

# Import shared functions from process_yt_groq
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from process_yt_groq import (
    AUDIENCES,
    CATEGORIES,
    CONTEXT_TAGS,
    SUBCATEGORIES,
    discover_transcripts,
    parse_transcript,
)


# ---------------------------------------------------------------------------
# Prompt for Claude Code batches
# ---------------------------------------------------------------------------

def build_batch_prompt() -> str:
    """Build the compact prompt included in each batch file.

    Designed to be token-efficient for Haiku processing.
    """
    return f"""Segment each transcript into topics. For each transcript, return a JSON object:
{{"video_id":"ID","channel":"ch","title":"title","segments":[...]}}

Per segment:
{{"segment_id":N,"title":"topic title","summary":"2-3 sentences","claims":["claim1"],"audiences":[],"context_tags":[],"category":"","subcategory":"","expertise_level":""}}

Skip intro/sponsor sections. Focus on substantive fitness/nutrition content.

audiences: {AUDIENCES}
context_tags (3-8): {CONTEXT_TAGS}
category (one): {CATEGORIES}
subcategory (one): {SUBCATEGORIES}
expertise_level (one): beginner,intermediate,advanced,professional,scientific

Return ONLY a JSON array with one object per transcript."""


# ---------------------------------------------------------------------------
# Prepare single transcript for batch
# ---------------------------------------------------------------------------

def prepare_transcript_for_batch(
    content: str,
    max_words: int = 3000,
    skip_start: int = 500,
) -> dict:
    """Prepare a single transcript for inclusion in a Claude Code batch.

    Trims to words skip_start..max_words for token efficiency.
    Short transcripts (< skip_start words) are included in full.

    Returns dict with: video_id, channel, title, text.
    """
    meta = parse_transcript(content)
    body = meta["body"]
    words = body.split()
    total = len(words)

    if total <= skip_start:
        # Short transcript: include everything
        trimmed_words = words
    elif total <= max_words:
        # Medium: skip intro but include all remaining
        trimmed_words = words[skip_start:]
    else:
        # Long: take words skip_start..max_words
        trimmed_words = words[skip_start:max_words]

    return {
        "video_id": meta["source_id"],
        "channel": meta["channel"],
        "title": meta["title"],
        "text": " ".join(trimmed_words),
    }


# ---------------------------------------------------------------------------
# Batch creation
# ---------------------------------------------------------------------------

def create_batches(
    transcripts: list[dict], batch_size: int = 5
) -> list[dict]:
    """Group prepared transcripts into batches.

    Each batch includes the prompt and a list of transcripts.

    Args:
        transcripts: List of prepared transcript dicts (from prepare_transcript_for_batch)
        batch_size: Number of transcripts per batch

    Returns:
        List of batch dicts with keys: batch_id, prompt, transcripts.
    """
    if not transcripts:
        return []

    prompt = build_batch_prompt()
    batches = []

    for i in range(0, len(transcripts), batch_size):
        batch_transcripts = transcripts[i : i + batch_size]
        batches.append({
            "batch_id": len(batches) + 1,
            "prompt": prompt,
            "transcripts": batch_transcripts,
        })

    return batches


# ---------------------------------------------------------------------------
# Export batches to disk
# ---------------------------------------------------------------------------

def export_batches(batches: list[dict], output_dir: str) -> list[str]:
    """Write batch files to output directory.

    Returns list of file paths written.
    """
    os.makedirs(output_dir, exist_ok=True)
    paths = []

    for batch in batches:
        filename = f"batch_{batch['batch_id']:03d}.json"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(batch, f, ensure_ascii=False, indent=2)
        paths.append(filepath)

    return paths


# ---------------------------------------------------------------------------
# Import results
# ---------------------------------------------------------------------------

def import_result_file(filepath: str) -> list[dict]:
    """Import a single Claude Code result file.

    Expects a JSON array of video result objects.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data
    elif isinstance(data, dict):
        return [data]
    return []


def write_imported_results(results: list[dict], output_dir: str) -> list[str]:
    """Write imported results to the standard youtube_processed/ format.

    Each result becomes output_dir/CHANNEL/VIDEO_ID.json.

    Returns list of file paths written.
    """
    paths = []

    for result in results:
        channel = result.get("channel", "unknown")
        video_id = result.get("video_id", "unknown")
        channel_dir = os.path.join(output_dir, channel)
        os.makedirs(channel_dir, exist_ok=True)

        filepath = os.path.join(channel_dir, f"{video_id}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        paths.append(filepath)

    return paths


# ---------------------------------------------------------------------------
# Resume-safe filtering
# ---------------------------------------------------------------------------

def filter_unprocessed(
    transcripts: list[tuple[str, str, str]],
    output_dir: str,
) -> list[tuple[str, str, str]]:
    """Filter out transcripts that already have output in youtube_processed/.

    Args:
        transcripts: List of (channel, video_id, filepath) tuples
        output_dir: Where processed outputs are stored

    Returns:
        Filtered list of unprocessed transcripts.
    """
    return [
        (ch, vid, fp)
        for ch, vid, fp in transcripts
        if not os.path.exists(os.path.join(output_dir, ch, f"{vid}.json"))
    ]


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments with export/import subcommands."""
    parser = argparse.ArgumentParser(
        description="Process YouTube transcripts for Claude Code Haiku agents"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Export subcommand
    export_parser = subparsers.add_parser(
        "export", help="Export transcripts as JSON batches for Claude Code"
    )
    export_parser.add_argument(
        "--input-dir",
        type=str,
        default=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "content", "5-youtube-transcripts",
        ),
        help="Directory containing channel/articles/ transcript files",
    )
    export_parser.add_argument(
        "--output-dir",
        type=str,
        default="youtube_batches/",
        help="Directory for batch output (default: youtube_batches/)",
    )
    export_parser.add_argument(
        "--processed-dir",
        type=str,
        default="youtube_processed/",
        help="Directory with already-processed outputs for resume (default: youtube_processed/)",
    )
    export_parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Transcripts per batch (default: 5)",
    )

    # Import subcommand
    import_parser = subparsers.add_parser(
        "import", help="Import Claude Code results into youtube_processed/"
    )
    import_parser.add_argument(
        "--input-dir",
        type=str,
        default="youtube_results/",
        help="Directory containing Claude Code result JSON files",
    )
    import_parser.add_argument(
        "--output-dir",
        type=str,
        default="youtube_processed/",
        help="Directory for processed output (default: youtube_processed/)",
    )

    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------

def run_export(args: argparse.Namespace):
    """Run the export workflow."""
    input_dir = args.input_dir
    output_dir = args.output_dir
    processed_dir = args.processed_dir
    batch_size = args.batch_size

    print(f"Discovering transcripts in {input_dir}...")
    all_transcripts = discover_transcripts(input_dir)
    print(f"Found {len(all_transcripts)} transcripts")

    # Filter already processed
    pending = filter_unprocessed(all_transcripts, processed_dir)
    print(f"{len(all_transcripts) - len(pending)} already processed, "
          f"{len(pending)} remaining")

    if not pending:
        print("Nothing to export!")
        return

    # Prepare each transcript
    prepared = []
    for ch, vid, fp in pending:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                content = f.read()
            prep = prepare_transcript_for_batch(content)
            if prep["text"].strip():
                prepared.append(prep)
        except Exception as e:
            print(f"  SKIP {ch}/{vid}: {e}")

    print(f"Prepared {len(prepared)} transcripts for batching")

    # Create batches
    batches = create_batches(prepared, batch_size=batch_size)
    print(f"Created {len(batches)} batches of up to {batch_size}")

    # Write to disk
    paths = export_batches(batches, output_dir)
    for p in paths:
        print(f"  Wrote {p}")

    # Token estimate
    total_words = sum(len(t["text"].split()) for t in prepared)
    est_input_tokens = int(total_words * 1.3)  # ~1.3 tokens/word
    est_output_tokens = len(prepared) * 1000  # ~1K tokens per transcript output
    print(f"\nEstimated tokens: ~{est_input_tokens:,} input + "
          f"~{est_output_tokens:,} output = ~{est_input_tokens + est_output_tokens:,} total")
    print(f"Estimated Haiku cost: ~${(est_input_tokens * 0.25 + est_output_tokens * 1.25) / 1_000_000:.2f}")


def run_import(args: argparse.Namespace):
    """Run the import workflow."""
    input_dir = args.input_dir
    output_dir = args.output_dir

    if not os.path.isdir(input_dir):
        print(f"Input directory not found: {input_dir}")
        sys.exit(1)

    # Find all result JSON files
    result_files = sorted([
        os.path.join(input_dir, f)
        for f in os.listdir(input_dir)
        if f.endswith(".json")
    ])
    print(f"Found {len(result_files)} result files in {input_dir}")

    total_imported = 0
    for rf in result_files:
        try:
            results = import_result_file(rf)
            paths = write_imported_results(results, output_dir)
            total_imported += len(paths)
            print(f"  Imported {len(paths)} videos from {os.path.basename(rf)}")
        except Exception as e:
            print(f"  ERROR importing {rf}: {e}")

    print(f"\nImported {total_imported} videos to {output_dir}")


def main():
    args = parse_args()
    if args.command == "export":
        run_export(args)
    elif args.command == "import":
        run_import(args)


if __name__ == "__main__":
    main()
