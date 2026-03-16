#!/usr/bin/env python3
"""
Scrape all Lift Vault posts via WP REST API.

Site: liftvault.com
Estimated posts: ~966
Source tier: tier2

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

DOMAIN = "liftvault.com"
BASE_URL = "https://liftvault.com/wp-json/wp/v2/posts"
SOURCE_TIER = "tier2"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Tor SOCKS5 proxy to bypass IP rate-limiting.
# Ports 10300-10399 are configured in /etc/tor/torrc (debian-tor service).
TOR_PROXY = ("127.0.0.1", 10300)


def main():
    scraper = WPScraper(
        domain=DOMAIN,
        base_url=BASE_URL,
        source_tier=SOURCE_TIER,
        articles_dir=ARTICLES_DIR,
        user_agent=USER_AGENT,
        delay=1.5,
        socks_proxy=TOR_PROXY,
    )
    scraper.scrape_all()


if __name__ == "__main__":
    main()
