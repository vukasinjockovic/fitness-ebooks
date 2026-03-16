#!/usr/bin/env python3
"""
Scrape nomeatathlete.com posts via WP REST API.

Site: nomeatathlete.com (vegan athletes)
Estimated posts: ~400
Source tier: tier1

Usage:
    python3 scrape_all.py
"""

import os
import sys

# Add shared wp_scraper module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "1-fitness-nutrition-science"))

from wp_scraper import WPScraper

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

DOMAIN = "nomeatathlete.com"
BASE_URL = "https://www.nomeatathlete.com/wp-json/wp/v2/posts"
SOURCE_TIER = "tier1"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


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


if __name__ == "__main__":
    main()
