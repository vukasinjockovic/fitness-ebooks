#!/usr/bin/env python3
"""
Scrape axiawh.com/resources articles.

WordPress with custom post types not exposed via REST API.
HTML scrape from sitemap: resources-sitemap.xml (192) + news-sitemap.xml (34).
Cloudflare passive. Rate limit: 1 req/sec.

Site: axiawh.com
Posts: 192 resources + 34 news = 226 total
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

SOURCE_DOMAIN = "axiawh.com"
SOURCE_CATEGORY = "9_womens_health"
SOURCE_TIER = "tier2"
SITEMAP_INDEX_URL = "https://axiawh.com/sitemap_index.xml"
DELAY = 1.0
REQUEST_TIMEOUT = 30

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT


def fetch_sitemap_urls() -> list[str]:
    """Fetch URLs from resources-sitemap.xml and news-sitemap.xml."""
    print("Fetching sitemap index...")
    resp = session.get(SITEMAP_INDEX_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.content)

    child_urls = []
    for sitemap in root.findall("sm:sitemap", SITEMAP_NS):
        loc = sitemap.find("sm:loc", SITEMAP_NS)
        if loc is not None and loc.text:
            child_urls.append(loc.text.strip())

    print(f"  Found {len(child_urls)} child sitemaps")

    article_urls = []
    for child_url in child_urls:
        # Only resources and news sitemaps
        if "resources-sitemap" in child_url or "news-sitemap" in child_url:
            print(f"  Fetching: {child_url}")
            resp = session.get(child_url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            child_root = ElementTree.fromstring(resp.content)
            for url_elem in child_root.findall("sm:url", SITEMAP_NS):
                loc = url_elem.find("sm:loc", SITEMAP_NS)
                if loc is not None and loc.text:
                    article_urls.append(loc.text.strip())
            print(f"    -> {len(article_urls)} URLs so far")
            time.sleep(DELAY)

    # Filter out index pages (just /resources/ or /news/ without slug)
    article_urls = [u for u in article_urls
                    if not u.rstrip("/").endswith("/resources")
                    and not u.rstrip("/").endswith("/news")]

    return article_urls


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
                    types = {"Article", "BlogPosting", "NewsArticle", "WebPage"}
                    if t in types or (isinstance(t, list) and set(t) & types):
                        return item
    return None


def extract_date(raw: str) -> str:
    if not raw:
        return ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else raw


def extract_body(soup: BeautifulSoup) -> str:
    """Extract body from Axia resource/news page.

    Axia uses div.news-article for the main content area (despite being resources).
    """
    # Remove nav, footer, header first
    for tag in soup.find_all(["nav", "footer", "header", "script", "style",
                              "noscript", "iframe", "svg"]):
        tag.decompose()

    # Best selector: div.news-article (used for both resources and news)
    content_div = soup.find("div", class_="news-article")
    if content_div:
        # Remove "Similar Articles" section
        for el in content_div.find_all(class_=re.compile(r"similar|related|article-list", re.I)):
            el.decompose()
        body_md = md(str(content_div), heading_style="ATX", strip=["img"])
        body_md = re.sub(r"\n{3,}", "\n\n", body_md)
        if len(body_md.strip()) > 100:
            return body_md.strip()

    # Fallback: find div with most paragraphs
    candidates = []
    for div in soup.find_all("div"):
        paras = div.find_all("p")
        text = div.get_text(strip=True)
        if len(paras) >= 3 and len(text) > 500:
            candidates.append((len(paras), div))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        content_el = candidates[0][1]
        for cls in ["similar", "related", "article-list", "footer", "sidebar"]:
            for el in content_el.find_all(class_=re.compile(cls, re.I)):
                el.decompose()
        body_md = md(str(content_el), heading_style="ATX", strip=["img"])
        body_md = re.sub(r"\n{3,}", "\n\n", body_md)
        return body_md.strip()

    return ""


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
    if not body_md or len(body_md) < 50:
        return {"status": "empty", "slug": slug}

    # JSON-LD metadata
    jsonld = extract_jsonld(soup)

    title = ""
    if jsonld:
        title = jsonld.get("headline", "") or jsonld.get("name", "")
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else slug
    # Clean title
    title = re.sub(r"\s*\|.*$", "", title)  # Remove "| Axia" suffix

    author = "Axia Women's Health"
    date_published = ""
    date_modified = ""
    image_url = ""
    tags = []

    if jsonld:
        a = jsonld.get("author", {})
        if isinstance(a, dict):
            author = a.get("name", author)
        date_published = extract_date(jsonld.get("datePublished", ""))
        date_modified = extract_date(jsonld.get("dateModified", ""))

    if not date_published:
        pub = soup.find("meta", property="article:published_time")
        if pub:
            date_published = extract_date(pub.get("content", ""))

    if not image_url:
        og = soup.find("meta", property="og:image")
        if og:
            image_url = og.get("content", "")

    # Tag by URL path
    if "/resources/" in url:
        tags.append("resource")
    elif "/news/" in url:
        tags.append("news")

    wc = len(re.sub(r"[*_#\[\]()>`~|]", " ", body_md).split())

    meta = {
        "title": title,
        "author": author,
        "date_published": date_published,
        "date_modified": date_modified,
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
    parser = argparse.ArgumentParser(description="Scrape axiawh.com resources")
    parser.add_argument("--limit", type=int, default=0, help="Limit to N posts (0=all)")
    args = parser.parse_args()

    os.makedirs(ARTICLES_DIR, exist_ok=True)

    # Step 1: Get URLs from sitemap
    print("=== Phase 1: Fetching URLs from sitemap ===")
    urls = fetch_sitemap_urls()
    print(f"Found {len(urls)} article URLs")

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
