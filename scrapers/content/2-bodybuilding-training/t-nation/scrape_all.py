#!/usr/bin/env python3
"""
Scrape all T Nation archive posts via WP REST API.

Site: archive.t-nation.com
Estimated posts: ~5,981
Source tier: tier1 (25+ years of expert content)
nginx, minimal anti-bot.

Usage:
    python3 scrape_all.py
"""

import os
import sys

# Add category-1 dir to path for shared wp_scraper module
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

DOMAIN = "archive.t-nation.com"
BASE_URL = "https://archive.t-nation.com/wp-json/wp/v2/posts"
SOURCE_TIER = "tier1"
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


if __name__ == "__main__":
    main()
