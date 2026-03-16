#!/usr/bin/env python3
"""Classify articles using an OpenAI-compatible LLM API.

Reads chunked JSON files produced by export_for_classification.py, sends
sub-batches to the LLM for classification, and writes classified output files.

Provider-agnostic: works with Groq, Together, OpenRouter, or any
OpenAI-compatible API.

Usage:
    # With Groq
    CLASSIFY_API_BASE=https://api.groq.com/openai/v1 \\
    CLASSIFY_API_KEY=gsk_xxx \\
    CLASSIFY_MODEL=llama-3.3-70b-versatile \\
    python3 classify_with_llm.py --input-dir classify_chunks/ --output-dir classified_chunks/

    # With Together
    CLASSIFY_API_BASE=https://api.together.xyz/v1 \\
    CLASSIFY_API_KEY=xxx \\
    CLASSIFY_MODEL=meta-llama/Llama-3.3-70B-Instruct-Turbo \\
    python3 classify_with_llm.py --input-dir classify_chunks/ --output-dir classified_chunks/
"""

import argparse
import json
import os
import re
import sys
import time
from typing import Iterator


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "CLASSIFY-PROMPT.md",
)


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
    - Returns empty list on parse failure
    """
    text = response_text.strip()

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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Classify articles using an OpenAI-compatible LLM API"
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
        "--delay",
        type=float,
        default=0.5,
        help="Delay in seconds between LLM calls (default: 0.5)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retries per LLM call on failure (default: 3)",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# LLM API interaction (side effects)
# ---------------------------------------------------------------------------

def call_llm(
    client,
    model: str,
    prompt: str,
    max_retries: int = 3,
) -> str:
    """Call the LLM API with retry and exponential backoff.

    Returns the response text content.
    """
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a content classification assistant. "
                            "You always respond with valid JSON arrays. "
                            "No explanations, just the JSON."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=16384,
            )
            return response.choices[0].message.content
        except Exception as e:
            wait = 2 ** attempt
            print(f"    LLM call failed (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                print(f"    Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


# ---------------------------------------------------------------------------
# Main (side effects: filesystem + API)
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    # Validate environment
    api_base = os.environ.get("CLASSIFY_API_BASE")
    api_key = os.environ.get("CLASSIFY_API_KEY")
    model = os.environ.get("CLASSIFY_MODEL")

    if not api_base or not api_key or not model:
        print("ERROR: Required environment variables:")
        print("  CLASSIFY_API_BASE  - API endpoint URL")
        print("  CLASSIFY_API_KEY   - API key")
        print("  CLASSIFY_MODEL     - Model name")
        sys.exit(1)

    # Find input chunk files
    import glob

    pattern = os.path.join(args.input_dir, "chunk_*.json")
    chunk_files = sorted(glob.glob(pattern))
    # Exclude already-classified files from input
    chunk_files = [f for f in chunk_files if "_classified" not in f]

    if not chunk_files:
        print(f"No chunk files found in {args.input_dir}")
        return

    print(f"Found {len(chunk_files)} chunk files in {args.input_dir}")
    print(f"Model: {model}")
    print(f"API: {api_base}")
    print(f"Batch size: {args.batch_size} articles per LLM call")
    print(f"Delay: {args.delay}s between calls")
    print()

    # Initialize OpenAI client (lazy import so pure functions are testable
    # without the openai package installed)
    from openai import OpenAI

    client = OpenAI(
        base_url=api_base,
        api_key=api_key,
    )

    os.makedirs(args.output_dir, exist_ok=True)

    total_classified = 0
    total_failed = 0
    start_time = time.time()

    for chunk_path in chunk_files:
        chunk_basename = os.path.basename(chunk_path)

        # Resume support: skip already-classified chunks
        if is_chunk_already_classified(chunk_basename, args.output_dir):
            print(f"  SKIP {chunk_basename} (already classified)")
            continue

        print(f"  Processing {chunk_basename}...")

        chunk_data = load_chunk_file(chunk_path)
        articles = chunk_data.get("articles", [])

        if not articles:
            print(f"    No articles in chunk, skipping")
            continue

        # Classify in sub-batches
        all_classifications = []

        for batch_idx, batch in enumerate(
            sub_batch_articles(articles, args.batch_size)
        ):
            batch_num = batch_idx + 1
            print(f"    Sub-batch {batch_num} ({len(batch)} articles)...")

            prompt = build_llm_prompt(batch)

            try:
                response_text = call_llm(
                    client, model, prompt, max_retries=args.max_retries
                )
                classifications = parse_llm_response(response_text)

                if not classifications:
                    print(f"    WARNING: Empty response for sub-batch {batch_num}")
                    total_failed += len(batch)
                else:
                    all_classifications.extend(classifications)
                    print(f"    Got {len(classifications)} classifications")

            except Exception as e:
                print(f"    ERROR: Sub-batch {batch_num} failed after retries: {e}")
                total_failed += len(batch)

            # Rate limiting
            if args.delay > 0:
                time.sleep(args.delay)

        # Write output for this chunk
        if all_classifications:
            outpath = write_classified_output(
                args.output_dir, chunk_basename, all_classifications
            )
            total_classified += len(all_classifications)
            print(f"    Wrote {outpath} ({len(all_classifications)} classifications)")
        else:
            print(f"    WARNING: No classifications for {chunk_basename}")

    elapsed = time.time() - start_time
    print()
    print(f"Classification complete:")
    print(f"  Classified: {total_classified:,}")
    print(f"  Failed: {total_failed:,}")
    print(f"  Time: {elapsed:.1f}s")
    if total_classified > 0:
        print(f"  Rate: {total_classified / elapsed:.0f} articles/sec")


if __name__ == "__main__":
    main()
