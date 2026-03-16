#!/usr/bin/env python3
"""Import scraped markdown content (with YAML frontmatter) into lake.content.

Walks the scrapers/content/ directory tree, parses YAML frontmatter + markdown
body from each article file, and upserts rows into the PostgreSQL lake.content
table.

Usage:
    python3 import_content_to_lake.py                              # import all
    python3 import_content_to_lake.py --category 1-fitness-nutrition-science
    python3 import_content_to_lake.py --site strongerbyscience
    python3 import_content_to_lake.py --dry-run
    python3 import_content_to_lake.py --force
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time

import psycopg2
from psycopg2.extras import execute_values, Json
import yaml


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

CONTENT_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "content")

BATCH_SIZE = 100

# Map folder names to database source_category values
CATEGORY_MAP = {
    "1-fitness-nutrition-science": "1_fitness_nutrition_science",
    "2-bodybuilding-training": "2_bodybuilding_training",
    "3-nutrition-meal-planning": "3_nutrition_meal_planning",
    "5-youtube-transcripts": "5_youtube_transcripts",
    "8-supplement-reviews": "8_supplement_reviews",
    "9-womens-health": "9_womens_health",
    "10-coach-education": "10_coach_education",
    "11-scientific-papers": "11_scientific_papers",
    "12-podcast-transcripts": "12_podcast_transcripts",
}


# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------

# Columns we insert into lake.content
INSERT_COLUMNS = [
    "source_id", "source_domain", "source_url", "source_category",
    "title", "author", "body_markdown", "summary", "word_count",
    "content_type", "source_tier",
    "date_published", "tags", "image_url",
    "duration_seconds", "channel", "transcript_type",
    "scrape_source", "content_hash",
]

_cols = ", ".join(INSERT_COLUMNS)
_update_set = ", ".join(
    f"{col} = EXCLUDED.{col}" for col in INSERT_COLUMNS
    if col not in ("scrape_source", "source_id")
)

UPSERT_SQL = f"""INSERT INTO lake.content ({_cols})
VALUES %s
ON CONFLICT (scrape_source, source_id) DO UPDATE SET
    {_update_set},
    updated_at = NOW()
"""

UPSERT_SQL_SKIP = f"""INSERT INTO lake.content ({_cols})
VALUES %s
ON CONFLICT (scrape_source, source_id) DO NOTHING
"""


# ---------------------------------------------------------------------------
# Pure functions (no side effects, fully testable)
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter and body from a markdown string.

    Expected format:
        ---
        key: value
        ---

        Markdown body here...

    Returns (metadata_dict, body_string).
    """
    # Match content between --- delimiters
    # The frontmatter must start at the beginning of the text
    text = text.lstrip("\n")
    if not text.startswith("---"):
        return {}, text

    # Find the closing ---
    end = text.find("---", 3)
    if end == -1:
        return {}, text

    yaml_text = text[3:end]
    body = text[end + 3:].strip()

    meta = yaml.safe_load(yaml_text)
    if meta is None:
        meta = {}

    return meta, body


