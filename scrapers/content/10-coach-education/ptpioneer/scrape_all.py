#!/usr/bin/env python3
"""
Scrape ptpioneer.com (PT Pioneer) via WP REST API.

Site: ptpioneer.com
Estimated posts: 776
Source tier: tier2
Category: 10_coach_education
Wordfence active -- throttled to 1-2 req/sec (delay=2.0).

Usage:
    python3 scrape_all.py
"""

import os
import sys

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "1-fitness-nutrition-science",
    ),
)
from wp_scraper import WPScraper

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

DOMAIN = "ptpioneer.com"
BASE_URL = "https://www.ptpioneer.com/wp-json/wp/v2/posts"
SOURCE_TIER = "tier2"
SOURCE_CATEGORY = "10_coach_education"
USER_AGENT = "Mozilla/5.0 (compatible; GymZilla/1.0)"


def main():
    scraper = WPScraper(
        domain=DOMAIN,
        base_url=BASE_URL,
        source_tier=SOURCE_TIER,
        articles_dir=ARTICLES_DIR,
        user_agent=USER_AGENT,
        delay=2.0,  # Wordfence -- slower to avoid rate limits
    )
    scraper.scrape_all()


if __name__ == "__main__":
    main()
