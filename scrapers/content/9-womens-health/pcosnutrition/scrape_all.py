#!/usr/bin/env python3
"""
Scrape pcosnutrition.com articles.

201 posts. WP API for metadata (content field is empty), HTML scrape for body.
source_tier: tier2, source_category: 9_womens_health
"""

import html as html_module
import json
import os
import re
import time
import urllib.request
import urllib.error

from bs4 import BeautifulSoup
from markdownify import markdownify as md

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

DOMAIN = "pcosnutrition.com"
API_BASE = "https://www.pcosnutrition.com/wp-json/wp/v2"
SOURCE_TIER = "tier2"
USER_AGENT = "Mozilla/5.0 (compatible; GymZilla/1.0)"
DELAY = 1.0


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


def fetch_json(url: str) -> list | dict | None:
    data = fetch(url)
    if data is None:
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


def fetch_all_post_metadata() -> list[dict]:
    """Fetch all post metadata via WP API (content will be empty)."""
    all_posts = []
    page = 1
    while True:
        url = f"{API_BASE}/posts?per_page=100&page={page}&_fields=id,title,slug,link,date,categories,tags,author,yoast_head_json"
        print(f"  API page {page}: {url}")
        posts = fetch_json(url)
        if not posts or not isinstance(posts, list) or len(posts) == 0:
            break
        all_posts.extend(posts)
        if len(posts) < 100:
            break
        page += 1
        time.sleep(0.5)
    return all_posts


def fetch_categories() -> dict:
    """Fetch WP categories."""
    cache = {}
    page = 1
    while True:
        url = f"{API_BASE}/categories?per_page=100&page={page}"
        items = fetch_json(url)
        if not items or not isinstance(items, list):
            break
        for item in items:
            cache[item["id"]] = html_module.unescape(item.get("name", ""))
        if len(items) < 100:
            break
        page += 1
    return cache


def scrape_html_content(url: str) -> str | None:
    """Scrape article body from HTML page."""
    data = fetch(url)
    if not data:
        return None

    html_str = data.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html_str, "lxml")

    # pcosnutrition uses div.post.single > div.content
    body_el = None
    post_div = soup.find("div", class_="post")
    if post_div:
        body_el = post_div.find("div", class_="content")

    if not body_el:
        body_el = soup.find("div", class_="entry-content")
    if not body_el:
        body_el = soup.find("article")
    if not body_el:
        body_el = soup.find("main")

    if not body_el:
        return None

    # Clean up
    for tag in body_el.find_all(["script", "style", "nav", "footer", "aside"]):
        tag.decompose()
    # Remove comments section
    for tag in body_el.find_all(id=re.compile(r"comment")):
        tag.decompose()
    # Remove share/social buttons
    for tag in body_el.find_all(class_=re.compile(r"share|social|related")):
        tag.decompose()

    body_md = md(str(body_el), heading_style="ATX", strip=["img"]).strip()
    return body_md if body_md and len(body_md) > 100 else None


def main():
    os.makedirs(ARTICLES_DIR, exist_ok=True)

    print(f"Scraping {DOMAIN}...")
    print(f"  Step 1: Fetch post metadata via WP API")

    posts = fetch_all_post_metadata()
    print(f"  Found {len(posts)} posts")

    print(f"  Step 2: Fetch categories")
    cat_cache = fetch_categories()
    print(f"  Cached {len(cat_cache)} categories")
    print()

    scraped = 0
    skipped = 0
    failed = 0

    for i, post in enumerate(posts, 1):
        slug = post.get("slug", "")
        if not slug:
            failed += 1
            continue

        filepath = os.path.join(ARTICLES_DIR, f"{slug}.md")
        if os.path.isfile(filepath):
            skipped += 1
            continue

        link = post.get("link", "")
        title = html_module.unescape(post.get("title", {}).get("rendered", ""))

        if not link or not title:
            failed += 1
            continue

        # Scrape HTML content
        body_md = scrape_html_content(link)
        if not body_md:
            failed += 1
            if failed <= 5:
                print(f"  [{i}/{len(posts)}] FAILED to get content: {slug}")
            continue

        wc = len(re.sub(r"[*_#\[\]()>~`|]", " ", body_md).split())

        # Resolve categories
        tags = []
        for cat_id in post.get("categories", []):
            name = cat_cache.get(cat_id)
            if name:
                tags.append(name.lower())

        # Date
        date_pub = post.get("date", "")
        yoast = post.get("yoast_head_json")
        if yoast and isinstance(yoast, dict):
            yoast_date = yoast.get("datePublished")
            if yoast_date:
                date_pub = yoast_date

        # Author from Yoast
        author = "Angela Grassi, MS RDN"
        if yoast and isinstance(yoast, dict):
            graph = yoast.get("schema", {}).get("@graph", [])
            for item in graph:
                item_type = item.get("@type")
                if item_type == "Person" or (isinstance(item_type, list) and "Person" in item_type):
                    name = item.get("name")
                    if name:
                        author = name
                        break

        article = {
            "source_id": slug,
            "source_domain": DOMAIN,
            "source_url": link,
            "title": title,
            "author": author,
            "date_published": date_pub,
            "tags": tags,
            "content_type": "nutrition",
            "source_tier": SOURCE_TIER,
            "word_count": wc,
            "image_url": None,
            "body_md": body_md,
        }

        content = build_frontmatter(article) + "\n" + body_md + "\n"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        scraped += 1

        if i % 25 == 0:
            print(f"  [{i}/{len(posts)}] scraped={scraped} skipped={skipped} failed={failed}")

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
