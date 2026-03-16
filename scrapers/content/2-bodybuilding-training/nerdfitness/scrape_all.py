#!/usr/bin/env python3
"""
Scrape all Nerd Fitness posts via WP REST API.

Site: nerdfitness.com
Estimated posts: ~1,037
Source tier: tier2
WP Engine + Cloudflare, 10-second crawl-delay in robots.txt.

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

DOMAIN = "nerdfitness.com"
BASE_URL = "https://www.nerdfitness.com/wp-json/wp/v2/posts"
SOURCE_TIER = "tier2"
USER_AGENT = "Mozilla/5.0 (compatible; GymZilla/1.0)"


def main():
    scraper = WPScraper(
        domain=DOMAIN,
        base_url=BASE_URL,
        source_tier=SOURCE_TIER,
        articles_dir=ARTICLES_DIR,
        user_agent=USER_AGENT,
        delay=10.0,
    )
    scraper.scrape_all()


if __name__ == "__main__":
    main()
