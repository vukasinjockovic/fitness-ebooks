#!/usr/bin/env python3
"""
Scrape theptdc.com (The PTDC) via WP REST API.

Site: theptdc.com
Estimated posts: 1,971
Source tier: tier2
Category: 10_coach_education
Cloudflare (passive, no challenge).

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

DOMAIN = "theptdc.com"
BASE_URL = "https://theptdc.com/wp-json/wp/v2/posts"
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
        delay=1.0,
    )
    scraper.scrape_all()


if __name__ == "__main__":
    main()
