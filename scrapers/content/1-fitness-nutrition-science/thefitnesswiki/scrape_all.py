#!/usr/bin/env python3
"""
Scrape all thefitness.wiki pages via WP REST API.

This is a community wiki (Reddit r/Fitness) hosted on WordPress.com.
All content is WP pages (not posts). Content type is how_to.

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
DOMAIN = "thefitness.wiki"
API_BASE = "https://thefitness.wiki/wp-json/wp/v2/pages"
PER_PAGE = 100
USER_AGENT = "Mozilla/5.0 (compatible; GymZilla/1.0)"
DELAY = 1  # seconds between API pages
REQUEST_TIMEOUT = 30
DEFAULT_AUTHOR = "Reddit r/Fitness Community"
SOURCE_TIER = "tier2"
CONTENT_TYPE = "how_to"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def decode_html(text: str) -> str:
    """Decode HTML entities in text."""
    return html.unescape(text)


def extract_tags(post: dict) -> list[str]:
    """Extract tag names from _embedded wp:term (usually empty for pages)."""
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
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


def word_count(text: str) -> int:
    """Count words in text."""
    return len(text.split())


def output_filename(post: dict) -> str:
    """Generate output filename for a page."""
    return f"{post['slug']}.md"


def should_skip(post: dict, articles_dir: str) -> bool:
    """Check if article already exists (resume support)."""
    fname = output_filename(post)
    return os.path.exists(os.path.join(articles_dir, fname))


def build_frontmatter(post: dict) -> str:
    """Build YAML frontmatter for a page."""
    slug = post["slug"]
    title = decode_html(post["title"]["rendered"])
    title = title.replace('"', '\\"')
    tags = extract_tags(post)
    image_url = extract_image_url(post)
    body_md = content_to_markdown(post["content"]["rendered"])
    wc = word_count(body_md)

    tags_str = json.dumps(tags)
    image_line = f'"{image_url}"' if image_url else "null"

    return f"""---
source_id: "{slug}"
source_domain: "{DOMAIN}"
source_url: "{post['link']}"
title: "{title}"
author: "{DEFAULT_AUTHOR}"
date_published: "{post['date']}"
tags: {tags_str}
content_type: "{CONTENT_TYPE}"
source_tier: "{SOURCE_TIER}"
word_count: {wc}
image_url: {image_line}
---"""


def build_article(post: dict) -> str:
    """Build complete article with frontmatter and markdown body."""
    fm = build_frontmatter(post)
    body = content_to_markdown(post["content"]["rendered"])
    return f"{fm}\n\n{body}\n"


# ---------------------------------------------------------------------------
# HTTP / API
# ---------------------------------------------------------------------------

def fetch_page(page: int) -> tuple[list[dict], int, int]:
    """
    Fetch a single page from the WP REST API.
    Returns (pages, total_items, total_pages).
    """
    url = f"{API_BASE}?per_page={PER_PAGE}&page={page}&_embed"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", USER_AGENT)

    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        total_items = int(resp.headers.get("X-WP-Total", 0))
        total_pages = int(resp.headers.get("X-WP-TotalPages", 0))
        data = json.loads(resp.read().decode("utf-8"))
        return data, total_items, total_pages


def fetch_with_retry(page: int, max_retries: int = 1) -> tuple[list[dict], int, int] | None:
    """Fetch a page with retry on 429/5xx errors."""
    for attempt in range(max_retries + 1):
        try:
            return fetch_page(page)
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    os.makedirs(ARTICLES_DIR, exist_ok=True)

    print(f"thefitness.wiki Scraper")
    print(f"Output: {ARTICLES_DIR}")
    print(f"\n--- Scraping Pages ---")
    print(f"API: {API_BASE}")

    stats = {"total": 0, "saved": 0, "skipped": 0, "errors": 0}

    # Page 1 to get totals
    result = fetch_with_retry(1)
    if result is None:
        print("No content found.")
        return

    pages, total_items, total_pages = result
    print(f"  Total: {total_items} pages across {total_pages} API pages")
    stats["total"] = total_items

    # Process page 1
    for pg in pages:
        try:
            if should_skip(pg, ARTICLES_DIR):
                stats["skipped"] += 1
                continue
            article = build_article(pg)
            fname = output_filename(pg)
            with open(os.path.join(ARTICLES_DIR, fname), "w", encoding="utf-8") as f:
                f.write(article)
            stats["saved"] += 1
        except Exception as e:
            print(f"  [ERROR] {pg.get('slug', '?')}: {e}")
            stats["errors"] += 1

    print(f"  API page 1/{total_pages}: {len(pages)} items")

    # Remaining pages
    for api_page in range(2, total_pages + 1):
        time.sleep(DELAY)
        result = fetch_with_retry(api_page)
        if result is None:
            break
        pages, _, _ = result
        if not pages:
            break

        for pg in pages:
            try:
                if should_skip(pg, ARTICLES_DIR):
                    stats["skipped"] += 1
                    continue
                article = build_article(pg)
                fname = output_filename(pg)
                with open(os.path.join(ARTICLES_DIR, fname), "w", encoding="utf-8") as f:
                    f.write(article)
                stats["saved"] += 1
            except Exception as e:
                print(f"  [ERROR] {pg.get('slug', '?')}: {e}")
                stats["errors"] += 1

        print(f"  API page {api_page}/{total_pages}: {len(pages)} items")

    # Summary
    print(f"\n=== Summary ===")
    print(f"Total: {stats['total']} wiki pages")
    print(f"Saved: {stats['saved']}")
    print(f"Skipped: {stats['skipped']} (already existed)")
    print(f"Errors: {stats['errors']}")
    print(f"Articles dir: {ARTICLES_DIR}")


if __name__ == "__main__":
    main()
