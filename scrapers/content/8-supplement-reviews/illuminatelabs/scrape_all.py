#!/usr/bin/env python3
"""
Scrape illuminatelabs.org blog articles.

Shopify platform, 934 articles in sitemap_blogs_1.xml.
Rich JSON-LD Article schema with citations.
source_tier: tier2, source_category: 8_supplement_reviews
"""

import json
import os
import re
import time
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error

from bs4 import BeautifulSoup
from markdownify import markdownify as md

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

DOMAIN = "illuminatelabs.org"
SITEMAP_URL = "https://illuminatelabs.org/sitemap_blogs_1.xml"
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


def parse_sitemap(url: str) -> list[str]:
    """Parse sitemap XML and return article URLs."""
    data = fetch(url)
    if not data:
        print(f"ERROR: Could not fetch sitemap: {url}")
        return []

    root = ET.fromstring(data)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = []
    for loc in root.findall(".//sm:loc", ns):
        u = loc.text.strip() if loc.text else ""
        # Only blog articles, skip locale variants
        if "/blogs/health/" in u and "/en-ca/" not in u and "/en-in/" not in u:
            urls.append(u)
    return urls


def extract_jsonld(soup: BeautifulSoup) -> dict | None:
    """Extract Article JSON-LD from page."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and data.get("@type") == "Article":
                return data
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") == "Article":
                        return item
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def scrape_article(url: str) -> dict | None:
    """Scrape a single article page."""
    data = fetch(url)
    if not data:
        return None

    html_str = data.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html_str, "lxml")

    # Extract from JSON-LD first
    jsonld = extract_jsonld(soup)

    title = ""
    author = "Illuminate Labs"
    date_pub = ""
    keywords = []
    image_url = None

    if jsonld:
        title = jsonld.get("headline", "")
        date_pub = jsonld.get("datePublished", "")
        kw = jsonld.get("keywords")
        if isinstance(kw, list):
            keywords = kw
        elif isinstance(kw, str):
            keywords = [k.strip() for k in kw.split(",") if k.strip()]
        auth = jsonld.get("author")
        if isinstance(auth, dict):
            author = auth.get("name", author)
        elif isinstance(auth, list) and auth:
            author = auth[0].get("name", author) if isinstance(auth[0], dict) else author
        img = jsonld.get("image")
        if isinstance(img, dict):
            image_url = img.get("url")
        elif isinstance(img, str):
            image_url = img

    # Fallback title from OG or h1
    if not title:
        og = soup.find("meta", property="og:title")
        if og:
            title = og.get("content", "")
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

    if not date_pub:
        og_time = soup.find("meta", property="article:published_time")
        if og_time:
            date_pub = og_time.get("content", "")

    if not image_url:
        og_img = soup.find("meta", property="og:image")
        if og_img:
            image_url = og_img.get("content", "")

    # Extract body content
    body_el = soup.find("article") or soup.find("div", class_="article-content")
    if not body_el:
        # Try finding the main content area
        body_el = soup.find("div", id="content") or soup.find("main")
    if not body_el:
        return None

    # Remove script/style/nav
    for tag in body_el.find_all(["script", "style", "nav", "footer"]):
        tag.decompose()

    body_html = str(body_el)
    body_md = md(body_html, heading_style="ATX", strip=["img"]).strip()

    if not body_md or len(body_md) < 100:
        return None

    wc = len(re.sub(r"[*_#\[\]()>~`|]", " ", body_md).split())

    # Slug from URL
    slug = url.rstrip("/").split("/")[-1]

    return {
        "source_id": slug,
        "source_domain": DOMAIN,
        "source_url": url,
        "title": title,
        "author": author,
        "date_published": date_pub,
        "tags": keywords[:10],
        "content_type": "supplement_review",
        "source_tier": SOURCE_TIER,
        "word_count": wc,
        "image_url": image_url,
        "body_md": body_md,
    }


def main():
    os.makedirs(ARTICLES_DIR, exist_ok=True)

    print(f"Scraping {DOMAIN}...")
    print(f"  Fetching sitemap: {SITEMAP_URL}")

    urls = parse_sitemap(SITEMAP_URL)
    print(f"  Found {len(urls)} article URLs")
    print()

    scraped = 0
    skipped = 0
    failed = 0

    for i, url in enumerate(urls, 1):
        slug = url.rstrip("/").split("/")[-1]
        filepath = os.path.join(ARTICLES_DIR, f"{slug}.md")

        if os.path.isfile(filepath):
            skipped += 1
            continue

        article = scrape_article(url)
        if article is None:
            failed += 1
            if i % 50 == 0:
                print(f"  [{i}/{len(urls)}] FAILED: {slug}")
            continue

        content = build_frontmatter(article) + "\n" + article["body_md"] + "\n"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        scraped += 1

        if i % 50 == 0:
            print(f"  [{i}/{len(urls)}] scraped={scraped} skipped={skipped} failed={failed}")

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
