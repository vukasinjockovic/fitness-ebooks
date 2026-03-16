#!/usr/bin/env python3
"""
Scrape all Stronger By Science posts via WP REST API.

Site: strongerbyscience.com
Estimated posts: ~640
Source tier: tier1

Fetches posts paginated at 100/page, resolves categories/tags/authors,
converts HTML body to markdown, saves to articles/{slug}.md with YAML
frontmatter.

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

DOMAIN = "strongerbyscience.com"
BASE_URL = "https://www.strongerbyscience.com/wp-json/wp/v2/posts"
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
