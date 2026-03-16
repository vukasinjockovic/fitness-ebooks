#!/usr/bin/env python3
"""Import LLM-classified data back into lake.content.

Reads classified JSON files and batch-updates the database with audiences,
context_tags, category, subcategory, expertise_level, and classified=true.

Usage:
    python3 import_classifications.py                          # defaults
    python3 import_classifications.py --input-dir classified_chunks/
    python3 import_classifications.py --dry-run
"""

import argparse
import glob
import json
import os
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

UPDATE_SQL = """\
UPDATE lake.content SET
    audiences = %s::jsonb,
    context_tags = %s::jsonb,
    category = %s,
    subcategory = %s,
    expertise_level = %s,
    classified = true,
    classified_at = NOW()
WHERE id = %s
"""


# ---------------------------------------------------------------------------
# Pure functions (no side effects, fully testable)
# ---------------------------------------------------------------------------

def load_classified_file(filepath: str) -> list[dict]:
    """Load a classified JSON file and return the list of classifications.

    Returns empty list on any parse error.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  WARNING: Could not parse {filepath}: {e}")
        return []

    if not isinstance(data, list):
        print(f"  WARNING: {filepath} does not contain a JSON array")
        return []

    return data


def validate_classification(entry: dict) -> bool:
    """Validate that a classification entry has the required fields.

    Required: id (integer), category, subcategory, expertise_level.
    Optional (defaulted): audiences, context_tags.
    """
    # Must have an id
    if "id" not in entry:
        return False

    # id must be an integer
    if not isinstance(entry["id"], int):
        return False

    # Must have category
    if "category" not in entry or not entry["category"]:
        return False

    # subcategory and expertise_level should exist
    if "subcategory" not in entry or not entry["subcategory"]:
        return False
    if "expertise_level" not in entry or not entry["expertise_level"]:
        return False

    return True


def build_update_params(entry: dict) -> tuple:
    """Build a parameter tuple for the UPDATE SQL statement.

    Returns (audiences_json, context_tags_json, category, subcategory,
             expertise_level, id).
    """
    audiences = entry.get("audiences", []) or []
    context_tags = entry.get("context_tags", []) or []

    return (
        json.dumps(audiences),
        json.dumps(context_tags),
        entry["category"],
        entry["subcategory"],
        entry["expertise_level"],
        entry["id"],
    )


def find_classified_files(input_dir: str) -> list[str]:
    """Find all *_classified.json files in the input directory, sorted.

    Returns full paths sorted alphabetically.
    """
    pattern = os.path.join(input_dir, "*_classified.json")
    files = sorted(glob.glob(pattern))
    return files


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Import LLM classifications back into lake.content"
    )
    parser.add_argument(
        "--input-dir",
        type=str,
        default="classified_chunks/",
        help="Directory containing classified JSON files (default: classified_chunks/)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Number of UPDATEs per commit batch (default: 1000)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate files but don't update the database",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Main (side effects: DB reads/writes)
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    input_dir = args.input_dir
    batch_size = args.batch_size
    dry_run = args.dry_run

    # Find classified files
    files = find_classified_files(input_dir)
    if not files:
        print(f"No classified files found in {input_dir}")
        return

    print(f"Found {len(files)} classified chunk files in {input_dir}")
    if dry_run:
        print("DRY RUN: will validate but not update the database")
    print()

    # Collect all valid classifications
    all_entries = []
    skipped = 0
    for filepath in files:
        basename = os.path.basename(filepath)
        entries = load_classified_file(filepath)
        valid = []
        for entry in entries:
            if validate_classification(entry):
                valid.append(entry)
            else:
                skipped += 1
        all_entries.extend(valid)
        print(f"  {basename}: {len(valid)} valid, {len(entries) - len(valid)} invalid")

    print()
    print(f"Total: {len(all_entries):,} valid classifications, {skipped:,} skipped")

    if dry_run:
        print("DRY RUN complete. No database changes made.")
        return

    if not all_entries:
        print("No valid classifications to import.")
        return

    # Connect and batch-update
    print(f"\nConnecting to database {DB_CONFIG['dbname']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}...")
    conn = psycopg2.connect(**DB_CONFIG)

    try:
        start_time = time.time()
        total_updated = 0
        batch = []

        for entry in all_entries:
            params = build_update_params(entry)
            batch.append(params)

            if len(batch) >= batch_size:
                with conn.cursor() as cur:
                    cur.executemany(UPDATE_SQL, batch)
                conn.commit()
                total_updated += len(batch)
                batch = []

                if total_updated % 10000 < batch_size:
                    elapsed = time.time() - start_time
                    rate = total_updated / elapsed if elapsed > 0 else 0
                    print(
                        f"  Progress: {total_updated:,}/{len(all_entries):,} "
                        f"({rate:.0f} rows/sec)"
                    )

        # Flush remaining batch
        if batch:
            with conn.cursor() as cur:
                cur.executemany(UPDATE_SQL, batch)
            conn.commit()
            total_updated += len(batch)

        elapsed = time.time() - start_time
        print()
        print(f"Import complete: {total_updated:,} rows updated")
        print(f"Time: {elapsed:.1f}s ({total_updated / elapsed:.0f} rows/sec)")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
