#!/usr/bin/env python3
"""
Scrape all Born Fitness articles via WP REST API.

Fetches posts and podcast episodes from bornfitness.com,
converts HTML to markdown, and saves with frontmatter.

Usage:
    python3 scrape_all.py
"""

import html
import json
import os
import re
import time
import urllib.error
import urllib.request

from markdownify import markdownify as md

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DOMAIN = "bornfitness.com"
POSTS_API = "https://www.bornfitness.com/wp-json/wp/v2/posts"
PODCAST_API = "https://www.bornfitness.com/wp-json/wp/v2/podcast-episode"
PER_PAGE = 100
USER_AGENT = "Mozilla/5.0 (compatible; GymZilla/1.0)"
DELAY = 1  # seconds between API pages
REQUEST_TIMEOUT = 30
DEFAULT_AUTHOR = "Adam Bornstein"
SOURCE_TIER = "tier2"
CONTENT_TYPE = "science"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def decode_html(text: str) -> str:
    """Decode HTML entities in text."""
    return html.unescape(text)


def extract_author(post: dict) -> str:
    """Extract author name from _embedded or fall back to default."""
    try:
        return post["_embedded"]["author"][0]["name"]
    except (KeyError, IndexError, TypeError):
        return DEFAULT_AUTHOR


def extract_tags(post: dict) -> list[str]:
    """Extract tag names from _embedded wp:term."""
    tags = []
    try:
        terms = post["_embedded"]["wp:term"]
        for term_group in terms:
            for term in term_group:
                if isinstance(term, dict) and "name" in term:
                    tags.append(term["name"])
    except (KeyError, IndexError, TypeError):
        pass
    return tags


def extract_image_url(post: dict) -> str | None:
    """Extract featured image URL from _embedded."""
    try:
        return post["_embedded"]["wp:featuredmedia"][0]["source_url"]
    except (KeyError, IndexError, TypeError):
        return None


def content_to_markdown(html_content: str) -> str:
    """Convert HTML content to markdown."""
    result = md(html_content, strip=["img", "script", "style"])
    # Clean up excessive whitespace
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


def word_count(text: str) -> int:
    """Count words in text."""
    return len(text.split())


def output_filename(post: dict, is_podcast: bool = False) -> str:
    """Generate output filename for a post."""
    slug = post["slug"]
    if is_podcast:
        return f"podcast-{slug}.md"
    return f"{slug}.md"


def should_skip(post: dict, articles_dir: str, is_podcast: bool = False) -> bool:
    """Check if article already exists (resume support)."""
    fname = output_filename(post, is_podcast)
    return os.path.exists(os.path.join(articles_dir, fname))


def build_frontmatter(post: dict, is_podcast: bool = False) -> str:
    """Build YAML frontmatter for a post."""
    slug = post["slug"]
    source_id = f"podcast-{slug}" if is_podcast else slug
    title = decode_html(post["title"]["rendered"])
    # Escape quotes in title
    title = title.replace('"', '\\"')
    author = extract_author(post)
    tags = extract_tags(post)
    image_url = extract_image_url(post)
    body_md = content_to_markdown(post["content"]["rendered"])
    wc = word_count(body_md)

    tags_str = json.dumps(tags)
    image_line = f'"{image_url}"' if image_url else "null"

    return f"""---
source_id: "{source_id}"
source_domain: "{DOMAIN}"
source_url: "{post['link']}"
title: "{title}"
author: "{author}"
date_published: "{post['date']}"
tags: {tags_str}
content_type: "{CONTENT_TYPE}"
source_tier: "{SOURCE_TIER}"
word_count: {wc}
image_url: {image_line}
---"""


def build_article(post: dict, is_podcast: bool = False) -> str:
    """Build complete article with frontmatter and markdown body."""
    fm = build_frontmatter(post, is_podcast)
    body = content_to_markdown(post["content"]["rendered"])
    return f"{fm}\n\n{body}\n"


# ---------------------------------------------------------------------------
# HTTP / API
# ---------------------------------------------------------------------------

