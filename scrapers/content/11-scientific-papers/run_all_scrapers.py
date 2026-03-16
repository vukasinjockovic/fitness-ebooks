#!/usr/bin/env python3
"""
Master runner script for all scientific paper scrapers.

Executes PubMed bulk abstract searches across all target queries,
then does journal-specific searches for key journals.

Usage:
    python3 run_all_scrapers.py              # Run everything
    python3 run_all_scrapers.py --pubmed     # PubMed only
    python3 run_all_scrapers.py --journals   # Journals only
    python3 run_all_scrapers.py --pmc        # PMC full text only
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PUBMED_SCRAPER = os.path.join(SCRIPT_DIR, "pubmed", "scrape_pubmed.py")

# Journal configurations: (name, output_subdir, pubmed_query, max_results)
JOURNAL_CONFIGS = [
    (
        "Frontiers in Nutrition",
        os.path.join(SCRIPT_DIR, "frontiers-nutrition", "articles"),
        '"Front Nutr"[journal]',
        15000,
    ),
    (
        "Nutrients (MDPI)",
        os.path.join(SCRIPT_DIR, "nutrients-journal", "articles"),
        '"Nutrients"[journal] AND ("exercise" OR "muscle" OR "protein" OR "sport" OR "body composition" OR "resistance training" OR "weight loss" OR "obesity" OR "diet" OR "supplementation")',
        10000,
    ),
    (
        "BMC Sports Science",
        os.path.join(SCRIPT_DIR, "bmc-sports-science", "articles"),
        '"BMC Sports Sci Med Rehabil"[journal]',
        5000,
    ),
    (
        "Sports Medicine - Open",
        os.path.join(SCRIPT_DIR, "sports-medicine-open", "articles"),
        '"Sports Med Open"[journal]',
        5000,
    ),
    (
        "British Journal of Sports Medicine",
        os.path.join(SCRIPT_DIR, "pubmed", "articles"),
        '"Br J Sports Med"[journal] AND ("nutrition" OR "diet" OR "exercise" OR "training" OR "performance")',
        5000,
    ),
    (
        "Medicine & Science in Sports & Exercise",
        os.path.join(SCRIPT_DIR, "pubmed", "articles"),
        '"Med Sci Sports Exerc"[journal] AND ("nutrition" OR "protein" OR "diet" OR "supplement" OR "body composition")',
        5000,
    ),
    (
        "Journal of Strength and Conditioning Research",
        os.path.join(SCRIPT_DIR, "pubmed", "articles"),
        '"J Strength Cond Res"[journal] AND ("nutrition" OR "protein" OR "diet" OR "supplement" OR "body composition")',
        5000,
    ),
]


def run_pubmed_bulk():
    """Run the main PubMed bulk abstract scraper with all default queries."""
    print(f"\n{'#'*70}")
    print(f"# PUBMED BULK ABSTRACT SCRAPER")
    print(f"{'#'*70}\n")

    cmd = [
        sys.executable, PUBMED_SCRAPER,
        "--all",
        "--max-results", "10000",
    ]
    print(f"Running: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, cwd=SCRIPT_DIR)
    return result.returncode


def run_journal_scraper(name, output_dir, query, max_results):
    """Run PubMed scraper for a specific journal."""
    print(f"\n{'#'*70}")
    print(f"# JOURNAL: {name}")
    print(f"{'#'*70}\n")

    os.makedirs(output_dir, exist_ok=True)
    cmd = [
        sys.executable, PUBMED_SCRAPER,
        "--query", query,
        "--max-results", str(max_results),
        "--output-dir", output_dir,
    ]
    print(f"Running: {' '.join(cmd[:4])} ...\n")
    result = subprocess.run(cmd, cwd=SCRIPT_DIR)
    return result.returncode


def run_journals():
    """Run all journal-specific scrapers."""
    for name, output_dir, query, max_results in JOURNAL_CONFIGS:
        run_journal_scraper(name, output_dir, query, max_results)


def count_all_articles():
    """Count all articles across all directories."""
    dirs = {
        "pubmed": os.path.join(SCRIPT_DIR, "pubmed", "articles"),
        "jissn": os.path.join(SCRIPT_DIR, "jissn", "articles"),
        "frontiers-nutrition": os.path.join(SCRIPT_DIR, "frontiers-nutrition", "articles"),
        "nutrients-journal": os.path.join(SCRIPT_DIR, "nutrients-journal", "articles"),
        "bmc-sports-science": os.path.join(SCRIPT_DIR, "bmc-sports-science", "articles"),
        "sports-medicine-open": os.path.join(SCRIPT_DIR, "sports-medicine-open", "articles"),
        "pubmed-central": os.path.join(SCRIPT_DIR, "pubmed-central", "articles"),
    }

    total = 0
    print(f"\n{'='*70}")
    print("ARTICLE COUNTS")
    print(f"{'='*70}")
    for name, path in dirs.items():
        if os.path.exists(path):
            count = len([f for f in os.listdir(path) if f.endswith(".md")])
        else:
            count = 0
        total += count
        print(f"  {name:30s}: {count:>6,d} articles")
    print(f"  {'TOTAL':30s}: {total:>6,d} articles")
    return total


def main():
    parser = argparse.ArgumentParser(description="Run all scientific paper scrapers")
    parser.add_argument("--pubmed", action="store_true", help="Run PubMed bulk only")
    parser.add_argument("--journals", action="store_true", help="Run journal-specific only")
    parser.add_argument("--count", action="store_true", help="Just count articles")
    args = parser.parse_args()

    start = time.time()
    print(f"Scientific Papers Scraper Suite")
    print(f"Started: {datetime.now().isoformat()}")

    if args.count:
        count_all_articles()
        return

    if args.pubmed or (not args.journals):
        run_pubmed_bulk()

    if args.journals or (not args.pubmed):
        run_journals()

    elapsed = time.time() - start
    print(f"\n\nAll scrapers complete. Total time: {elapsed:.0f}s ({elapsed/60:.1f}m)")
    count_all_articles()


if __name__ == "__main__":
    main()
