#!/usr/bin/env python3
"""
Scrape chrismasterjohnphd.com via WP REST API.

Site: chrismasterjohnphd.com
Estimated posts: 900
Source tier: tier1
Category: 1_fitness_nutrition_science
WP REST API fully open. Multiple content types (Q&A, blog, lite videos, podcast).
Also scrapes substantive pages (COVID research updates, guides).

Usage:
    python3 scrape_all.py
"""

import os
import sys

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
    ),
)
from wp_scraper import WPScraper

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

DOMAIN = "chrismasterjohnphd.com"
BASE_URL = "https://chrismasterjohnphd.com/wp-json/wp/v2/posts"
SOURCE_TIER = "tier1"
SOURCE_CATEGORY = "1_fitness_nutrition_science"
USER_AGENT = "Mozilla/5.0 (compatible; GymZilla/1.0)"


def main():
    scraper = WPScraper(
        domain=DOMAIN,
        base_url=BASE_URL,
        source_tier=SOURCE_TIER,
        articles_dir=ARTICLES_DIR,
        user_agent=USER_AGENT,
        delay=1.0,
    )
    scraper.scrape_all()

    # Also scrape pages (38 substantive ones per probe)
    print()
    print("=" * 60)
    print("Now scraping WP pages...")
    print("=" * 60)

    page_scraper = WPScraper(
        domain=DOMAIN,
        base_url="https://chrismasterjohnphd.com/wp-json/wp/v2/pages",
        source_tier=SOURCE_TIER,
        articles_dir=ARTICLES_DIR,
        user_agent=USER_AGENT,
        delay=1.0,
    )
    page_scraper.scrape_all()


if __name__ == "__main__":
    main()
