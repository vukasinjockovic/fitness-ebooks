#!/usr/bin/env python3
"""
Scrape femalehealthawareness.org via WP REST API.

116 posts, bilingual EN/ES, fully open WP REST API.
source_tier: tier2, source_category: 9_womens_health
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..",
                                "content", "1-fitness-nutrition-science"))

from wp_scraper import WPScraper

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")


def main():
    scraper = WPScraper(
        domain="femalehealthawareness.org",
        base_url="https://femalehealthawareness.org/wp-json/wp/v2/posts?_fields=id,title,content,excerpt,slug,link,date,categories,tags,author,yoast_head_json,_embedded&_embed",
        source_tier="tier2",
        articles_dir=ARTICLES_DIR,
        user_agent="Mozilla/5.0 (compatible; GymZilla/1.0)",
        delay=1.0,
    )
    scraper.scrape_all()


if __name__ == "__main__":
    main()
