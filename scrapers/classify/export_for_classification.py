#!/usr/bin/env python3
"""Export unclassified articles from lake.content to chunked JSON files.

Reads articles from PostgreSQL, strips YAML frontmatter, extracts a 300-word
excerpt, and writes chunked JSON files ready for LLM classification.

Usage:
    python3 export_for_classification.py                              # defaults
    python3 export_for_classification.py --chunk-size 500 --output-dir classify_chunks/
"""

import argparse
import json
import math
import os
import re
import sys
import time

import psycopg2
import psycopg2.extras


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5433,
    "dbname": "gymzillatribe_dev",
    "user": "app",
    "password": "phevasTAz7d2",
}

EXPORT_SQL = """\
SELECT id, title, body_markdown, source_domain, source_category, tags, word_count
FROM lake.content
WHERE classified = false
ORDER BY id
"""

COUNT_SQL = """\
SELECT COUNT(*) FROM lake.content WHERE classified = false
"""


# ---------------------------------------------------------------------------
# Pure functions (no side effects, fully testable)
# ---------------------------------------------------------------------------

def strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter (--- ... ---) from the beginning of text.

    Returns only the body content after the frontmatter block.
    """
    if not text:
        return text

    # Strip leading newlines
    stripped = text.lstrip("\n")

    if not stripped.startswith("---"):
        return text.strip()

    # Find the closing ---
    end = stripped.find("---", 3)
    if end == -1:
        return text.strip()

    body = stripped[end + 3:].strip()
    return body


def extract_excerpt(body: str, max_words: int = 300) -> str:
    """Extract the first max_words words from body text.

    Strips markdown headers before extracting. Returns empty string for
    empty/whitespace-only input.
    """
    if not body or not body.strip():
        return ""

    # Strip markdown header markers but keep their text
    lines = body.strip().split("\n")
    cleaned_lines = []
    for line in lines:
        s = line.strip()
        if s.startswith("#"):
            cleaned = re.sub(r"^#+\s*", "", s)
            if cleaned:
                cleaned_lines.append(cleaned)
        else:
            cleaned_lines.append(line)

    text = " ".join(cleaned_lines)
    text = re.sub(r"\s+", " ", text).strip()

    words = text.split()
    if len(words) <= max_words:
        return text

    return " ".join(words[:max_words])


def build_article_record(row: dict) -> dict:
    """Build an export record from a database row dict.

    Strips frontmatter from body_markdown and extracts a 300-word excerpt.
    """
    body_raw = row.get("body_markdown", "") or ""
    body_clean = strip_frontmatter(body_raw)
    excerpt = extract_excerpt(body_clean)

    tags = row.get("tags") or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            tags = []

    return {
        "id": row["id"],
        "title": row.get("title", ""),
        "excerpt": excerpt,
        "source_domain": row.get("source_domain", ""),
        "source_category": row.get("source_category", ""),
        "tags": tags,
        "word_count": row.get("word_count") or 0,
    }


def write_chunk_file(
    output_dir: str,
    chunk_id: int,
    total_chunks: int,
    articles: list[dict],
) -> str:
    """Write a chunk of articles to a JSON file.

    Creates the output directory if it doesn't exist.
    Returns the path to the written file.
    """
    os.makedirs(output_dir, exist_ok=True)

    filename = f"chunk_{chunk_id:03d}.json"
    filepath = os.path.join(output_dir, filename)

    data = {
        "chunk_id": chunk_id,
        "total_chunks": total_chunks,
        "articles": articles,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return filepath


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Export unclassified articles to chunked JSON files"
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500,
        help="Number of articles per chunk file (default: 500)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="classify_chunks/",
        help="Output directory for chunk files (default: classify_chunks/)",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Main (side effects: DB + filesystem)
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    chunk_size = args.chunk_size
    output_dir = args.output_dir

    print(f"Connecting to database {DB_CONFIG['dbname']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}...")
    conn = psycopg2.connect(**DB_CONFIG)

    try:
        # Get total count for progress reporting
        with conn.cursor() as cur:
            cur.execute(COUNT_SQL)
            total_articles = cur.fetchone()[0]

        if total_articles == 0:
            print("No unclassified articles found.")
            return

        total_chunks = math.ceil(total_articles / chunk_size)
        print(f"Found {total_articles:,} unclassified articles")
        print(f"Will create {total_chunks} chunk files of {chunk_size} articles each")
        print(f"Output directory: {output_dir}")
        print()

        # Use a server-side cursor for memory efficiency
        with conn.cursor(
            name="export_cursor",
            cursor_factory=psycopg2.extras.RealDictCursor,
        ) as cur:
            cur.execute(EXPORT_SQL)

            chunk_id = 0
            total_exported = 0
            start_time = time.time()

            while True:
                rows = cur.fetchmany(chunk_size)
                if not rows:
                    break

                chunk_id += 1
                articles = [build_article_record(dict(row)) for row in rows]

                filepath = write_chunk_file(
                    output_dir, chunk_id, total_chunks, articles
                )

                total_exported += len(articles)
                elapsed = time.time() - start_time
                rate = total_exported / elapsed if elapsed > 0 else 0

                print(
                    f"  Wrote {filepath} "
                    f"({len(articles)} articles, "
                    f"{total_exported:,}/{total_articles:,} total, "
                    f"{rate:.0f} articles/sec)"
                )

        elapsed = time.time() - start_time
        print()
        print(f"Export complete: {total_exported:,} articles in {chunk_id} chunks")
        print(f"Time: {elapsed:.1f}s ({total_exported / elapsed:.0f} articles/sec)")
        print(f"Output: {output_dir}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
