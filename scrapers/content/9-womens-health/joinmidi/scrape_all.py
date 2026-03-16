#!/usr/bin/env python3
"""
Scrape joinmidi.com blog posts.

Next.js + Prismic CMS, no sitemap. URL discovery from /blog page.
If JS pagination (Show More) blocks discovery, falls back to Wayback CDX.
Long-form content: 5,500-6,500 words per article.

Site: joinmidi.com
Posts: ~175
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
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")
RAW_DIR = os.path.join(SCRIPT_DIR, "raw")
URLS_CACHE = os.path.join(SCRIPT_DIR, "discovered_urls.json")

SOURCE_DOMAIN = "joinmidi.com"
SOURCE_CATEGORY = "9_womens_health"
SOURCE_TIER = "tier2"
BLOG_URL = "https://www.joinmidi.com/blog"
BASE_URL = "https://www.joinmidi.com"
DELAY = 1.0
REQUEST_TIMEOUT = 30

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT


def discover_urls_from_blog_page() -> list[str]:
    """Parse the /blog page SSR HTML for post links."""
    print("Fetching /blog page for URL discovery...")
    resp = session.get(BLOG_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    html = resp.text
    soup = BeautifulSoup(html, "lxml")

    post_urls = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Match /post/slug pattern
        if "/post/" in href:
            if href.startswith("/"):
                href = BASE_URL + href
            if "/post/" in href and href.startswith("http"):
                post_urls.add(href)

    # Also check Next.js data in script tags
    for script in soup.find_all("script"):
        text = script.string or ""
        # Find /post/ URLs in JSON data
        found = re.findall(r'"/post/([^"]+)"', text)
        for slug in found:
            url = f"{BASE_URL}/post/{slug}"
            post_urls.add(url)

    # Also try __NEXT_DATA__ JSON
    next_data_script = soup.find("script", id="__NEXT_DATA__")
    if next_data_script and next_data_script.string:
        try:
            data = json.loads(next_data_script.string)
            # Walk the JSON looking for post slugs
            _extract_slugs_from_json(data, post_urls)
        except json.JSONDecodeError:
            pass

    # Clean URLs: strip trailing backslashes and whitespace
    cleaned = set()
    for u in post_urls:
        u = u.rstrip("\\/").strip()
        if u:
            cleaned.add(u)

    return sorted(cleaned)


def _extract_slugs_from_json(obj, urls: set, depth: int = 0):
    """Recursively find post slugs in Next.js JSON data."""
    if depth > 20:
        return
    if isinstance(obj, dict):
        # Look for uid/slug fields next to type=blog_post or similar
        uid = obj.get("uid", "") or obj.get("slug", "")
        if uid and obj.get("type") in ("blog_post", "post", "article"):
            urls.add(f"{BASE_URL}/post/{uid}")
        # Look for url fields
        url = obj.get("url", "")
        if isinstance(url, str) and "/post/" in url:
            if url.startswith("/"):
                urls.add(f"{BASE_URL}{url}")
            elif url.startswith("http"):
                urls.add(url)
        for v in obj.values():
            _extract_slugs_from_json(v, urls, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _extract_slugs_from_json(item, urls, depth + 1)


def discover_urls_from_wayback() -> list[str]:
    """Fall back to Wayback CDX API for URL discovery."""
    print("Falling back to Wayback CDX for URL discovery...")
    cdx_url = (
        "http://web.archive.org/cdx/search/cdx"
        "?url=joinmidi.com/post/*"
        "&output=json"
        "&fl=original"
        "&filter=statuscode:200"
        "&collapse=urlkey"
    )
    resp = session.get(cdx_url, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    post_urls = set()
    for row in data[1:]:  # skip header
        url = row[0]
        if "/post/" in url:
            # Normalize to https://www.joinmidi.com/post/slug
            parsed = urlparse(url)
            path = parsed.path.strip("/")
            if path.startswith("post/"):
                slug = path.split("/")[1] if len(path.split("/")) > 1 else ""
                if slug:
                    post_urls.add(f"{BASE_URL}/post/{slug}")

    return sorted(post_urls)


def get_all_urls() -> list[str]:
    """Get all post URLs, using cache if available."""
    # Check cache first
    if os.path.isfile(URLS_CACHE):
        with open(URLS_CACHE, "r") as f:
            cached = json.load(f)
        if cached:
            print(f"Loaded {len(cached)} URLs from cache")
            return cached

    # Try blog page first
    urls = discover_urls_from_blog_page()
    print(f"  Found {len(urls)} URLs from /blog page")

    # If we got very few, try Wayback too and merge
    if len(urls) < 50:
        print("  Few URLs from blog page, supplementing with Wayback CDX...")
        wb_urls = discover_urls_from_wayback()
        print(f"  Found {len(wb_urls)} URLs from Wayback CDX")
        urls = sorted(set(urls) | set(wb_urls))
        print(f"  Merged total: {len(urls)} unique URLs")

    # Save cache
    with open(URLS_CACHE, "w") as f:
        json.dump(urls, f, indent=2)

    return urls


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
            types = {"Article", "BlogPosting", "NewsArticle", "MedicalWebPage"}
            if t in types or (isinstance(t, list) and set(t) & types):
                return data
            if "@graph" in data:
                for item in data["@graph"]:
                    it = item.get("@type", "")
                    if it in types or (isinstance(it, list) and set(it) & types):
                        return item
    return None


def extract_date(raw: str) -> str:
    if not raw:
        return ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else raw


def extract_body(soup: BeautifulSoup) -> str:
    """Extract body from Next.js rendered article."""
    # Remove nav, footer, sidebar
    for tag in soup.find_all(["nav", "footer", "header", "script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()

    # Try common article selectors
    candidates = []
    for selector in [
        "article", "[class*='article']", "[class*='post-content']",
        "[class*='blog-content']", "[class*='content']", "main",
    ]:
        for el in soup.select(selector):
            text = el.get_text(strip=True)
            if len(text) > 500:
                candidates.append((len(text), el))

    if not candidates:
        return ""

    candidates.sort(key=lambda x: x[0], reverse=True)
    content_el = candidates[0][1]

    # Remove common noise
    for cls in ["sidebar", "related", "newsletter", "cta", "share", "social"]:
        for el in content_el.find_all(class_=re.compile(cls, re.I)):
            el.decompose()

    body_md = md(str(content_el), heading_style="ATX", strip=["img"])
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
    if not body_md or len(body_md) < 100:
        return {"status": "empty", "slug": slug}

    # JSON-LD metadata (MedicalWebPage schema)
    jsonld = extract_jsonld(soup)

    title = ""
    author = ""
    date_published = ""
    date_modified = ""
    image_url = ""
    tags = ["menopause"]

    if jsonld:
        title = jsonld.get("headline", "") or jsonld.get("name", "")
        a = jsonld.get("author", {})
        if isinstance(a, dict):
            author = a.get("name", "")
        elif isinstance(a, list) and a:
            author = a[0].get("name", "") if isinstance(a[0], dict) else ""
        date_published = extract_date(jsonld.get("datePublished", ""))
        date_modified = extract_date(jsonld.get("dateModified", ""))
        # Image
        img = jsonld.get("image", "")
        if isinstance(img, str):
            image_url = img
        elif isinstance(img, dict):
            image_url = img.get("url", "")
        # Medical reviewer
        reviewer = jsonld.get("reviewedBy", {})
        if isinstance(reviewer, dict) and reviewer.get("name"):
            tags.append("medically-reviewed")

    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else ""
    if not title:
        og = soup.find("meta", property="og:title")
        title = og.get("content", slug) if og else slug

    if not author:
        author = "Midi Health"

    if not date_published:
        pub = soup.find("meta", property="article:published_time")
        if pub:
            date_published = extract_date(pub.get("content", ""))

    if not image_url:
        og = soup.find("meta", property="og:image")
        if og:
            image_url = og.get("content", "")

    wc = len(re.sub(r"[*_#\[\]()>`~|]", " ", body_md).split())

    meta = {
        "title": title,
        "author": author,
        "date_published": date_published,
        "date_modified": date_modified,
        "tags": list(set(tags)),
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
    parser = argparse.ArgumentParser(description="Scrape joinmidi.com blog")
    parser.add_argument("--limit", type=int, default=0, help="Limit to N posts (0=all)")
    parser.add_argument("--no-cache", action="store_true", help="Ignore URL cache")
    args = parser.parse_args()

    os.makedirs(ARTICLES_DIR, exist_ok=True)

    if args.no_cache and os.path.isfile(URLS_CACHE):
        os.remove(URLS_CACHE)

    # Step 1: Discover URLs
    print("=== Phase 1: URL Discovery ===")
    urls = get_all_urls()
    print(f"Total URLs: {len(urls)}")

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