def fetch_page(api_url: str, page: int) -> tuple[list[dict], int, int]:
    """
    Fetch a single page from the WP REST API.
    Returns (posts, total_items, total_pages).
    """
    url = f"{api_url}?per_page={PER_PAGE}&page={page}&_embed"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", USER_AGENT)

    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        total_items = int(resp.headers.get("X-WP-Total", 0))
        total_pages = int(resp.headers.get("X-WP-TotalPages", 0))
        data = json.loads(resp.read().decode("utf-8"))
        return data, total_items, total_pages


def fetch_with_retry(api_url: str, page: int, max_retries: int = 1) -> tuple[list[dict], int, int] | None:
    """Fetch a page with retry on 429/5xx errors."""
    for attempt in range(max_retries + 1):
        try:
            return fetch_page(api_url, page)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"  [SKIP] Page {page} returned 404")
                return None
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries:
                wait = 5 * (attempt + 1)
                print(f"  [RETRY] Page {page} returned {e.code}, waiting {wait}s...")
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < max_retries:
                wait = 5 * (attempt + 1)
                print(f"  [RETRY] Page {page} error: {e}, waiting {wait}s...")
                time.sleep(wait)
                continue
            raise


def scrape_endpoint(api_url: str, label: str, is_podcast: bool = False) -> dict:
    """
    Scrape all pages from a WP REST API endpoint.
    Returns stats dict.
    """
    stats = {"total": 0, "saved": 0, "skipped": 0, "errors": 0}

    print(f"\n--- Scraping {label} ---")
    print(f"API: {api_url}")

    # Page 1 to get totals
    result = fetch_with_retry(api_url, 1)
    if result is None:
        print(f"  No content found at {api_url}")
        return stats

    posts, total_items, total_pages = result
    print(f"  Total: {total_items} items across {total_pages} pages")
    stats["total"] = total_items

    # Process page 1
    for post in posts:
        try:
            if should_skip(post, ARTICLES_DIR, is_podcast):
                stats["skipped"] += 1
                continue
            article = build_article(post, is_podcast)
            fname = output_filename(post, is_podcast)
            with open(os.path.join(ARTICLES_DIR, fname), "w", encoding="utf-8") as f:
                f.write(article)
            stats["saved"] += 1
        except Exception as e:
            print(f"  [ERROR] {post.get('slug', '?')}: {e}")
            stats["errors"] += 1

    print(f"  Page 1/{total_pages}: {len(posts)} items")

    # Remaining pages
    for page in range(2, total_pages + 1):
        time.sleep(DELAY)
        result = fetch_with_retry(api_url, page)
        if result is None:
            break
        posts, _, _ = result
        if not posts:
            break

        for post in posts:
            try:
                if should_skip(post, ARTICLES_DIR, is_podcast):
                    stats["skipped"] += 1
                    continue
                article = build_article(post, is_podcast)
                fname = output_filename(post, is_podcast)
                with open(os.path.join(ARTICLES_DIR, fname), "w", encoding="utf-8") as f:
                    f.write(article)
                stats["saved"] += 1
            except Exception as e:
                print(f"  [ERROR] {post.get('slug', '?')}: {e}")
                stats["errors"] += 1

        print(f"  Page {page}/{total_pages}: {len(posts)} items")

    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    os.makedirs(ARTICLES_DIR, exist_ok=True)

    print(f"Born Fitness Scraper")
    print(f"Output: {ARTICLES_DIR}")

    # Scrape posts
    post_stats = scrape_endpoint(POSTS_API, "Posts", is_podcast=False)

    # Scrape podcast episodes
    podcast_stats = scrape_endpoint(PODCAST_API, "Podcast Episodes", is_podcast=True)

    # Summary
    total_saved = post_stats["saved"] + podcast_stats["saved"]
    total_skipped = post_stats["skipped"] + podcast_stats["skipped"]
    total_errors = post_stats["errors"] + podcast_stats["errors"]

    print(f"\n=== Summary ===")
    print(f"Posts:    {post_stats['total']} total, {post_stats['saved']} saved, {post_stats['skipped']} skipped, {post_stats['errors']} errors")
    print(f"Podcasts: {podcast_stats['total']} total, {podcast_stats['saved']} saved, {podcast_stats['skipped']} skipped, {podcast_stats['errors']} errors")
    print(f"Combined: {total_saved} saved, {total_skipped} skipped, {total_errors} errors")
    print(f"Articles dir: {ARTICLES_DIR}")


if __name__ == "__main__":
    main()