def compute_content_hash(body: str) -> str:
    """Compute SHA256 hash of normalized body text.

    Normalization: lowercase, collapse whitespace, strip leading/trailing.
    """
    normalized = body.lower().strip()
    # Collapse all whitespace sequences to single space
    normalized = re.sub(r"\s+", " ", normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def generate_summary(body: str) -> str:
    """Generate a summary from the first 200 words of the body.

    Strips markdown headers (lines starting with #) before extracting words.
    """
    if not body or not body.strip():
        return ""

    # Strip markdown header lines
    lines = body.strip().split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            # Remove the # prefix but keep the text
            cleaned = re.sub(r"^#+\s*", "", stripped)
            if cleaned:
                cleaned_lines.append(cleaned)
        else:
            cleaned_lines.append(line)

    text = " ".join(cleaned_lines)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    words = text.split()
    if len(words) <= 200:
        return text

    return " ".join(words[:200])


def compute_word_count(body: str) -> int:
    """Count words in markdown body, ignoring markdown syntax characters."""
    if not body or not body.strip():
        return 0

    # Remove markdown header markers
    text = re.sub(r"^#+\s*", "", body, flags=re.MULTILINE)
    # Remove bullet markers
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        return 0
    return len(text.split())


def normalize_scrape_source(source_domain: str) -> str:
    """Derive scrape_source from source_domain.

    Examples:
        strongerbyscience.com -> strongerbyscience
        www.strongerbyscience.com -> strongerbyscience
        youtube.com -> youtube
        blog.example.co.uk -> blog-example
        born-fitness.com -> born-fitness
    """
    domain = source_domain.lower().strip()

    # Remove www. prefix
    if domain.startswith("www."):
        domain = domain[4:]

    # Known TLDs to strip (order matters: check multi-part first)
    tld_suffixes = [".co.uk", ".com.au", ".co.nz", ".org.uk", ".com", ".org", ".net", ".io", ".gov", ".edu"]
    for suffix in tld_suffixes:
        if domain.endswith(suffix):
            domain = domain[: -len(suffix)]
            break

    # Replace dots with hyphens for subdomains
    domain = domain.replace(".", "-")

    return domain


def derive_source_category(folder_name: str) -> str:
    """Map a category folder name to its database source_category value.

    Uses CATEGORY_MAP for known categories, falls back to replacing hyphens
    with underscores for unknown categories.
    """
    if folder_name in CATEGORY_MAP:
        return CATEGORY_MAP[folder_name]
    return folder_name.replace("-", "_")


def build_row(meta: dict, body: str, source_category: str) -> dict:
    """Build a database row dict from parsed frontmatter and body.

    Computes derived fields: content_hash, summary, word_count (if missing),
    scrape_source.
    """
    # Use frontmatter word_count if provided, otherwise compute from body
    word_count = meta.get("word_count")
    if word_count is None:
        word_count = compute_word_count(body)

    # Tags: ensure it's a list
    tags = meta.get("tags")
    if tags is None:
        tags = []
    elif not isinstance(tags, list):
        tags = [tags]

    return {
        "source_id": meta.get("source_id", ""),
        "source_domain": meta.get("source_domain", ""),
        "source_url": meta.get("source_url"),
        "source_category": source_category,
        "title": meta.get("title", ""),
        "author": meta.get("author"),
        "body_markdown": body,
        "summary": generate_summary(body),
        "word_count": word_count,
        "content_type": meta.get("content_type") or None,
        "source_tier": meta.get("source_tier") or "tier2",
        "date_published": meta.get("date_published") or None,
        "tags": tags,
        "image_url": meta.get("image_url") or None,
        "duration_seconds": meta.get("duration_seconds") or None,
        "channel": meta.get("channel") or None,
        "transcript_type": meta.get("transcript_type") or None,
        "scrape_source": normalize_scrape_source(meta.get("source_domain", "")),
        "content_hash": compute_content_hash(body),
    }


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def find_content_files(
    base_dir: str,
    category: str | None = None,
    site: str | None = None,
) -> list[dict]:
    """Walk the content directory tree and find all article markdown files.

    Directory structure expected:
        base_dir/{category_folder}/{site_folder}/articles/{slug}.md

    Returns a list of dicts with keys:
        filepath, category_folder, source_category, site
    """
    results = []

    for category_folder in sorted(os.listdir(base_dir)):
        cat_path = os.path.join(base_dir, category_folder)
        if not os.path.isdir(cat_path):
            continue

        # Filter by category if specified
        if category and category_folder != category:
            continue

        source_category = derive_source_category(category_folder)

        for site_folder in sorted(os.listdir(cat_path)):
            site_path = os.path.join(cat_path, site_folder)
            if not os.path.isdir(site_path):
                continue

            # Filter by site if specified
            if site and site_folder != site:
                continue

            articles_dir = os.path.join(site_path, "articles")
            if not os.path.isdir(articles_dir):
                continue

            for fname in sorted(os.listdir(articles_dir)):
                if not fname.endswith(".md"):
                    continue
                filepath = os.path.join(articles_dir, fname)
                results.append({
                    "filepath": filepath,
                    "category_folder": category_folder,
                    "source_category": source_category,
                    "site": site_folder,
                })

    return results


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Import scraped markdown content into lake.content"
    )
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help="Import only files from this category folder (e.g. 1-fitness-nutrition-science)",
    )
    parser.add_argument(
        "--site",
        type=str,
        default=None,
        help="Import only files from this site folder (e.g. strongerbyscience)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Count files and show what would be imported, without inserting",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Force re-import: update existing rows instead of skipping",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

def row_to_tuple(row: dict) -> tuple:
    """Convert a row dict to a tuple matching INSERT_COLUMNS order."""
    return tuple(
        Json(row[col]) if col == "tags" else row[col]
        for col in INSERT_COLUMNS
    )


def import_batch(conn, batch: list[dict], force: bool) -> int:
    """Insert a batch of rows into lake.content.

    Returns number of rows affected.
    """
    if not batch:
        return 0

    sql = UPSERT_SQL if force else UPSERT_SQL_SKIP
    tuples = [row_to_tuple(row) for row in batch]

    with conn.cursor() as cur:
        execute_values(cur, sql, tuples, page_size=BATCH_SIZE)
        affected = cur.rowcount
    conn.commit()
    return affected


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    # Discover files
    base_dir = CONTENT_BASE
    if not os.path.isdir(base_dir):
        print(f"ERROR: Content directory not found: {base_dir}")
        sys.exit(1)

    files = find_content_files(base_dir, category=args.category, site=args.site)

    if not files:
        print("No article files found.")
        sys.exit(0)

    print(f"Found {len(files)} article files")

    # Group by category/site for summary
    from collections import Counter
    by_category = Counter(f["category_folder"] for f in files)
    by_site = Counter(f["site"] for f in files)
    print(f"  Categories: {len(by_category)}")
    for cat, count in sorted(by_category.items()):
        print(f"    {cat}: {count}")
    print(f"  Sites: {len(by_site)}")
    for site_name, count in sorted(by_site.items()):
        print(f"    {site_name}: {count}")

    if args.dry_run:
        print("\n[DRY RUN] No database changes made.")
        return

    # Connect to database
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False

    start = time.time()
    total_inserted = 0
    total_updated = 0
    total_skipped = 0
    total_failed = 0
    batch = []

    for i, file_info in enumerate(files):
        try:
            with open(file_info["filepath"], "r", encoding="utf-8") as f:
                text = f.read()

            meta, body = parse_frontmatter(text)
            if not meta.get("source_id") or not meta.get("title"):
                total_skipped += 1
                continue

            row = build_row(meta, body, file_info["source_category"])
            batch.append(row)

        except Exception as e:
            total_failed += 1
            if total_failed <= 10:
                print(f"  ERROR parsing {file_info['filepath']}: {e}")
            continue

        # Flush batch
        if len(batch) >= BATCH_SIZE:
            affected = import_batch(conn, batch, args.force)
            if args.force:
                total_updated += affected
            else:
                total_inserted += affected
                total_skipped += len(batch) - affected
            batch = []

            processed = i + 1
            if processed % 100 == 0:
                print(f"  [{processed}/{len(files)}] processed...")

    # Flush remaining batch
    if batch:
        affected = import_batch(conn, batch, args.force)
        if args.force:
            total_updated += affected
        else:
            total_inserted += affected
            total_skipped += len(batch) - affected

    elapsed = time.time() - start

    # Print summary
    print(f"\n{'='*50}")
    print(f"Import complete in {elapsed:.1f}s")
    print(f"  Total files found:  {len(files)}")
    if args.force:
        print(f"  Inserted/Updated:   {total_updated}")
    else:
        print(f"  Inserted:           {total_inserted}")
    print(f"  Skipped:            {total_skipped}")
    print(f"  Failed:             {total_failed}")

    # Show database counts
    with conn.cursor() as cur:
        cur.execute(
            "SELECT scrape_source, COUNT(*) FROM lake.content "
            "GROUP BY scrape_source ORDER BY count DESC"
        )
        rows = cur.fetchall()

    if rows:
        print(f"\nlake.content by source:")
        total_db = 0
        for source, count in rows:
            print(f"  {source:30s} {count:>8,}")
            total_db += count
        print(f"  {'TOTAL':30s} {total_db:>8,}")

    conn.close()


if __name__ == "__main__":
    main()
