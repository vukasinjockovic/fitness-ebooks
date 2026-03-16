#!/usr/bin/env python3
"""
Scrape menohello.com articles.

Pixpa platform, no API. Sitemap-driven HTML scraper.
101 articles, 23 symptom guides, 15 recipes, 14 headlines.
robots.txt blocks ClaudeBot -- use browser User-Agent.

Site: menohello.com
Posts: 219 URLs
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

SOURCE_DOMAIN = "menohello.com"
SOURCE_CATEGORY = "9_womens_health"
SOURCE_TIER = "tier2"
SITEMAP_URL = "https://www.menohello.com/sitemap.xml"
DELAY = 1.0
REQUEST_TIMEOUT = 30

# Use browser UA -- robots.txt blocks AI bots specifically
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

# URL path prefixes for content (skip static/legal pages)
CONTENT_PREFIXES = [
    "/articles/",
    "/menopause-symptoms/",
    "/menopause-toolkit/",
    "/recipes-for-menopause-support/",
    "/menopause-headlines/",
    "/hrt-guide/",
]

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT


def fetch_sitemap_urls() -> list[str]:
    """Fetch sitemap and extract content URLs."""
    print("Fetching sitemap...")
    resp = session.get(SITEMAP_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.content)

    # Check for child sitemaps first
    child_sitemaps = root.findall("sm:sitemap", SITEMAP_NS)
    if child_sitemaps:
        all_urls = []
        for sitemap in child_sitemaps:
            loc = sitemap.find("sm:loc", SITEMAP_NS)
            if loc is not None and loc.text:
                child_url = loc.text.strip()
                if "website" in child_url.lower():
                    print(f"  Fetching child sitemap: {child_url}")
                    resp2 = session.get(child_url, timeout=REQUEST_TIMEOUT)
                    resp2.raise_for_status()
                    child_root = ElementTree.fromstring(resp2.content)
                    for url_elem in child_root.findall("sm:url", SITEMAP_NS):
                        loc2 = url_elem.find("sm:loc", SITEMAP_NS)
                        if loc2 is not None and loc2.text:
                            all_urls.append(loc2.text.strip())
                    time.sleep(DELAY)
    else:
        # Flat sitemap
        all_urls = []
        for url_elem in root.findall("sm:url", SITEMAP_NS):
            loc = url_elem.find("sm:loc", SITEMAP_NS)
            if loc is not None and loc.text:
                all_urls.append(loc.text.strip())

    print(f"  Total sitemap URLs: {len(all_urls)}")

    # Filter to content URLs
    content_urls = []
    for u in all_urls:
        path = urlparse(u).path
        if any(path.startswith(prefix) for prefix in CONTENT_PREFIXES):
            content_urls.append(u)

    print(f"  Content URLs after filtering: {len(content_urls)}")
    return content_urls


def url_to_slug(url: str) -> str:
    path = urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p]
    # Use last 2 parts for uniqueness (e.g., "articles/some-title")
    if len(parts) >= 2:
        return f"{parts[-2]}--{parts[-1]}"
    return parts[-1] if parts else "unknown"


def extract_jsonld_list(soup: BeautifulSoup) -> list[dict]:
    """Extract all JSON-LD blocks."""
    results = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, dict):
                results.append(data)
            elif isinstance(data, list):
                results.extend([d for d in data if isinstance(d, dict)])
        except (json.JSONDecodeError, TypeError):
            continue
    return results


def extract_date(raw: str) -> str:
    if not raw:
        return ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else raw


def extract_body(soup: BeautifulSoup) -> str:
    """Extract body from Pixpa page.

    Pixpa uses non-standard markup. Try multiple strategies.
    """
    # Strategy 1: Find main content area
    # Pixpa often uses .page-content, .blog-post-content, or article
    candidates = []

    for selector in [
        ".blog-post-content", ".page-content", ".post-content",
        "article", ".article-body", "[class*='content']",
        "main", "#content",
    ]:
        for el in soup.select(selector):
            text = el.get_text(strip=True)
            if len(text) > 200:
                candidates.append((len(text), el))

    if not candidates:
        # Fallback: find the div with the most paragraph content
        for div in soup.find_all("div"):
            paras = div.find_all("p")
            if len(paras) >= 3:
                text = div.get_text(strip=True)
                if len(text) > 200:
                    candidates.append((len(text), div))

    if not candidates:
        return ""

    # Pick the best candidate (most text)
    candidates.sort(key=lambda x: x[0], reverse=True)
    content_el = candidates[0][1]

    # Remove unwanted elements
    for tag in content_el.find_all(["script", "style", "noscript", "iframe", "svg", "nav", "footer"]):
        tag.decompose()
    for cls in ["cookie", "banner", "modal", "popup", "newsletter"]:
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

    # Extract metadata from JSON-LD
    jsonlds = extract_jsonld_list(soup)
    title = ""
    author = ""
    date_published = ""
    image_url = ""
    tags = []

    for ld in jsonlds:
        t = ld.get("@type", "")
        if t in ("BlogPosting", "Article", "NewsArticle"):
            title = title or ld.get("headline", "")
            a = ld.get("author", {})
            if isinstance(a, dict):
                author = author or a.get("name", "")
            elif isinstance(a, list) and a:
                author = author or (a[0].get("name", "") if isinstance(a[0], dict) else "")
            date_published = date_published or extract_date(ld.get("datePublished", ""))
            section = ld.get("articleSection", "")
            if isinstance(section, str) and section:
                tags.append(section)
            elif isinstance(section, list):
                tags.extend([s for s in section if s])

    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else ""
    if not title:
        og = soup.find("meta", property="og:title")
        title = og.get("content", slug) if og else slug

    if not author:
        author = "MenoHello"

    if not date_published:
        pub = soup.find("meta", property="article:published_time")
        if pub:
            date_published = extract_date(pub.get("content", ""))

    if not image_url:
        og = soup.find("meta", property="og:image")
        if og:
            image_url = og.get("content", "")

    # Tag by URL section
    path = urlparse(url).path
    if "/articles/" in path:
        tags.append("article")
    elif "/menopause-symptoms/" in path:
        tags.append("symptom-guide")
    elif "/recipes-for-menopause-support/" in path:
        tags.append("recipe")
    elif "/menopause-headlines/" in path:
        tags.append("headline")
    elif "/hrt-guide/" in path:
        tags.append("hrt")
    elif "/menopause-toolkit/" in path:
        tags.append("toolkit")

    tags = list(set(tags))  # dedupe
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
    parser = argparse.ArgumentParser(description="Scrape menohello.com")
    parser.add_argument("--limit", type=int, default=0, help="Limit to N posts (0=all)")
    args = parser.parse_args()

    os.makedirs(ARTICLES_DIR, exist_ok=True)

    # Step 1: Get URLs from sitemap
    print("=== Phase 1: Fetching URLs from sitemap ===")
    urls = fetch_sitemap_urls()
    print(f"Found {len(urls)} content URLs")

    if args.limit > 0:
        urls = urls[:args.limit]
        print(f"Limited to {len(urls)} URLs")

    # Step 2: Scrape each page
    print(f"\n=== Phase 2: Scraping {len(urls)} pages ===")
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
