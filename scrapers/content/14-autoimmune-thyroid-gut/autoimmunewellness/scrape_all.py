#!/usr/bin/env python3
"""
Scrape all Autoimmune Wellness posts via WP REST API.

Site: autoimmunewellness.com
Estimated posts: ~1,013
Source tier: tier2

NOTE: This site has aggressive Cloudflare rate-limiting that blocks
taxonomy pre-loading. We use _embed to get taxonomy data inline
and skip the separate taxonomy fetch, then use a custom scrape loop
with per_page=10 to stay under limits.

Usage:
    python3 scrape_all.py
"""

import html
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "1-fitness-nutrition-science"))
from wp_scraper import WPScraper, process_post

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

DOMAIN = "autoimmunewellness.com"
BASE_URL = "https://autoimmunewellness.com/wp-json/wp/v2/posts"
SOURCE_TIER = "tier2"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
PER_PAGE = 10
DELAY = 2.0


def main():
    scraper = WPScraper(
        domain=DOMAIN,
        base_url=BASE_URL,
        source_tier=SOURCE_TIER,
        articles_dir=ARTICLES_DIR,
        user_agent=USER_AGENT,
        delay=DELAY,
        per_page=PER_PAGE,
    )
    os.makedirs(ARTICLES_DIR, exist_ok=True)

    print(f"Scraping {DOMAIN} (custom loop, _embed, per_page={PER_PAGE})...")
    print(f"  Articles dir: {ARTICLES_DIR}")
    print()

    # Skip taxonomy pre-loading -- Cloudflare rate-limits it.
    # Instead, use ?_embed to get categories/tags/author inline.
    page = 1
    scraped = 0
    skipped = 0
    failed = 0

    while True:
        url = f"{BASE_URL}?per_page={PER_PAGE}&page={page}&_embed"
        print(f"Fetching page {page}: {url}")

        posts = scraper.fetch_json(url)
        if not posts or not isinstance(posts, list) or len(posts) == 0:
            print(f"  No more posts (page {page}). Done.")
            break

        for post in posts:
            slug = post.get("slug", "")
            if not slug:
                failed += 1
                continue

            if scraper.already_exists(slug):
                skipped += 1
                continue

            try:
                # Extract embedded taxonomy data into caches so process_post works
                embedded = post.get("_embedded", {})

                # Author
                author_id = post.get("author", 0)
                if author_id and author_id not in scraper.author_cache:
                    wp_authors = embedded.get("author", [])
                    if wp_authors and isinstance(wp_authors[0], dict):
                        scraper.author_cache[author_id] = html.unescape(
                            wp_authors[0].get("name", "Unknown"))

                # Categories
                wp_terms = embedded.get("wp:term", [])
                if wp_terms:
                    for term_group in wp_terms:
                        if isinstance(term_group, list):
                            for term in term_group:
                                tid = term.get("id", 0)
                                taxonomy = term.get("taxonomy", "")
                                name = html.unescape(term.get("name", ""))
                                if taxonomy == "category" and tid not in scraper.category_cache:
                                    scraper.category_cache[tid] = name
                                elif taxonomy == "post_tag" and tid not in scraper.tag_cache:
                                    scraper.tag_cache[tid] = name

                article = process_post(
                    post=post,
                    domain=DOMAIN,
                    source_tier=SOURCE_TIER,
                    author_cache=scraper.author_cache,
                    category_cache=scraper.category_cache,
                    tag_cache=scraper.tag_cache,
                )
                scraper.save_article(article)
                scraped += 1
            except Exception as e:
                print(f"  ERROR processing {slug}: {e}")
                failed += 1

            total = scraped + skipped + failed
            if total % 50 == 0:
                print(f"  Progress: {total} processed "
                      f"({scraped} scraped, {skipped} skipped, {failed} failed)")

        if len(posts) < PER_PAGE:
            print(f"  Last page (got {len(posts)} posts).")
            break

        page += 1
        time.sleep(DELAY)

    print()
    print("=" * 60)
    print(f"SUMMARY for {DOMAIN}")
    print(f"  Scraped: {scraped}")
    print(f"  Skipped: {skipped}")
    print(f"  Failed:  {failed}")
    print(f"  Total:   {scraped + skipped + failed}")
    print("=" * 60)


if __name__ == "__main__":
    main()
