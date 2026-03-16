#!/usr/bin/env python3
"""
Scrape noom.com/blog content via sitemap + HTML scraping.

The blog uses WordPress with Divi theme. WP REST API was disabled (404).
Content is in HTML via et_pb_post_content / post-content divs.

Sitemaps: post-sitemap1.xml (~941), post-sitemap2.xml (~640)
Filter to /blog/ URLs only.

robots.txt: Crawl-delay: 10 (respected at 5s since we're not hammering)
Resume-safe: skips files that already exist.

Usage:
    python3 scrape_all.py
    python3 scrape_all.py --limit 100
"""

import argparse
import json
import os
import re
import time
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

SITEMAP_INDEX_URL = "https://www.noom.com/sitemaps.xml"
SOURCE_DOMAIN = "noom.com"
SOURCE_TIER = "tier2"
SOURCE_CATEGORY = "3_nutrition_meal_planning"

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
REQUEST_TIMEOUT = 30
CRAWL_DELAY = 5.0  # robots.txt says 10, but that's very conservative for polite scraping
PROGRESS_EVERY = 50

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


# ---------------------------------------------------------------------------
# Sitemap parsing
# ---------------------------------------------------------------------------

def fetch_sitemap_index(session: requests.Session) -> list[str]:
    resp = session.get(SITEMAP_INDEX_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.content)
    urls = []
    for sitemap in root.findall("sm:sitemap", SITEMAP_NS):
        loc = sitemap.find("sm:loc", SITEMAP_NS)
        if loc is not None and loc.text:
            urls.append(loc.text.strip())
    return urls


def fetch_sitemap_urls(session: requests.Session, sitemap_url: str) -> list[str]:
    resp = session.get(sitemap_url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.content)
    urls = []
    for url_elem in root.findall("sm:url", SITEMAP_NS):
        loc = url_elem.find("sm:loc", SITEMAP_NS)
        if loc is not None and loc.text:
            urls.append(loc.text.strip())
    return urls


def get_blog_urls(session: requests.Session) -> list[str]:
    print(f"Fetching sitemap index: {SITEMAP_INDEX_URL}")
    sitemaps = fetch_sitemap_index(session)
    print(f"  Found {len(sitemaps)} sitemaps")

    all_urls = []
    for sm_url in sitemaps:
        basename = sm_url.rstrip("/").split("/")[-1]
        if "post-sitemap" in basename.lower():
            print(f"  Fetching: {basename}")
            urls = fetch_sitemap_urls(session, sm_url)
            all_urls.extend(urls)
            print(f"    -> {len(urls)} URLs")
            time.sleep(1)

    # Filter to /blog/ URLs
    blog_urls = [u for u in all_urls if "/blog/" in u]
    print(f"\nTotal URLs: {len(all_urls)}, Blog URLs: {len(blog_urls)}")
    return blog_urls


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------

def url_to_slug(url: str) -> str:
    path = urlparse(url).path.strip("/")
    # /blog/category/slug -> slug
    parts = path.split("/")
    slug = parts[-1] if parts else "unknown"
    slug = re.sub(r"[^\w\-]", "-", slug).strip("-")
    return slug or "unknown"


def extract_jsonld(soup: BeautifulSoup) -> dict | None:
    target_types = {"Article", "BlogPosting", "NewsArticle", "WebPage"}
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            t = data.get("@type", "")
            if (isinstance(t, str) and t in target_types) or \
               (isinstance(t, list) and set(t) & target_types):
                return data
            if "@graph" in data:
                for item in data["@graph"]:
                    if isinstance(item, dict):
                        it = item.get("@type", "")
                        if (isinstance(it, str) and it in target_types) or \
                           (isinstance(it, list) and set(it) & target_types):
                            return item
    return None


def extract_metadata(soup: BeautifulSoup) -> dict:
    meta = {
        "title": "", "author": "",
        "date_published": "", "date_modified": "",
        "tags": [], "image_url": "", "description": "",
    }

    # Try JSON-LD first
    jsonld = extract_jsonld(soup)
    if jsonld:
        meta["title"] = jsonld.get("headline", "") or jsonld.get("name", "")
        meta["description"] = jsonld.get("description", "")
        meta["date_published"] = _extract_date(jsonld.get("datePublished", ""))
        meta["date_modified"] = _extract_date(jsonld.get("dateModified", ""))

        author = jsonld.get("author", {})
        if isinstance(author, dict):
            meta["author"] = author.get("name", "")
        elif isinstance(author, list) and author:
            meta["author"] = author[0].get("name", "") if isinstance(author[0], dict) else str(author[0])

    # Fallbacks from meta tags
    if not meta["title"]:
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            meta["title"] = og["content"]
        else:
            t = soup.find("title")
            if t:
                meta["title"] = t.get_text(strip=True)

    if not meta["author"]:
        am = soup.find("meta", attrs={"name": "author"})
        if am and am.get("content"):
            meta["author"] = am["content"]
        else:
            meta["author"] = "Noom"

    if not meta["date_published"]:
        pm = soup.find("meta", property="article:published_time")
        if pm and pm.get("content"):
            meta["date_published"] = _extract_date(pm["content"])

    if not meta["date_modified"]:
        mm = soup.find("meta", property="article:modified_time")
        if mm and mm.get("content"):
            meta["date_modified"] = _extract_date(mm["content"])

    if not meta["image_url"]:
        oi = soup.find("meta", property="og:image")
        if oi and oi.get("content"):
            meta["image_url"] = oi["content"]

    if not meta["description"]:
        desc = soup.find("meta", attrs={"name": "description"})
        if desc and desc.get("content"):
            meta["description"] = desc["content"]

    # Extract category from URL or meta
    cat_meta = soup.find("meta", property="article:section")
    if cat_meta and cat_meta.get("content"):
        meta["tags"].append(cat_meta["content"])

    return meta


