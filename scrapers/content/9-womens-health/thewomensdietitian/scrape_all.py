#!/usr/bin/env python3
"""
Scrape thewomensdietitian.com via Squarespace JSON API.

~40 posts, Squarespace platform with ?format=json endpoint.
source_tier: tier2, source_category: 9_womens_health
"""

import json
import os
import re
import time
import urllib.request
import urllib.error

from markdownify import markdownify as md

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

DOMAIN = "thewomensdietitian.com"
BLOG_URL = "https://www.thewomensdietitian.com/blog"
SOURCE_TIER = "tier2"
USER_AGENT = "Mozilla/5.0 (compatible; GymZilla/1.0)"
DELAY = 1.5


def _escape_yaml(s: str) -> str:
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    return s


def build_frontmatter(article: dict) -> str:
    tags_str = json.dumps(article.get("tags", []))
    img = article.get("image_url")
    img_str = f'"{_escape_yaml(img)}"' if img else "null"
    lines = [
        "---",
        f'source_id: "{_escape_yaml(article["source_id"])}"',
        f'source_domain: "{_escape_yaml(article["source_domain"])}"',
        f'source_url: "{_escape_yaml(article["source_url"])}"',
        f'title: "{_escape_yaml(article["title"])}"',
        f'author: "{_escape_yaml(article["author"])}"',
        f'date_published: "{_escape_yaml(article["date_published"])}"',
        f"tags: {tags_str}",
        f'content_type: "{_escape_yaml(article["content_type"])}"',
        f'source_tier: "{_escape_yaml(article["source_tier"])}"',
        f'word_count: {article["word_count"]}',
        f"image_url: {img_str}",
        "---",
    ]
    return "\n".join(lines) + "\n"


def fetch(url: str, retries: int = 2) -> bytes | None:
    for attempt in range(1 + retries):
        req = urllib.request.Request(url)
        req.add_header("User-Agent", USER_AGENT)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            return None
        except (urllib.error.URLError, OSError):
            if attempt < retries:
                time.sleep(3)
                continue
            return None
    return None


def fetch_json(url: str) -> dict | None:
    data = fetch(url)
    if data is None:
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


def get_all_posts() -> list[dict]:
    """Fetch all blog posts via Squarespace JSON API with pagination."""
    all_items = []
    url = f"{BLOG_URL}?format=json"

    seen_ids = set()
    while url:
        print(f"  Fetching: {url}")
        data = fetch_json(url)
        if not data:
            break

        items = data.get("items", [])
        if not items:
            break

        new_items = []
        for item in items:
            item_id = item.get("id", "")
            if item_id and item_id not in seen_ids:
                seen_ids.add(item_id)
                new_items.append(item)

        if not new_items:
            break
        all_items.extend(new_items)

        # Squarespace pagination: use offset of last item's publishOn timestamp
        # The API returns 20 items per page, paginating backward in time
        if len(items) >= 20:
            last_ts = items[-1].get("publishOn", items[-1].get("updatedOn", 0))
            if last_ts:
                url = f"{BLOG_URL}?format=json&offset={last_ts}"
            else:
                break
        else:
            break

        time.sleep(DELAY)

    return all_items


def process_item(item: dict) -> dict | None:
    """Process a Squarespace blog item into article dict."""
    title = item.get("title", "")
    if not title:
        return None

    body_html = item.get("body", "")
    if not body_html or len(body_html) < 100:
        return None

    body_md = md(body_html, heading_style="ATX", strip=["img"]).strip()
    if not body_md:
        return None

    wc = len(re.sub(r"[*_#\[\]()>~`|]", " ", body_md).split())

    # Slug from fullUrl
    full_url = item.get("fullUrl", "")
    slug = full_url.rstrip("/").split("/")[-1] if full_url else item.get("urlId", "unknown")
    source_url = f"https://www.thewomensdietitian.com{full_url}" if full_url else ""

    # Date
    date_pub = ""
    publish_on = item.get("publishOn")
    if publish_on:
        # Squarespace timestamps are ms since epoch
        if isinstance(publish_on, (int, float)):
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(publish_on / 1000, tz=timezone.utc)
            date_pub = dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        else:
            date_pub = str(publish_on)

    # Image
    image_url = None
    as_img = item.get("assetUrl")
    if as_img:
        image_url = as_img

    # Tags from categories
    tags = item.get("categories", []) or []

    # Author
    author = "Cory Ruth, MS, RDN"
    auth_data = item.get("author")
    if isinstance(auth_data, dict):
        author = auth_data.get("displayName", author)

    return {
        "source_id": slug,
        "source_domain": DOMAIN,
        "source_url": source_url,
        "title": title,
        "author": author,
        "date_published": date_pub,
        "tags": tags[:10],
        "content_type": "nutrition",
        "source_tier": SOURCE_TIER,
        "word_count": wc,
        "image_url": image_url,
        "body_md": body_md,
    }


def main():
    os.makedirs(ARTICLES_DIR, exist_ok=True)

    print(f"Scraping {DOMAIN} via Squarespace JSON API...")
    print()

    items = get_all_posts()
    print(f"  Found {len(items)} blog items")
    print()

    scraped = 0
    skipped = 0
    failed = 0

    for item in items:
        article = process_item(item)
        if article is None:
            failed += 1
            continue

        filepath = os.path.join(ARTICLES_DIR, f"{article['source_id']}.md")
        if os.path.isfile(filepath):
            skipped += 1
            continue

        content = build_frontmatter(article) + "\n" + article["body_md"] + "\n"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        scraped += 1

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
