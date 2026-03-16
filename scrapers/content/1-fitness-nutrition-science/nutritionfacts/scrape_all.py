#!/usr/bin/env python3
"""
Scrape nutritionfacts.org content via WP REST API.

Fetches posts, videos, audio, questions, topics, and recipes.
Saves each as a markdown file with YAML frontmatter to articles/.

Uses only stdlib (urllib, json, os, time, html, re) + markdownify.
Resume-safe: skips files that already exist.

Usage:
    python3 scrape_all.py
"""

import html as html_module
import json
import os
import re
import time
import urllib.error
import urllib.request

from markdownify import markdownify as md

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

BASE_URL = "https://nutritionfacts.org/wp-json/wp/v2"
SOURCE_DOMAIN = "nutritionfacts.org"
SOURCE_TIER = "tier2"
AUTHOR_NAME = "Michael Greger M.D. FACLM"

USER_AGENT = "GymZilla-ContentScraper/1.0 (fitness-books project)"
PER_PAGE = 100
PAGE_DELAY = 1.0  # seconds between API page requests
REQUEST_TIMEOUT = 30
PROGRESS_EVERY = 50

# Post types to scrape
POST_TYPES = ["posts", "video", "audio", "questions", "topics", "recipe"]

# Map post type -> content_type for frontmatter
CONTENT_TYPE_MAP = {
    "posts": "science",
    "video": "transcript",
    "audio": "transcript",
    "questions": "q_and_a",
    "topics": "science",
    "recipe": "recipe_related",
}


# ---------------------------------------------------------------------------
# Helper functions (testable, no side effects)
# ---------------------------------------------------------------------------

def build_api_url(post_type: str, page: int = 1) -> str:
    """Build WP REST API URL for a given post type and page."""
    return f"{BASE_URL}/{post_type}?per_page={PER_PAGE}&page={page}"


def html_to_markdown(html_content: str | None) -> str:
    """Convert HTML string to markdown. Returns empty string for None/empty."""
    if not html_content:
        return ""
    return md(html_content, strip=["img"]).strip()


def count_words(text: str) -> int:
    """Count words in a text string."""
    if not text:
        return 0
    return len(text.split())


def clean_title(raw_title: str) -> str:
    """Decode HTML entities in title string."""
    return html_module.unescape(raw_title)


def extract_tags(post: dict) -> list[str]:
    """Extract tag names from WP API post with _embedded terms."""
    try:
        term_lists = post["_embedded"]["wp:term"]
        tags = []
        for term_list in term_lists:
            for term in term_list:
                if "name" in term:
                    tags.append(term["name"])
        return tags
    except (KeyError, TypeError):
        return []


def extract_featured_image(post: dict) -> str | None:
    """Extract featured image URL from embedded media."""
    try:
        media = post["_embedded"]["wp:featuredmedia"]
        if media and len(media) > 0:
            return media[0].get("source_url")
    except (KeyError, TypeError, IndexError):
        pass
    return None


def build_post_url(post: dict) -> str:
    """Get canonical URL from post data."""
    return post.get("link", "")


def make_filename(slug: str, post_type: str, existing_filenames: set) -> str:
    """
    Generate filename for a post. Uses bare slug if no collision,
    otherwise prefixes with post type.
    """
    bare = f"{slug}.md"
    if bare not in existing_filenames:
        return bare
    return f"{post_type}-{slug}.md"


def already_scraped(filename: str, articles_dir: str) -> bool:
    """Check if a file already exists (for resume support)."""
    return os.path.isfile(os.path.join(articles_dir, filename))


def build_frontmatter(
    slug: str,
    url: str,
    title: str,
    date_published: str,
    tags: list[str],
    content_type: str,
    word_count: int,
    image_url: str | None,
) -> str:
    """Build YAML frontmatter string for a markdown article."""
    tags_str = json.dumps(tags)
    image_str = f"\"{image_url}\"" if image_url else "null"

    return (
        f"---\n"
        f"source_id: \"{slug}\"\n"
        f"source_domain: \"{SOURCE_DOMAIN}\"\n"
        f"source_url: \"{url}\"\n"
        f"title: \"{title}\"\n"
        f"author: \"{AUTHOR_NAME}\"\n"
        f"date_published: \"{date_published}\"\n"
        f"tags: {tags_str}\n"
        f"content_type: \"{content_type}\"\n"
        f"source_tier: \"{SOURCE_TIER}\"\n"
        f"word_count: {word_count}\n"
        f"image_url: {image_str}\n"
        f"---\n"
    )


# ---------------------------------------------------------------------------
# HTTP fetch with retry
# ---------------------------------------------------------------------------

