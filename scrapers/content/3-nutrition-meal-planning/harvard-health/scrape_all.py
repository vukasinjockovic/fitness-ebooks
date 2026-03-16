#!/usr/bin/env python3
"""
Scrape health.harvard.edu FREE blog posts on nutrition topics.

Blog posts (/blog/ path) are fully free. Regular articles are paywalled.
source_tier: "tier1" (highest medical authority)

Sitemap: sitemap-content.xml has 13,168 URLs
We filter to: /blog/ path + nutrition keywords = ~342 free posts

SSR HTML with JSON-LD Article schema. reCAPTCHA v3 present but doesn't block curl.
Authors include MDs and health professionals.

Resume-safe.

Usage:
    python3 scrape_all.py
    python3 scrape_all.py --limit 100
    python3 scrape_all.py --all-blog   # Get ALL blog posts, not just nutrition
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

SITEMAP_CONTENT_URL = "https://www.health.harvard.edu/sitemap/sitemap-content.xml"
SOURCE_DOMAIN = "health.harvard.edu"
SOURCE_TIER = "tier1"
SOURCE_CATEGORY = "3_nutrition_meal_planning"

NUTRITION_KEYWORDS = [
    "nutrition", "diet", "food", "calorie", "vitamin", "protein",
    "supplement", "weight", "eating", "keto", "vegan",
    "vegetarian", "carb", "fat", "fiber", "mineral", "omega",
    "antioxidant", "probiotic", "gut", "fasting",
    "cholesterol", "sugar", "gluten", "dairy", "plant-based",
    "mediterranean", "low-carb", "high-protein", "superfood",
    "nutrient", "recipe", "fruit", "vegetable",
    "grain", "seed", "nut", "bean", "fish", "meat",
    "breakfast", "lunch", "dinner", "snack",
    "hydration", "water", "electrolyte",
    "iron", "calcium", "zinc", "magnesium",
    "b12", "vitamin-d", "vitamin-c", "folate",
    "obesity", "bmi", "metabolism", "healthy-eating",
    "heart-health", "blood-sugar", "insulin",
]

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
REQUEST_TIMEOUT = 30
CRAWL_DELAY = 3.0
PROGRESS_EVERY = 25

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


# ---------------------------------------------------------------------------
# Sitemap
# ---------------------------------------------------------------------------

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


def is_blog_url(url: str) -> bool:
    return "/blog/" in urlparse(url).path


def is_nutrition_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(kw in path for kw in NUTRITION_KEYWORDS)


def get_blog_nutrition_urls(session: requests.Session, all_blog: bool = False) -> list[str]:
    print(f"Fetching content sitemap: {SITEMAP_CONTENT_URL}")
    all_urls = fetch_sitemap_urls(session, SITEMAP_CONTENT_URL)
    print(f"  Total content URLs: {len(all_urls)}")

    blog_urls = [u for u in all_urls if is_blog_url(u)]
    print(f"  Blog URLs (free): {len(blog_urls)}")

    if all_blog:
        print(f"  --all-blog: returning all {len(blog_urls)} blog posts")
        return blog_urls

    nutrition = [u for u in blog_urls if is_nutrition_url(u)]
    print(f"  Nutrition-filtered blog: {len(nutrition)}")
    return nutrition


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------

def url_to_slug(url: str) -> str:
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    slug = parts[-1] if parts else "unknown"
    slug = re.sub(r"[^\w\-]", "-", slug).strip("-")
    return slug or "unknown"


def extract_jsonld(soup: BeautifulSoup) -> dict | None:
    target_types = {"Article", "BlogPosting", "NewsArticle", "WebPage", "MedicalWebPage"}
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
    meta = {
        "title": "", "author": "",
        "date_published": "", "date_modified": "",
        "tags": [], "image_url": "", "description": "",
    }

    if jsonld:
        meta["title"] = jsonld.get("headline", "") or jsonld.get("name", "")
        meta["description"] = jsonld.get("description", "")
        meta["date_published"] = _extract_date(jsonld.get("datePublished", ""))
        meta["date_modified"] = _extract_date(jsonld.get("dateModified", ""))

        author = jsonld.get("author", {})
        if isinstance(author, dict):
            meta["author"] = author.get("name", "")
        elif isinstance(author, list) and author:
            names = []
            for a in author:
                if isinstance(a, dict):
                    names.append(a.get("name", ""))
                else:
                    names.append(str(a))
            meta["author"] = ", ".join(n for n in names if n)

        image = jsonld.get("image", {})
        if isinstance(image, dict):
            meta["image_url"] = image.get("url", "")
        elif isinstance(image, str):
            meta["image_url"] = image
        elif isinstance(image, list) and image:
            first = image[0]
            meta["image_url"] = first.get("url", "") if isinstance(first, dict) else str(first)

        keywords = jsonld.get("keywords", [])
        if isinstance(keywords, str):
            meta["tags"] = [k.strip() for k in keywords.split(",") if k.strip()]
        elif isinstance(keywords, list):
            meta["tags"] = [str(k).strip() for k in keywords if k]

    # Fallbacks
    if not meta["title"]:
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            meta["title"] = og["content"]
            meta["title"] = re.sub(r"\s*[-|]\s*Harvard Health\s*$", "", meta["title"])
    if not meta["author"]:
        am = soup.find("meta", attrs={"name": "author"})
        if am and am.get("content"):
            meta["author"] = am["content"]
    if not meta["date_published"]:
        pm = soup.find("meta", property="article:published_time")
        if pm and pm.get("content"):
            meta["date_published"] = _extract_date(pm["content"])
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


def is_paywalled(soup: BeautifulSoup) -> bool:
    """Check if content is behind paywall."""
    text = soup.get_text().lower()
    paywall_signals = [
        "to continue reading this article, you must log in",
        "you must log in",
        "subscribe to continue",
        "become a member",
    ]
    return any(signal in text for signal in paywall_signals)


def extract_body(soup: BeautifulSoup) -> str:
    # Try various content selectors
    content = soup.find("article")
    if not content:
        for sel in ["div.entry-content", "div.content-body", "div.article-body", "main"]:
            content = soup.select_one(sel)
            if content:
                break
    if not content:
        return ""

    for tag in content.find_all(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()
    for cls in ["ad-container", "sidebar", "related-posts", "social-share",
                "newsletter", "paywall", "cta", "share-tools"]:
        for el in content.find_all(class_=lambda c: c and cls in c):
            el.decompose()
    for nav in content.find_all("nav"):
        nav.decompose()

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

    # Check for paywall
    if is_paywalled(soup):
        stats["paywalled"] += 1
        return

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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scrape Harvard Health free blog posts")
    parser.add_argument("--limit", type=int, default=0, help="Max articles (0=all)")
    parser.add_argument("--all-blog", action="store_true", help="Get ALL blog posts, not just nutrition")
    args = parser.parse_args()

    os.makedirs(ARTICLES_DIR, exist_ok=True)
    existing = set(os.listdir(ARTICLES_DIR))
    stats = {"scraped": 0, "skipped": 0, "failed": 0, "paywalled": 0}

    print("Harvard Health Blog Scraper (FREE posts only)")
    print(f"Articles directory: {ARTICLES_DIR}")
    print(f"Existing files: {len(existing)}")

    session = make_session()

    try:
        urls = get_blog_nutrition_urls(session, all_blog=args.all_blog)
    except Exception as e:
        print(f"ERROR fetching sitemap: {e}")
        return

    if args.limit > 0:
        urls = urls[:args.limit]

    print(f"\nScraping {len(urls)} blog posts (delay: {CRAWL_DELAY}s)...")

    for i, url in enumerate(urls, 1):
        if i % PROGRESS_EVERY == 0:
            print(f"  Progress: {i}/{len(urls)} | scraped={stats['scraped']} paywalled={stats['paywalled']}")

        scrape_one(session, url, existing, stats)
        time.sleep(CRAWL_DELAY)

    session.close()

    print(f"\n{'='*60}")
    print(f"HARVARD HEALTH SCRAPER - SUMMARY")
    print(f"{'='*60}")
    print(f"  Scraped:    {stats['scraped']}")
    print(f"  Skipped:    {stats['skipped']} (already existed)")
    print(f"  Paywalled:  {stats['paywalled']} (filtered out)")
    print(f"  Failed:     {stats['failed']}")
    print(f"  Files:      {len(os.listdir(ARTICLES_DIR))}")


if __name__ == "__main__":
    main()
