#!/usr/bin/env python3
"""
Scrape healthline.com/nutrition articles via sitemap + HTML scraping.

Healthline uses a custom CMS (hlcms) by RVO Health. Content is SSR HTML with
rich JSON-LD (MedicalWebPage schema). No anti-bot protection (CloudFront CDN).

Sitemap structure:
  - sitemap.xml -> hlan.xml -> hlan-articles-1.xml through hlan-articles-4.xml
  - These contain ~3,815 nutrition-specific URLs (all /nutrition/ prefix)

robots.txt: Crawl-delay: 5 (respected)
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
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

SITEMAP_INDEX_URL = "https://www.healthline.com/sitemap.xml"
SOURCE_DOMAIN = "healthline.com"
SOURCE_TIER = "tier2"
SOURCE_CATEGORY = "3_nutrition_meal_planning"

# Only scrape HLAN (nutrition) sitemaps
HLAN_PATTERN = "hlan-articles"

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
REQUEST_TIMEOUT = 30
CRAWL_DELAY = 5.0  # per robots.txt
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
    """Fetch sitemap index and return child sitemap URLs."""
    resp = session.get(SITEMAP_INDEX_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.content)
    urls = []
    for sitemap in root.findall("sm:sitemap", SITEMAP_NS):
        loc = sitemap.find("sm:loc", SITEMAP_NS)
        if loc is not None and loc.text:
            urls.append(loc.text.strip())
    return urls


def fetch_child_sitemap_urls(session: requests.Session, sitemap_url: str) -> list[str]:
    """Fetch a sitemap XML and return all article URLs."""
    resp = session.get(sitemap_url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.content)

    # Could be a sitemap index (hlan.xml) or a URL set (hlan-articles-N.xml)
    # Try as sitemap index first
    child_sitemaps = root.findall("sm:sitemap", SITEMAP_NS)
    if child_sitemaps:
        # This is a sub-index, recurse
        urls = []
        for sm in child_sitemaps:
            loc = sm.find("sm:loc", SITEMAP_NS)
            if loc is not None and loc.text:
                child_url = loc.text.strip()
                if HLAN_PATTERN in child_url:
                    print(f"    Fetching: {child_url}")
                    child_urls = fetch_child_sitemap_urls(session, child_url)
                    urls.extend(child_urls)
                    time.sleep(1)
        return urls

    # URL set
    urls = []
    for url_elem in root.findall("sm:url", SITEMAP_NS):
        loc = url_elem.find("sm:loc", SITEMAP_NS)
        if loc is not None and loc.text:
            urls.append(loc.text.strip())
    return urls


def get_nutrition_urls(session: requests.Session) -> list[str]:
    """Get all nutrition article URLs from HLAN sitemaps."""
    print(f"Fetching sitemap index: {SITEMAP_INDEX_URL}")
    all_sitemaps = fetch_sitemap_index(session)
    print(f"  Found {len(all_sitemaps)} top-level sitemaps")

    urls = []
    for sm_url in all_sitemaps:
        basename = sm_url.rstrip("/").split("/")[-1]
        if "hlan" in basename.lower():
            print(f"  Processing HLAN sitemap: {basename}")
            found = fetch_child_sitemap_urls(session, sm_url)
            urls.extend(found)
            print(f"    -> {len(found)} URLs")
            time.sleep(1)

    # Filter to /nutrition/ URLs only
    nutrition_urls = [u for u in urls if "/nutrition/" in u]
    print(f"\nTotal nutrition URLs: {len(nutrition_urls)}")
    return nutrition_urls


# ---------------------------------------------------------------------------
# URL to slug
# ---------------------------------------------------------------------------

def url_to_slug(url: str) -> str:
    """Extract slug from healthline URL path."""
    from urllib.parse import urlparse
    path = urlparse(url).path.strip("/")
    # /nutrition/some-article -> nutrition--some-article or just the last part
    parts = path.split("/")
    slug = parts[-1] if parts else "unknown"
    slug = re.sub(r"[^\w\-]", "-", slug).strip("-")
    return slug or "unknown"


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------

def extract_jsonld(soup: BeautifulSoup) -> dict | None:
    """Extract MedicalWebPage or Article JSON-LD."""
    target_types = {"MedicalWebPage", "Article", "BlogPosting", "WebPage"}
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
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    it = item.get("@type", "")
                    if (isinstance(it, str) and it in target_types) or \
                       (isinstance(it, list) and set(it) & target_types):
                        return item
    return None


def extract_metadata(jsonld: dict | None, soup: BeautifulSoup) -> dict:
    """Extract metadata from JSON-LD and/or meta tags."""
    meta = {
        "title": "",
        "author": "",
        "reviewer": "",
        "date_published": "",
        "date_modified": "",
        "tags": [],
        "image_url": "",
        "description": "",
    }

    if jsonld:
        meta["title"] = jsonld.get("headline", "") or jsonld.get("name", "")

        author = jsonld.get("author", {})
        if isinstance(author, dict):
            meta["author"] = author.get("name", "")
        elif isinstance(author, list) and author:
            meta["author"] = author[0].get("name", "") if isinstance(author[0], dict) else str(author[0])

        reviewer = jsonld.get("reviewedBy", {})
        if isinstance(reviewer, dict):
            meta["reviewer"] = reviewer.get("name", "")
        elif isinstance(reviewer, list) and reviewer:
            meta["reviewer"] = reviewer[0].get("name", "") if isinstance(reviewer[0], dict) else str(reviewer[0])

        meta["date_published"] = _extract_date(jsonld.get("datePublished", ""))
        meta["date_modified"] = _extract_date(jsonld.get("dateModified", ""))
        meta["description"] = jsonld.get("description", "")

        keywords = jsonld.get("keywords", [])
        if isinstance(keywords, str):
            meta["tags"] = [k.strip() for k in keywords.split(",") if k.strip()]
        elif isinstance(keywords, list):
            meta["tags"] = [str(k).strip() for k in keywords if k]

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

    return meta


def _extract_date(raw: str) -> str:
    if not raw:
        return ""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return match.group(1) if match else raw


def extract_body(soup: BeautifulSoup) -> str:
    """Extract article body and convert to markdown."""
    # Healthline uses article tag with specific classes
    content = soup.find("article")
    if not content:
        # Try common content containers
        for selector in ["div.article-body", "div.content-body", "div[data-testid='article-body']",
                          "main"]:
            content = soup.select_one(selector)
            if content:
                break

    if not content:
        return ""

    # Remove unwanted elements
    for tag in content.find_all(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()

    # Remove ads, nav, related content, sidebars
    for cls in ["adsense", "ad-container", "sidebar", "related-articles",
                "newsletter-signup", "social-share", "article-share",
                "css-0", "bottom-cta", "inline-cta", "lazy-ad"]:
        for el in content.find_all(class_=lambda c: c and cls in c):
            el.decompose()

    # Remove nav elements
    for nav in content.find_all("nav"):
        nav.decompose()

    body_html = str(content)
    body_md = md(body_html, heading_style="ATX", strip=["img"])
    body_md = re.sub(r"\n{3,}", "\n\n", body_md)
    return body_md.strip()


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
    if meta["reviewer"]:
        lines.append(f'medical_reviewer: "{_escape_yaml(meta["reviewer"])}"')
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
# Fetch + scrape one article
# ---------------------------------------------------------------------------

def fetch_page(session: requests.Session, url: str) -> str | None:
    """Fetch a page with retry."""
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if resp.status_code in (404, 410):
                return None
            if resp.status_code in (403, 429, 500, 502, 503, 504):
                wait = 5 * (attempt + 1)
                print(f"    HTTP {resp.status_code}, retrying in {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.text
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                requests.exceptions.HTTPError) as e:
            if attempt < 2:
                time.sleep(5)
                continue
            return None
    return None


def scrape_one(session: requests.Session, url: str, existing: set, stats: dict) -> None:
    """Scrape a single article."""
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
    jsonld = extract_jsonld(soup)
    meta = extract_metadata(jsonld, soup)
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


def count_words(text: str) -> int:
    return len(text.split()) if text else 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scrape Healthline nutrition articles")
    parser.add_argument("--limit", type=int, default=0, help="Max articles (0=all)")
    args = parser.parse_args()

    os.makedirs(ARTICLES_DIR, exist_ok=True)
    existing = set(os.listdir(ARTICLES_DIR))
    stats = {"scraped": 0, "skipped": 0, "failed": 0}

    print("Healthline Nutrition Scraper")
    print(f"Articles directory: {ARTICLES_DIR}")
    print(f"Existing files: {len(existing)}")

    session = make_session()

    # Get all nutrition URLs from sitemaps
    try:
        urls = get_nutrition_urls(session)
    except Exception as e:
        print(f"ERROR fetching sitemaps: {e}")
        return

    if args.limit > 0:
        urls = urls[:args.limit]

    print(f"\nScraping {len(urls)} articles (crawl-delay: {CRAWL_DELAY}s)...")

    for i, url in enumerate(urls, 1):
        if i % PROGRESS_EVERY == 0:
            print(f"  Progress: {i}/{len(urls)} | scraped={stats['scraped']} skipped={stats['skipped']} failed={stats['failed']}")

        scrape_one(session, url, existing, stats)
        time.sleep(CRAWL_DELAY)

    session.close()

    print(f"\n{'='*60}")
    print(f"HEALTHLINE NUTRITION SCRAPER - SUMMARY")
    print(f"{'='*60}")
    print(f"  Scraped:  {stats['scraped']}")
    print(f"  Skipped:  {stats['skipped']} (already existed)")
    print(f"  Failed:   {stats['failed']}")
    print(f"  Files:    {len(os.listdir(ARTICLES_DIR))}")


if __name__ == "__main__":
    main()
