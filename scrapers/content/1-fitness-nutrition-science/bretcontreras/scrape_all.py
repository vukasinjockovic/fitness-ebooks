#!/usr/bin/env python3
"""
Scrape all Bret Contreras posts via WP REST API.

Site: bretcontreras.com
Estimated posts: ~991
Source tier: tier2

Has Yoast JSON-LD in yoast_head_json field -- datePublished/dateModified
are extracted automatically by the shared wp_scraper module.

Usage:
    python3 scrape_all.py
"""

import os
import sys

# Add parent dir to path for shared module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wp_scraper import WPScraper

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

DOMAIN = "bretcontreras.com"
BASE_URL = "https://bretcontreras.com/wp-json/wp/v2/posts"
SOURCE_TIER = "tier2"
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
