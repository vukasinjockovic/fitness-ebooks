#!/usr/bin/env python3
"""
Scrape drbrighten.com articles.

WP REST API returns metadata but content.rendered is stripped.
Strategy: WP API for URL discovery + metadata, HTML scrape for body content.

Site: drbrighten.com
Posts: 462
Selector: div.entry-content.content
Cloudflare passive (no challenges)
Source tier: tier2

Usage:
    python3 scrape_all.py              # Scrape all
    python3 scrape_all.py --limit 10   # First 10 only
"""

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")
RAW_DIR = os.path.join(SCRIPT_DIR, "raw")

SOURCE_DOMAIN = "drbrighten.com"
SOURCE_CATEGORY = "9_womens_health"
SOURCE_TIER = "tier2"
WP_API_URL = "https://drbrighten.com/wp-json/wp/v2/posts"
PER_PAGE = 8  # API caps per_page at 8
DELAY = 0.5

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT


def get_all_post_metadata() -> list[dict]:
    """Fetch all post metadata from WP REST API (paginated)."""
    all_posts = []
    page = 1
    while True:
        print(f"  Fetching WP API page {page}...")
        resp = session.get(
            WP_API_URL,
            params={"per_page": PER_PAGE, "page": page},
            timeout=30,
        )
        if resp.status_code == 400:
            break  # past last page
        resp.raise_for_status()

        posts = resp.json()
        if not posts:
            break

        all_posts.extend(posts)
        total = int(resp.headers.get("X-WP-Total", 0))
        total_pages = int(resp.headers.get("X-WP-TotalPages", 0))
        print(f"    Got {len(posts)} posts (total: {total}, pages: {total_pages})")

        if page >= total_pages:
            break
        page += 1
        time.sleep(DELAY)

    return all_posts


def url_to_slug(url: str) -> str:
    path = urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p]
    return parts[-1] if parts else "unknown"


