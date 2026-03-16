#!/usr/bin/env python3
"""
Scrape thepauselife.com (Dr. Mary Claire Haver / Galveston Diet).

Shopify platform - no API available. HTML scrape from sitemap URLs.
Selectors: .article-content, .article-heading
Rate limit: 2 req/sec safe (Shopify).

Site: thepauselife.com
Posts: 132 blog + 26 podcast = 158 content items
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
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")
RAW_DIR = os.path.join(SCRIPT_DIR, "raw")

SOURCE_DOMAIN = "thepauselife.com"
SOURCE_CATEGORY = "9_womens_health"
SOURCE_TIER = "tier2"
SITEMAP_URL = "https://thepauselife.com/sitemap.xml"
DELAY = 0.5  # 2 req/sec
REQUEST_TIMEOUT = 30

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT


def fetch_sitemap_urls() -> list[str]:
    """Fetch sitemap and extract blog post URLs."""
    print("Fetching sitemap index...")
    resp = session.get(SITEMAP_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.content)

    # Find child sitemaps
    child_urls = []
    for sitemap in root.findall("sm:sitemap", SITEMAP_NS):
        loc = sitemap.find("sm:loc", SITEMAP_NS)
        if loc is not None and loc.text:
            child_urls.append(loc.text.strip())

    # If no child sitemaps, this might be a flat sitemap
    if not child_urls:
        urls = []
        for url_elem in root.findall("sm:url", SITEMAP_NS):
            loc = url_elem.find("sm:loc", SITEMAP_NS)
            if loc is not None and loc.text:
                urls.append(loc.text.strip())
        return [u for u in urls if "/blogs/" in u and not u.endswith("/blogs/the-pause-blog") and not u.endswith("/blogs/the-unpaused-podcast")]

    # Fetch blog sitemaps
    blog_urls = []
    for child_url in child_urls:
        if "blogs" in child_url.lower():
            print(f"  Fetching child sitemap: {child_url}")
            resp = session.get(child_url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            child_root = ElementTree.fromstring(resp.content)
            for url_elem in child_root.findall("sm:url", SITEMAP_NS):
                loc = url_elem.find("sm:loc", SITEMAP_NS)
                if loc is not None and loc.text:
                    u = loc.text.strip()
                    if "/blogs/" in u:
                        blog_urls.append(u)
            time.sleep(DELAY)

    # Filter out index pages
    blog_urls = [u for u in blog_urls
                 if not u.endswith("/blogs/the-pause-blog")
                 and not u.endswith("/blogs/the-unpaused-podcast")
                 and not u.endswith("/blogs/partnerships")]

    return blog_urls


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
            t = data.get("@type", "")
            if t in ("Article", "BlogPosting", "NewsArticle"):
                return data
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("@type", "") in ("Article", "BlogPosting", "NewsArticle"):
                    return item
    return None


def extract_date(raw: str) -> str:
    if not raw:
        return ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else raw


def extract_body(soup: BeautifulSoup) -> str:
    """Extract body from Shopify article template."""
    content_div = soup.find(class_="article-content")
    if not content_div:
        content_div = soup.find("div", class_="rte")
    if not content_div:
        # Try article element
        content_div = soup.find("article")
    if not content_div:
        return ""

    for tag in content_div.find_all(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()

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


def scrape_article(url: str) -> dict | None:
    slug = url_to_slug(url)
    article_path = os.path.join(ARTICLES_DIR, f"{slug}.md")

    if os.path.isfile(article_path):
        return {"status": "skipped", "slug": slug}

    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            return {"status": "not_found", "slug": slug}
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"status": "error", "slug": slug, "error": str(e)}

    html = resp.text
    os.makedirs(RAW_DIR, exist_ok=True)
    with open(os.path.join(RAW_DIR, f"{slug}.html"), "w", encoding="utf-8") as f:
        f.write(html)

    soup = BeautifulSoup(html, "lxml")
    body_md = extract_body(soup)
    if not body_md or len(body_md) < 50:
        return {"status": "empty", "slug": slug}

    # Title
    title = ""
    heading = soup.find(class_="article-heading")
    if heading:
        h1 = heading.find("h1")
        title = h1.get_text(strip=True) if h1 else heading.get_text(strip=True)
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else slug

    # JSON-LD
    jsonld = extract_jsonld(soup)
    author = "Dr. Mary Claire Haver"
    date_published = ""
    image_url = ""
    tags = []

    if jsonld:
        a = jsonld.get("author", {})
        if isinstance(a, dict):
            author = a.get("name", author)
        date_published = extract_date(jsonld.get("datePublished", ""))
        img = jsonld.get("image", "")
        if isinstance(img, str):
            image_url = img
        elif isinstance(img, dict):
            image_url = img.get("url", "")

    if not date_published:
        pub_meta = soup.find("meta", property="article:published_time")
        if pub_meta:
            date_published = extract_date(pub_meta.get("content", ""))

    if not image_url:
        og = soup.find("meta", property="og:image")
        if og:
            image_url = og.get("content", "")

    # Determine content type from URL
    if "/the-unpaused-podcast/" in url:
        tags.append("podcast")
    else:
        tags.append("menopause")

    wc = len(re.sub(r"[*_#\[\]()>`~|]", " ", body_md).split())

    meta = {
        "title": title,
        "author": author,
        "date_published": date_published,
        "tags": tags,
        "image_url": image_url,
        "word_count": wc,
    }

    frontmatter = build_frontmatter(slug, url, meta)
    os.makedirs(ARTICLES_DIR, exist_ok=True)
    with open(article_path, "w", encoding="utf-8") as f:
        f.write(frontmatter)
        f.write("\n\n")
        f.write(body_md)
        f.write("\n")

    return {"status": "success", "slug": slug, "title": title, "word_count": wc}


def main():
    parser = argparse.ArgumentParser(description="Scrape thepauselife.com")
    parser.add_argument("--limit", type=int, default=0, help="Limit to N posts (0=all)")
    args = parser.parse_args()

    os.makedirs(ARTICLES_DIR, exist_ok=True)

    # Step 1: Get URLs from sitemap
    print("=== Phase 1: Fetching URLs from sitemap ===")
    urls = fetch_sitemap_urls()
    print(f"Found {len(urls)} blog post URLs")

    if args.limit > 0:
        urls = urls[:args.limit]
        print(f"Limited to {len(urls)} posts")

    # Step 2: Scrape each article
    print(f"\n=== Phase 2: Scraping {len(urls)} articles ===")
    stats = {"success": 0, "skipped": 0, "failed": 0}

    for i, url in enumerate(urls, 1):
        result = scrape_article(url)
        if result:
            if result["status"] == "success":
                stats["success"] += 1
                print(f"  [{i}/{len(urls)}] OK: {result.get('title', '')[:60]} ({result.get('word_count', 0)}w)")
            elif result["status"] == "skipped":
                stats["skipped"] += 1
            else:
                stats["failed"] += 1
                print(f"  [{i}/{len(urls)}] FAIL ({result['status']}): {result['slug']}")

        if i % 25 == 0:
            print(f"  Progress: {i}/{len(urls)} (ok={stats['success']}, skip={stats['skipped']}, fail={stats['failed']})")

        time.sleep(DELAY)

    print(f"\n=== COMPLETE ===")
    print(f"  Success:  {stats['success']}")
    print(f"  Skipped:  {stats['skipped']}")
    print(f"  Failed:   {stats['failed']}")
    print(f"  Total:    {len(urls)}")


if __name__ == "__main__":
    main()
