#!/usr/bin/env python3
"""
Scrape dietdoctor.com editorial articles and guides (NOT recipes).

DietDoctor is WordPress but WP REST API is behind OAuth2 proxy.
Must scrape HTML directly from sitemap URLs.

Sitemaps:
  - post-sitemap1.xml (964), post-sitemap2.xml (990), post-sitemap3.xml (16) = 1,970 posts
  - page-sitemap1.xml (362), page-sitemap2.xml (357) = 719 pages (guides)
  - EXCLUDE: kd_recipe-sitemap* (already scraped)

Selectors: .kd-text-section for article body
Metadata: itemprop attributes + meta tags (no JSON-LD on articles)
No anti-bot (Nginx/Varnish). No rate limit in robots.txt.

Resume-safe.

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

SITEMAP_INDEX_URL = "https://www.dietdoctor.com/sitemap_index.xml"
SOURCE_DOMAIN = "dietdoctor.com"
SOURCE_TIER = "tier2"
SOURCE_CATEGORY = "3_nutrition_meal_planning"

# Sitemap patterns to include (posts + pages, NOT recipes)
INCLUDE_PATTERNS = ["post-sitemap", "page-sitemap"]
EXCLUDE_PATTERNS = ["kd_recipe", "kd_author", "web-story", "kd_recipe_diet"]

# URL paths to exclude (recipes already scraped)
RECIPE_PATH_PATTERNS = ["/recipes/", "/recipe/", "/kd_recipe/"]

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
REQUEST_TIMEOUT = 30
CRAWL_DELAY = 2.0  # Be polite, no explicit limit in robots.txt
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


def should_include_sitemap(url: str) -> bool:
    basename = url.rstrip("/").split("/")[-1].lower()
    if any(exc in basename for exc in EXCLUDE_PATTERNS):
        return False
    return any(inc in basename for inc in INCLUDE_PATTERNS)


def is_recipe_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(p in path for p in RECIPE_PATH_PATTERNS)


def get_article_urls(session: requests.Session) -> list[str]:
    print(f"Fetching sitemap index: {SITEMAP_INDEX_URL}")
    all_sitemaps = fetch_sitemap_index(session)
    print(f"  Found {len(all_sitemaps)} sitemaps")

    urls = []
    for sm_url in all_sitemaps:
        if should_include_sitemap(sm_url):
            basename = sm_url.rstrip("/").split("/")[-1]
            print(f"  Fetching: {basename}")
            found = fetch_sitemap_urls(session, sm_url)
            urls.extend(found)
            print(f"    -> {len(found)} URLs")
            time.sleep(0.5)

    # Filter out recipe URLs
    before = len(urls)
    urls = [u for u in urls if not is_recipe_url(u)]
    print(f"\nTotal URLs: {before}, After recipe filter: {len(urls)}")
    return urls


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------

def url_to_slug(url: str) -> str:
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    slug = parts[-1] if parts else "unknown"
    slug = re.sub(r"[^\w\-]", "-", slug).strip("-")
    return slug or "unknown"


def extract_metadata(soup: BeautifulSoup) -> dict:
    """Extract metadata from itemprop attributes and meta tags."""
    meta = {
        "title": "", "author": "",
        "date_published": "", "date_modified": "",
        "tags": [], "image_url": "", "description": "",
    }

    # Title
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        meta["title"] = og_title["content"]
    else:
        title_tag = soup.find("title")
        if title_tag:
            meta["title"] = title_tag.get_text(strip=True)
            # Remove " - Diet Doctor" suffix
            meta["title"] = re.sub(r"\s*[-|]\s*Diet\s*Doctor\s*$", "", meta["title"])

    # Author from itemprop
    author_elem = soup.find(attrs={"itemprop": "author"})
    if author_elem:
        name_elem = author_elem.find(attrs={"itemprop": "name"})
        if name_elem:
            meta["author"] = name_elem.get_text(strip=True)
        else:
            meta["author"] = author_elem.get_text(strip=True)
    if not meta["author"]:
        am = soup.find("meta", attrs={"name": "author"})
        if am and am.get("content"):
            meta["author"] = am["content"]

    # Dates
    date_pub = soup.find(attrs={"itemprop": "datePublished"})
    if date_pub:
        meta["date_published"] = _extract_date(
            date_pub.get("content", "") or date_pub.get("datetime", "") or date_pub.get_text(strip=True)
        )
    if not meta["date_published"]:
        pm = soup.find("meta", property="article:published_time")
        if pm and pm.get("content"):
            meta["date_published"] = _extract_date(pm["content"])

    date_mod = soup.find(attrs={"itemprop": "dateModified"})
    if date_mod:
        meta["date_modified"] = _extract_date(
            date_mod.get("content", "") or date_mod.get("datetime", "") or date_mod.get_text(strip=True)
        )
    if not meta["date_modified"]:
        mm = soup.find("meta", property="article:modified_time")
        if mm and mm.get("content"):
            meta["date_modified"] = _extract_date(mm["content"])

    # Image
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        meta["image_url"] = og_image["content"]

    # Description
    desc = soup.find("meta", attrs={"name": "description"})
    if desc and desc.get("content"):
        meta["description"] = desc["content"]

    # Tags from category links
    cat_links = soup.select("a[rel='tag']")
    for link in cat_links:
        tag = link.get_text(strip=True)
        if tag and tag not in meta["tags"]:
            meta["tags"].append(tag)

    return meta


def _extract_date(raw: str) -> str:
    if not raw:
        return ""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return match.group(1) if match else raw


def extract_body(soup: BeautifulSoup) -> str:
    """Extract article body from kd-text-section divs."""
    # Primary: DietDoctor specific
    sections = soup.find_all("div", class_="kd-text-section")
    if sections:
        combined = "\n".join(str(s) for s in sections)
    else:
        # Fallback: try single-post or entry-content
        content = soup.find("div", class_="single-post")
        if not content:
            content = soup.find("div", class_="entry-content")
        if not content:
            content = soup.find("article")
        if not content:
            content = soup.find("main")
        if not content:
            return ""
        combined = str(content)

    # Parse and clean
    temp = BeautifulSoup(combined, "lxml")
    for tag in temp.find_all(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()
    for cls in ["kd-recipe-card", "recipe-card", "social-share", "newsletter",
                "related-posts", "ad-container", "sidebar"]:
        for el in temp.find_all(class_=lambda c: c and cls in c):
            el.decompose()

    body_md = md(str(temp), heading_style="ATX", strip=["img"])
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

    if not body_md or len(body_md) < 50:
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
    parser = argparse.ArgumentParser(description="Scrape DietDoctor articles/guides")
    parser.add_argument("--limit", type=int, default=0, help="Max articles (0=all)")
    args = parser.parse_args()

    os.makedirs(ARTICLES_DIR, exist_ok=True)
    existing = set(os.listdir(ARTICLES_DIR))
    stats = {"scraped": 0, "skipped": 0, "failed": 0}

    print("DietDoctor Articles/Guides Scraper")
    print(f"Articles directory: {ARTICLES_DIR}")
    print(f"Existing files: {len(existing)}")

    session = make_session()

    try:
        urls = get_article_urls(session)
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
    print(f"DIETDOCTOR ARTICLES SCRAPER - SUMMARY")
    print(f"{'='*60}")
    print(f"  Scraped:  {stats['scraped']}")
    print(f"  Skipped:  {stats['skipped']} (already existed)")
    print(f"  Failed:   {stats['failed']}")
    print(f"  Files:    {len(os.listdir(ARTICLES_DIR))}")


if __name__ == "__main__":
    main()