def extract_jsonld(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            if "@graph" in data:
                for item in data["@graph"]:
                    t = item.get("@type", "")
                    if t in ("Article", "BlogPosting", "NewsArticle") or (
                        isinstance(t, list) and set(t) & {"Article", "BlogPosting", "NewsArticle"}
                    ):
                        return item
            t = data.get("@type", "")
            if t in ("Article", "BlogPosting", "NewsArticle"):
                return data
    return None


def extract_date(raw: str) -> str:
    if not raw:
        return ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else raw


def extract_body(soup: BeautifulSoup) -> str:
    """Extract body from div.entry-content.content"""
    content_div = soup.find("div", class_="entry-content")
    if not content_div:
        # try class="content"
        content_div = soup.find("div", class_="content")
    if not content_div:
        return ""

    # Remove unwanted elements
    for tag in content_div.find_all(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()
    for cls in ["sharedaddy", "sd-sharing", "newsletter-signup",
                "wp-block-buttons", "related-posts"]:
        for el in content_div.find_all(class_=cls):
            el.decompose()

    body_md = md(str(content_div), heading_style="ATX", strip=["img"])
    body_md = re.sub(r"\n{3,}", "\n\n", body_md)
    return body_md.strip()


def escape_yaml(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_frontmatter(slug: str, url: str, meta: dict) -> str:
    lines = ["---"]
    lines.append(f'source_id: "{slug}"')
    lines.append(f'source_domain: "{SOURCE_DOMAIN}"')
    lines.append(f'source_url: "{url}"')
    lines.append(f'title: "{escape_yaml(meta.get("title", slug))}"')
    if meta.get("author"):
        lines.append(f'author: "{escape_yaml(meta["author"])}"')
    if meta.get("date_published"):
        lines.append(f'date_published: "{meta["date_published"]}"')
    if meta.get("date_modified"):
        lines.append(f'date_modified: "{meta["date_modified"]}"')
    if meta.get("tags"):
        tag_list = ", ".join(f'"{escape_yaml(t)}"' for t in meta["tags"])
        lines.append(f"tags: [{tag_list}]")
    lines.append(f'content_type: "science"')
    lines.append(f'source_tier: "{SOURCE_TIER}"')
    if meta.get("image_url"):
        lines.append(f'image_url: "{meta["image_url"]}"')
    if meta.get("word_count"):
        lines.append(f'word_count: {meta["word_count"]}')
    lines.append("---")
    return "\n".join(lines)


def scrape_article(url: str, wp_meta: dict) -> dict | None:
    """Scrape a single article page for body content."""
    slug = url_to_slug(url)
    article_path = os.path.join(ARTICLES_DIR, f"{slug}.md")

    if os.path.isfile(article_path):
        return {"status": "skipped", "slug": slug}

    try:
        resp = session.get(url, timeout=30)
        if resp.status_code == 404:
            return {"status": "not_found", "slug": slug}
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"status": "error", "slug": slug, "error": str(e)}

    html = resp.text

    # Save raw
    os.makedirs(RAW_DIR, exist_ok=True)
    with open(os.path.join(RAW_DIR, f"{slug}.html"), "w", encoding="utf-8") as f:
        f.write(html)

    soup = BeautifulSoup(html, "lxml")

    # Extract body
    body_md = extract_body(soup)
    if not body_md or len(body_md) < 100:
        return {"status": "empty", "slug": slug}

    # Extract metadata from JSON-LD on page
    jsonld = extract_jsonld(soup)

    # Build metadata preferring JSON-LD, falling back to WP API
    title = ""
    if jsonld:
        title = jsonld.get("headline", "")
    if not title:
        title = wp_meta.get("title", {}).get("rendered", "")
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else slug

    author = "Dr. Jolene Brighten"
    if jsonld:
        a = jsonld.get("author", {})
        if isinstance(a, dict):
            author = a.get("name", author)
        elif isinstance(a, list) and a:
            author = a[0].get("name", author) if isinstance(a[0], dict) else author

    date_published = ""
    if jsonld:
        date_published = extract_date(jsonld.get("datePublished", ""))
    if not date_published:
        date_published = extract_date(wp_meta.get("date", ""))

    date_modified = ""
    if jsonld:
        date_modified = extract_date(jsonld.get("dateModified", ""))
    if not date_modified:
        date_modified = extract_date(wp_meta.get("modified", ""))

    # Tags from WP API categories / from JSON-LD articleSection
    tags = []
    if jsonld:
        section = jsonld.get("articleSection", "")
        if isinstance(section, list):
            tags.extend(section)
        elif isinstance(section, str) and section:
            tags.append(section)

    # Word count
    wc_val = 0
    if jsonld:
        wc_val = jsonld.get("wordCount", 0)
    if not wc_val:
        wc_val = len(re.sub(r"[*_#\[\]()>`~|]", " ", body_md).split())

    image_url = ""
    if jsonld:
        img = jsonld.get("image", {})
        if isinstance(img, dict):
            image_url = img.get("url", "")
        elif isinstance(img, str):
            image_url = img
    if not image_url:
        og = soup.find("meta", property="og:image")
        if og:
            image_url = og.get("content", "")

    meta = {
        "title": title,
        "author": author,
        "date_published": date_published,
        "date_modified": date_modified,
        "tags": tags,
        "image_url": image_url,
        "word_count": wc_val,
    }

    frontmatter = build_frontmatter(slug, url, meta)
    os.makedirs(ARTICLES_DIR, exist_ok=True)
    with open(article_path, "w", encoding="utf-8") as f:
        f.write(frontmatter)
        f.write("\n\n")
        f.write(body_md)
        f.write("\n")

    return {"status": "success", "slug": slug, "title": title, "word_count": wc_val}


def main():
    parser = argparse.ArgumentParser(description="Scrape drbrighten.com")
    parser.add_argument("--limit", type=int, default=0, help="Limit to N posts (0=all)")
    args = parser.parse_args()

    os.makedirs(ARTICLES_DIR, exist_ok=True)

    # Step 1: Get all post metadata from WP API
    print("=== Phase 1: Fetching post metadata from WP REST API ===")
    wp_posts = get_all_post_metadata()
    print(f"Got {len(wp_posts)} posts from WP API")

    if args.limit > 0:
        wp_posts = wp_posts[:args.limit]
        print(f"Limited to {len(wp_posts)} posts")

    # Step 2: Scrape each article's HTML for body content
    print(f"\n=== Phase 2: Scraping {len(wp_posts)} articles ===")
    stats = {"success": 0, "skipped": 0, "failed": 0}

    for i, post in enumerate(wp_posts, 1):
        url = post.get("link", "")
        if not url:
            stats["failed"] += 1
            continue

        result = scrape_article(url, post)
        if result:
            if result["status"] == "success":
                stats["success"] += 1
                print(f"  [{i}/{len(wp_posts)}] OK: {result.get('title', '')[:60]} ({result.get('word_count', 0)} words)")
            elif result["status"] == "skipped":
                stats["skipped"] += 1
            else:
                stats["failed"] += 1
                print(f"  [{i}/{len(wp_posts)}] FAIL ({result['status']}): {result['slug']}")

        if i % 25 == 0:
            print(f"  Progress: {i}/{len(wp_posts)} (ok={stats['success']}, skip={stats['skipped']}, fail={stats['failed']})")

        time.sleep(DELAY)

    print(f"\n=== COMPLETE ===")
    print(f"  Success:  {stats['success']}")
    print(f"  Skipped:  {stats['skipped']}")
    print(f"  Failed:   {stats['failed']}")
    print(f"  Total:    {len(wp_posts)}")


if __name__ == "__main__":
    main()