def fetch_api_page(url: str) -> tuple[list[dict], int, int]:
    """
    Fetch one page from the WP REST API.

    Returns (items, total_items, total_pages).
    Retries once on 429/5xx. Raises on other errors.
    """
    for attempt in range(2):
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", USER_AGENT)
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                total_items = int(resp.headers.get("X-WP-Total", 0))
                total_pages = int(resp.headers.get("X-WP-TotalPages", 0))
                data = json.loads(resp.read().decode("utf-8"))
                return data, total_items, total_pages
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return [], 0, 0
            if e.code in (429, 500, 502, 503, 504) and attempt == 0:
                print(f"  HTTP {e.code} on {url}, retrying in 5s...")
                time.sleep(5)
                continue
            raise
        except urllib.error.URLError as e:
            if attempt == 0:
                print(f"  URL error on {url}: {e}, retrying in 5s...")
                time.sleep(5)
                continue
            raise
    return [], 0, 0


# ---------------------------------------------------------------------------
# Main scraping logic
# ---------------------------------------------------------------------------

def scrape_post_type(post_type: str, existing_filenames: set) -> dict:
    """
    Scrape all items of a given post type.

    Returns stats dict: {scraped, skipped, failed}.
    """
    content_type = CONTENT_TYPE_MAP[post_type]
    stats = {"scraped": 0, "skipped": 0, "failed": 0}

    print(f"\n{'='*60}")
    print(f"Scraping post type: {post_type}")
    print(f"{'='*60}")

    # Fetch first page to get totals
    # Use _embed to get tags and featured media in one request
    page = 1
    first_url = build_api_url(post_type, page)
    items, total_items, total_pages = fetch_api_page(first_url)

    if total_items == 0:
        print(f"  No items found for {post_type}")
        return stats

    print(f"  Total items: {total_items}, Total pages: {total_pages}")

    all_items = items
    processed = 0

    # Fetch remaining pages
    while page < total_pages:
        page += 1
        url = build_api_url(post_type, page)
        time.sleep(PAGE_DELAY)
        try:
            page_items, _, _ = fetch_api_page(url)
            all_items.extend(page_items)
        except Exception as e:
            print(f"  ERROR fetching page {page}: {e}")
            stats["failed"] += PER_PAGE  # approximate
            continue

    # Process all items
    for item in all_items:
        processed += 1
        if processed % PROGRESS_EVERY == 0:
            print(f"  Progress: {processed}/{len(all_items)} items processed")

        try:
            slug = item.get("slug", "")
            if not slug:
                stats["failed"] += 1
                continue

            filename = make_filename(slug, post_type, existing_filenames)

            # Resume: skip if already exists
            if already_scraped(filename, ARTICLES_DIR):
                stats["skipped"] += 1
                continue

            # Extract data
            title_raw = item.get("title", {}).get("rendered", "")
            title = clean_title(title_raw)
            body_html = item.get("content", {}).get("rendered", "")
            body_md = html_to_markdown(body_html)
            date_pub = item.get("date", "")
            link = build_post_url(item)
            tags = extract_tags(item)
            image_url = extract_featured_image(item)
            wc = count_words(body_md)

            # Build frontmatter + body
            frontmatter = build_frontmatter(
                slug=slug,
                url=link,
                title=title,
                date_published=date_pub,
                tags=tags,
                content_type=content_type,
                word_count=wc,
                image_url=image_url,
            )

            full_content = frontmatter + "\n" + body_md + "\n"

            # Write file
            filepath = os.path.join(ARTICLES_DIR, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(full_content)

            existing_filenames.add(filename)
            stats["scraped"] += 1

        except Exception as e:
            print(f"  ERROR processing item: {e}")
            stats["failed"] += 1

    return stats


def main():
    """Main entry point: scrape all post types."""
    os.makedirs(ARTICLES_DIR, exist_ok=True)

    # Track existing filenames for collision detection
    existing_filenames = set(os.listdir(ARTICLES_DIR))

    total_stats = {"scraped": 0, "skipped": 0, "failed": 0}

    print(f"NutritionFacts.org WP API Scraper")
    print(f"Articles directory: {ARTICLES_DIR}")
    print(f"Existing files: {len(existing_filenames)}")

    for post_type in POST_TYPES:
        stats = scrape_post_type(post_type, existing_filenames)
        for key in total_stats:
            total_stats[key] += stats[key]

    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"  Total scraped:  {total_stats['scraped']}")
    print(f"  Total skipped:  {total_stats['skipped']} (already existed)")
    print(f"  Total failed:   {total_stats['failed']}")
    print(f"  Files in dir:   {len(os.listdir(ARTICLES_DIR))}")


if __name__ == "__main__":
    main()