def _extract_date(raw: str) -> str:
    if not raw:
        return ""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return match.group(1) if match else raw


def extract_body(soup: BeautifulSoup) -> str:
    """Extract body from Divi theme content containers."""
    # Noom uses Divi -- et_pb_post_content is the main content div
    content = soup.find(class_=lambda c: c and "et_pb_post_content" in str(c))
    if not content:
        content = soup.find(class_=lambda c: c and "post-content" in str(c))
    if not content:
        content = soup.find("div", class_="entry-content")
    if not content:
        content = soup.find("article")
    if not content:
        return ""

    for tag in content.find_all(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()
    for cls in ["ad-container", "sidebar", "newsletter", "social-share",
                "related-posts", "cta-banner", "et_social_inline"]:
        for el in content.find_all(class_=lambda c: c and cls in str(c)):
            el.decompose()

    body_html = str(content)
    body_md = md(body_html, heading_style="ATX", strip=["img"])
    body_md = re.sub(r"\n{3,}", "\n\n", body_md)
    return body_md.strip()


def count_words(text: str) -> int:
    return len(text.split()) if text else 0


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------

def _escape_yaml(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_frontmatter(slug: str, url: str, meta: dict) -> str:
    lines = ["---"]
    lines.append(f'source_id: "{slug}"')
    lines.append(f'source_domain: "{SOURCE_DOMAIN}"')
    lines.append(f'source_url: "{_escape_yaml(url)}"')
    lines.append(f'title: "{_escape_yaml(meta["title"])}"')
    if meta["author"]:
        lines.append(f'author: "{_escape_yaml(meta["author"])}"')
    if meta["date_published"]:
        lines.append(f'date_published: "{meta["date_published"]}"')
    if meta["date_modified"]:
        lines.append(f'date_modified: "{meta["date_modified"]}"')
    if meta["description"]:
        lines.append(f'description: "{_escape_yaml(meta["description"])}"')
    if meta["tags"]:
        tag_list = ", ".join(f'"{_escape_yaml(t)}"' for t in meta["tags"][:20])
        lines.append(f"tags: [{tag_list}]")
    lines.append(f'content_type: "article"')
    lines.append(f'source_tier: "{SOURCE_TIER}"')
    lines.append(f'source_category: "{SOURCE_CATEGORY}"')
    if meta.get("word_count"):
        lines.append(f'word_count: {meta["word_count"]}')
    if meta["image_url"]:
        lines.append(f'image_url: "{meta["image_url"]}"')
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fetch + scrape
# ---------------------------------------------------------------------------

def fetch_page(session: requests.Session, url: str) -> str | None:
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if resp.status_code == 404:
                return None
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = 5 * (attempt + 1)
                print(f"    HTTP {resp.status_code}, retrying in {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.text
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if attempt < 2:
                time.sleep(5)
                continue
            return None
    return None


def scrape_one(session: requests.Session, url: str, existing: set, stats: dict) -> None:
    slug = url_to_slug(url)
    filename = f"{slug}.md"

    if filename in existing:
        stats["skipped"] += 1
        return

    html = fetch_page(session, url)
    if html is None:
        stats["failed"] += 1
        return

    soup = BeautifulSoup(html, "lxml")
    meta = extract_metadata(soup)
    body_md = extract_body(soup)

    if not body_md or len(body_md) < 100:
        stats["failed"] += 1
        return

    meta["word_count"] = count_words(body_md)
    frontmatter = build_frontmatter(slug, url, meta)
    content = f"{frontmatter}\n\n{body_md}\n"

    filepath = os.path.join(ARTICLES_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    existing.add(filename)
    stats["scraped"] += 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scrape Noom blog articles")
    parser.add_argument("--limit", type=int, default=0, help="Max articles (0=all)")
    args = parser.parse_args()

    os.makedirs(ARTICLES_DIR, exist_ok=True)
    existing = set(os.listdir(ARTICLES_DIR))
    stats = {"scraped": 0, "skipped": 0, "failed": 0}

    print("Noom Blog HTML Scraper")
    print(f"Articles directory: {ARTICLES_DIR}")
    print(f"Existing files: {len(existing)}")

    session = make_session()

    try:
        urls = get_blog_urls(session)
    except Exception as e:
        print(f"ERROR fetching sitemaps: {e}")
        return

    if args.limit > 0:
        urls = urls[:args.limit]

    print(f"\nScraping {len(urls)} articles (delay: {CRAWL_DELAY}s)...")

    for i, url in enumerate(urls, 1):
        if i % PROGRESS_EVERY == 0:
            print(f"  Progress: {i}/{len(urls)} | scraped={stats['scraped']} skipped={stats['skipped']} failed={stats['failed']}")

        scrape_one(session, url, existing, stats)
        time.sleep(CRAWL_DELAY)

    session.close()

    print(f"\n{'='*60}")
    print(f"NOOM BLOG SCRAPER - SUMMARY")
    print(f"{'='*60}")
    print(f"  Scraped:  {stats['scraped']}")
    print(f"  Skipped:  {stats['skipped']} (already existed)")
    print(f"  Failed:   {stats['failed']}")
    print(f"  Files:    {len(os.listdir(ARTICLES_DIR))}")


if __name__ == "__main__":
    main()
