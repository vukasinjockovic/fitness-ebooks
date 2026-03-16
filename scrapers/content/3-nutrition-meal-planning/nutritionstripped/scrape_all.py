#!/usr/bin/env python3
"""
Scrape nutritionstripped.com content via WP REST API.

WordPress site by McKel (Hill) Kooienga, MS, RD with Yoast SEO Premium.
WP REST API is fully open, no auth required.

Endpoint: https://nutritionstripped.com/wp-json/wp/v2/posts?per_page=100&page=N
Total: 801 posts (288 articles + 481 recipes + misc)

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

BASE_URL = "https://nutritionstripped.com/wp-json/wp/v2"
SOURCE_DOMAIN = "nutritionstripped.com"
SOURCE_TIER = "tier2"
SOURCE_CATEGORY = "3_nutrition_meal_planning"
DEFAULT_AUTHOR = "McKel Kooienga, MS, RD"

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
PER_PAGE = 100
PAGE_DELAY = 1.5
REQUEST_TIMEOUT = 30
PROGRESS_EVERY = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_api_url(page: int = 1) -> str:
    return f"{BASE_URL}/posts?per_page={PER_PAGE}&page={page}&_embed"


def html_to_markdown(html_content: str | None) -> str:
    if not html_content:
        return ""
    return md(html_content, heading_style="ATX", strip=["img"]).strip()


def count_words(text: str) -> int:
    if not text:
        return 0
    return len(text.split())


def clean_title(raw_title: str) -> str:
    return html_module.unescape(raw_title)


def _escape_yaml(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _extract_date(raw: str) -> str:
    if not raw:
        return ""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return match.group(1) if match else raw


def extract_tags(post: dict) -> list[str]:
    try:
        term_lists = post["_embedded"]["wp:term"]
        tags = []
        for term_list in term_lists:
            for term in term_list:
                if "name" in term:
                    tags.append(html_module.unescape(term["name"]))
        return tags
    except (KeyError, TypeError):
        return []


def extract_author(post: dict) -> str:
    try:
        authors = post["_embedded"]["author"]
        if authors and len(authors) > 0:
            return authors[0].get("name", DEFAULT_AUTHOR)
    except (KeyError, TypeError, IndexError):
        pass
    return DEFAULT_AUTHOR


def extract_featured_image(post: dict) -> str | None:
    try:
        media = post["_embedded"]["wp:featuredmedia"]
        if media and len(media) > 0:
            return media[0].get("source_url")
    except (KeyError, TypeError, IndexError):
        pass
    return None


def extract_yoast_meta(post: dict) -> dict:
    """Extract SEO metadata from Yoast head in API response."""
    meta = {"description": "", "schema_type": ""}
    try:
        yoast = post.get("yoast_head_json", {})
        if yoast:
            meta["description"] = yoast.get("description", "")
            schema = yoast.get("schema", {})
            graph = schema.get("@graph", [])
            for item in graph:
                if item.get("@type") in ("Article", "BlogPosting", "WebPage"):
                    meta["schema_type"] = item.get("@type", "")
                    break
    except (AttributeError, TypeError):
        pass
    return meta


def already_scraped(filename: str) -> bool:
    return os.path.isfile(os.path.join(ARTICLES_DIR, filename))


def build_frontmatter(slug: str, url: str, title: str, author: str,
                       date_published: str, date_modified: str,
                       tags: list[str], word_count: int,
                       image_url: str | None, description: str = "") -> str:
    lines = ["---"]
    lines.append(f'source_id: "{slug}"')
    lines.append(f'source_domain: "{SOURCE_DOMAIN}"')
    lines.append(f'source_url: "{_escape_yaml(url)}"')
    lines.append(f'title: "{_escape_yaml(title)}"')
    lines.append(f'author: "{_escape_yaml(author)}"')
    lines.append(f'date_published: "{date_published}"')
    if date_modified:
        lines.append(f'date_modified: "{date_modified}"')
    if description:
        lines.append(f'description: "{_escape_yaml(description)}"')
    if tags:
        tag_list = ", ".join(f'"{_escape_yaml(t)}"' for t in tags)
        lines.append(f"tags: [{tag_list}]")
    lines.append(f'content_type: "article"')
    lines.append(f'source_tier: "{SOURCE_TIER}"')
    lines.append(f'source_category: "{SOURCE_CATEGORY}"')
    lines.append(f"word_count: {word_count}")
    if image_url:
        lines.append(f'image_url: "{image_url}"')
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------

def fetch_api_page(url: str) -> tuple[list[dict], int, int]:
    for attempt in range(3):
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", USER_AGENT)
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                total_items = int(resp.headers.get("X-WP-Total", 0))
                total_pages = int(resp.headers.get("X-WP-TotalPages", 0))
                data = json.loads(resp.read().decode("utf-8"))
                return data, total_items, total_pages
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return [], 0, 0
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                wait = 5 * (attempt + 1)
                print(f"  HTTP {e.code}, retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise
        except urllib.error.URLError as e:
            if attempt < 2:
                print(f"  URL error: {e}, retrying in 5s...")
                time.sleep(5)
                continue
            raise
    return [], 0, 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    os.makedirs(ARTICLES_DIR, exist_ok=True)

    existing = set(os.listdir(ARTICLES_DIR))
    stats = {"scraped": 0, "skipped": 0, "failed": 0}

    print("NutritionStripped WP API Scraper")
    print(f"Articles directory: {ARTICLES_DIR}")
    print(f"Existing files: {len(existing)}")

    # Fetch first page
    page = 1
    url = build_api_url(page)
    items, total_items, total_pages = fetch_api_page(url)

    if total_items == 0:
        print("No items found!")
        return

    print(f"Total posts: {total_items}, Pages: {total_pages}")

    all_items = list(items)

    # Fetch remaining pages
    while page < total_pages:
        page += 1
        time.sleep(PAGE_DELAY)
        url = build_api_url(page)
        try:
            page_items, _, _ = fetch_api_page(url)
            all_items.extend(page_items)
            print(f"  Fetched page {page}/{total_pages} ({len(all_items)} posts)")
        except Exception as e:
            print(f"  ERROR fetching page {page}: {e}")
            stats["failed"] += PER_PAGE

    print(f"\nFetched {len(all_items)} posts. Processing...")

    for i, item in enumerate(all_items, 1):
        if i % PROGRESS_EVERY == 0:
            print(f"  Progress: {i}/{len(all_items)}")

        try:
            slug = item.get("slug", "")
            if not slug:
                stats["failed"] += 1
                continue

            filename = f"{slug}.md"
            if already_scraped(filename):
                stats["skipped"] += 1
                continue

            title = clean_title(item.get("title", {}).get("rendered", ""))
            body_html = item.get("content", {}).get("rendered", "")
            body_md = html_to_markdown(body_html)
            date_pub = _extract_date(item.get("date", ""))
            date_mod = _extract_date(item.get("modified", ""))
            link = item.get("link", "")
            tags = extract_tags(item)
            author = extract_author(item)
            image_url = extract_featured_image(item)
            yoast = extract_yoast_meta(item)
            wc = count_words(body_md)

            frontmatter = build_frontmatter(
                slug=slug, url=link, title=title, author=author,
                date_published=date_pub, date_modified=date_mod,
                tags=tags, word_count=wc, image_url=image_url,
                description=yoast.get("description", ""),
            )

            content = f"{frontmatter}\n\n{body_md}\n"

            filepath = os.path.join(ARTICLES_DIR, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

            existing.add(filename)
            stats["scraped"] += 1

        except Exception as e:
            print(f"  ERROR processing {item.get('slug', '?')}: {e}")
            stats["failed"] += 1

    print(f"\n{'='*60}")
    print(f"NUTRITIONSTRIPPED SCRAPER - SUMMARY")
    print(f"{'='*60}")
    print(f"  Scraped:  {stats['scraped']}")
    print(f"  Skipped:  {stats['skipped']} (already existed)")
    print(f"  Failed:   {stats['failed']}")
    print(f"  Files:    {len(os.listdir(ARTICLES_DIR))}")


if __name__ == "__main__":
    main()
