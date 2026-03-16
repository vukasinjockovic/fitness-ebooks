#!/usr/bin/env python3
"""
Scrape all Breaking Muscle posts via WP REST API.

Site: breakingmuscle.com
Estimated posts: ~13,972
Source tier: tier2
Note: Wordfence WAF blocks per_page>30, so we use per_page=30 with 3s delay.

Usage:
    python3 scrape_all.py
"""

import os
import sys
import time
import json

# Add category-1 dir to path for shared wp_scraper module
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "1-fitness-nutrition-science",
    ),
)
from wp_scraper import WPScraper, process_post

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

DOMAIN = "breakingmuscle.com"
BASE_URL = "https://breakingmuscle.com/wp-json/wp/v2/posts"
SOURCE_TIER = "tier2"
USER_AGENT = "Mozilla/5.0 (compatible; GymZilla/1.0)"
PER_PAGE = 30


class BreakingMuscleScraper(WPScraper):
    """Custom scraper for breakingmuscle.com with reduced page size.

    Wordfence WAF blocks per_page > 30, so we override page_url and
    the last-page detection logic.
    """

    def __init__(self, *args, per_page: int = 30, **kwargs):
        super().__init__(*args, **kwargs)
        self.per_page = per_page

    def page_url(self, page: int) -> str:
        sep = "&" if "?" in self.base_url else "?"
        return f"{self.base_url}{sep}per_page={self.per_page}&page={page}"

    def scrape_all(self):
        """Scrape all posts with custom per_page support."""
        os.makedirs(self.articles_dir, exist_ok=True)

        print(f"Scraping {self.domain}...")
        print(f"  Base URL: {self.base_url}")
        print(f"  Articles dir: {self.articles_dir}")
        print(f"  Per page: {self.per_page}")
        print()

        # Load taxonomy caches (uses per_page=100 internally but the
        # taxonomy endpoints are lightweight -- override if needed)
        print("Loading taxonomy caches...")
        self.load_categories()
        self.load_tags()
        self.load_authors()
        print()

        page = 1
        total_processed = 0

        while True:
            url = self.page_url(page)
            print(f"Fetching page {page}: {url}")

            posts = self.fetch_json(url)
            if not posts or not isinstance(posts, list) or len(posts) == 0:
                print(f"  No more posts (page {page}). Done.")
                break

            for post in posts:
                slug = post.get("slug", "")
                if not slug:
                    self.failed += 1
                    continue

                if self.already_exists(slug):
                    self.skipped += 1
                    total_processed += 1
                    continue

                try:
                    author_id = post.get("author", 0)
                    if author_id and author_id not in self.author_cache:
                        self.fetch_author(author_id)

                    article = process_post(
                        post=post,
                        domain=self.domain,
                        source_tier=self.source_tier,
                        author_cache=self.author_cache,
                        category_cache=self.category_cache,
                        tag_cache=self.tag_cache,
                    )
                    self.save_article(article)
                    self.scraped += 1
                except Exception as e:
                    print(f"  ERROR processing {slug}: {e}")
                    self.failed += 1

                total_processed += 1

                if total_processed % 50 == 0:
                    print(f"  Progress: {total_processed} processed "
                          f"({self.scraped} scraped, {self.skipped} skipped, "
                          f"{self.failed} failed)")

            if len(posts) < self.per_page:
                print(f"  Last page (got {len(posts)} posts).")
                break

            page += 1
            time.sleep(self.delay)

        print()
        print("=" * 60)
        print(f"SUMMARY for {self.domain}")
        print(f"  Scraped: {self.scraped}")
        print(f"  Skipped: {self.skipped}")
        print(f"  Failed:  {self.failed}")
        print(f"  Total:   {self.scraped + self.skipped + self.failed}")
        print("=" * 60)


def main():
    scraper = BreakingMuscleScraper(
        domain=DOMAIN,
        base_url=BASE_URL,
        source_tier=SOURCE_TIER,
        articles_dir=ARTICLES_DIR,
        user_agent=USER_AGENT,
        delay=3.0,
        per_page=PER_PAGE,
    )
    scraper.scrape_all()


if __name__ == "__main__":
    main()
