#!/usr/bin/env python3
"""
Scrape legionathletics.com via WP REST API.

3,863 total posts, filter to ~1,500 relevant (Supplements, Nutrition,
Building Muscle, Definitive Guides, Fitness Science, Training, General Health).
Skip: Podcast, Success Stories, Cool Stuff.

Uses shared wp_scraper module.
"""

import os
import sys

# Add parent paths so we can import the shared module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "1-fitness-nutrition-science"))

from wp_scraper import WPScraper

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

# Categories to SKIP (by name patterns)
SKIP_CATEGORIES = {
    "podcast", "podcasts", "success stories", "cool stuff",
    "recipes", "great books", "tools", "motivation",
}


def main():
    scraper = WPScraper(
        domain="legionathletics.com",
        base_url="https://legionathletics.com/wp-json/wp/v2/posts?_fields=id,title,content,excerpt,slug,link,date,categories,tags,author,yoast_head_json,_embedded&_embed",
        source_tier="tier2",
        articles_dir=ARTICLES_DIR,
        user_agent="Mozilla/5.0 (compatible; GymZilla/1.0)",
        delay=1.0,
    )

    os.makedirs(ARTICLES_DIR, exist_ok=True)

    print(f"Scraping {scraper.domain}...")
    print(f"  Articles dir: {ARTICLES_DIR}")
    print()

    # Load taxonomy caches
    print("Loading taxonomy caches...")
    scraper.load_categories()
    scraper.load_tags()
    scraper.load_authors()
    print()

    # Build set of category IDs to skip
    skip_cat_ids = set()
    for cat_id, cat_name in scraper.category_cache.items():
        if cat_name.lower() in SKIP_CATEGORIES:
            skip_cat_ids.add(cat_id)
            print(f"  Will skip category: {cat_name} (ID {cat_id})")

    print()

    page = 1
    total_processed = 0

    while True:
        url = scraper.page_url(page)
        print(f"Fetching page {page}: {url}")

        posts = scraper.fetch_json(url)
        if not posts or not isinstance(posts, list) or len(posts) == 0:
            print(f"  No more posts (page {page}). Done.")
            break

        for post in posts:
            slug = post.get("slug", "")
            if not slug:
                scraper.failed += 1
                continue

            # Filter: skip posts in unwanted categories
            post_cats = set(post.get("categories", []))
            if post_cats and post_cats.issubset(skip_cat_ids):
                total_processed += 1
                continue

            if scraper.already_exists(slug):
                scraper.skipped += 1
                total_processed += 1
                continue

            # Check if content is empty/too short
            body_html = post.get("content", {}).get("rendered", "")
            if len(body_html) < 200:
                total_processed += 1
                continue

            try:
                author_id = post.get("author", 0)
                if author_id and author_id not in scraper.author_cache:
                    scraper.fetch_author(author_id)

                from wp_scraper import process_post
                article = process_post(
                    post=post,
                    domain=scraper.domain,
                    source_tier=scraper.source_tier,
                    author_cache=scraper.author_cache,
                    category_cache=scraper.category_cache,
                    tag_cache=scraper.tag_cache,
                )
                # Override content_type based on categories
                article["content_type"] = "supplement_review"

                # Skip very short articles (< 100 words)
                if article["word_count"] < 100:
                    total_processed += 1
                    continue

                scraper.save_article(article)
                scraper.scraped += 1
            except Exception as e:
                print(f"  ERROR processing {slug}: {e}")
                scraper.failed += 1

            total_processed += 1

            if total_processed % 100 == 0:
                print(f"  Progress: {total_processed} processed "
                      f"({scraper.scraped} scraped, {scraper.skipped} skipped, "
                      f"{scraper.failed} failed)")

        if len(posts) < 100:
            print(f"  Last page (got {len(posts)} posts).")
            break

        page += 1
        import time
        time.sleep(scraper.delay)

    print()
    print("=" * 60)
    print(f"SUMMARY for {scraper.domain}")
    print(f"  Scraped: {scraper.scraped}")
    print(f"  Skipped (existing): {scraper.skipped}")
    print(f"  Failed:  {scraper.failed}")
    print(f"  Total processed: {total_processed}")
    print("=" * 60)


if __name__ == "__main__":
    main()
