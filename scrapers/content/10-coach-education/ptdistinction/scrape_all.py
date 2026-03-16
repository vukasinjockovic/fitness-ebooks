#!/usr/bin/env python3
"""
Scrape ptdistinction.com/blog via sitemap + HTML scraping.

Site: ptdistinction.com
Platform: Webflow (NOT WordPress)
Estimated posts: 333
Source tier: tier2
Category: 10_coach_education
Content selector: div.w-richtext
Single flat sitemap.xml.

Usage:
    python3 scrape_all.py
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape as html_unescape

from bs4 import BeautifulSoup
from markdownify import markdownify as md

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

DOMAIN = "ptdistinction.com"
SITEMAP_URL = "https://www.ptdistinction.com/sitemap.xml"
SOURCE_TIER = "tier2"
SOURCE_CATEGORY = "10_coach_education"
USER_AGENT = "Mozilla/5.0 (compatible; GymZilla/1.0)"
REQUEST_TIMEOUT = 30
DELAY = 1.0
PROGRESS_EVERY = 50

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)


def html_to_markdown(html_content: str) -> str:
    cleaned = _SCRIPT_STYLE_RE.sub("", html_content)
    result = md(cleaned, heading_style="ATX", strip=["img"])
    return result.strip() if result else ""


def word_count(text: str) -> int:
    cleaned = re.sub(r"[*_#\[\]()>~`|]", " ", text)
    return len(cleaned.split())


def _escape_yaml(s: str) -> str:
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    return s


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def fetch_url(url: str, retries: int = 2) -> bytes | None:
    for attempt in range(1 + retries):
        req = urllib.request.Request(url)
        req.add_header("User-Agent", USER_AGENT)
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                wait = 5 * (attempt + 1)
                print(f"  HTTP {e.code} for {url[:80]}... retrying in {wait}s")
                time.sleep(wait)
                continue
            print(f"  HTTP {e.code} for {url[:80]}")
            return None
        except (urllib.error.URLError, OSError) as e:
            if attempt < retries:
                time.sleep(3)
                continue
            print(f"  Error fetching {url[:80]}: {e}")
            return None
    return None


# ---------------------------------------------------------------------------
# Sitemap
# ---------------------------------------------------------------------------


def fetch_blog_urls() -> list[str]:
    """Fetch all /blog/ URLs from the sitemap."""
    print(f"  Fetching sitemap: {SITEMAP_URL}")
    data = fetch_url(SITEMAP_URL)
    if not data:
        print("  Failed to fetch sitemap!")
        return []

    urls = []
    try:
        root = ET.fromstring(data)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for url_el in root.findall(".//sm:url/sm:loc", ns):
            if url_el.text:
                loc = url_el.text.strip()
                # Only blog posts
                if "/blog/" in loc and loc != "https://www.ptdistinction.com/blog":
                    urls.append(loc)
    except ET.ParseError as e:
        print(f"  Sitemap parse error: {e}")

    print(f"  Found {len(urls)} blog URLs")
    return urls


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------


def extract_article(html_bytes: bytes, url: str) -> dict | None:
    html_str = html_bytes.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html_str, "lxml")

    # Title
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        og_title = soup.find("meta", property="og:title")
        if og_title:
            title = og_title.get("content", "")
    title = html_unescape(title) if title else ""

    # Content from div.w-richtext (Webflow rich text)
    content_el = soup.select_one("div.w-richtext")
    if not content_el:
        # Fallback
        content_el = soup.select_one("article")
        if not content_el:
            content_el = soup.select_one("main")
    if not content_el:
        return None

    body_md = html_to_markdown(str(content_el))
    if not body_md or len(body_md) < 50:
        return None

    # JSON-LD metadata
    author = "Tim Saye"  # Primary author per probe
    date_published = ""
    tags = []

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            ld = json.loads(script.string)
            if isinstance(ld, list):
                ld = ld[0]
            if isinstance(ld, dict):
                if ld.get("@type") in ("Article", "BlogPosting"):
                    a = ld.get("author", {})
                    if isinstance(a, dict):
                        author = a.get("name", author)
                    elif isinstance(a, list) and a:
                        author = a[0].get("name", author)
                    date_published = ld.get("datePublished", date_published)
                    kw = ld.get("keywords", [])
                    if isinstance(kw, str):
                        tags = [t.strip() for t in kw.split(",")]
                    elif isinstance(kw, list):
                        tags = kw
        except (json.JSONDecodeError, TypeError):
            continue

    if not date_published:
        date_meta = soup.find("meta", property="article:published_time")
        if date_meta:
            date_published = date_meta.get("content", "")

    # Slug from URL
    slug = url.rstrip("/").split("/")[-1]
    if not slug:
        slug = "unknown"

    wc = word_count(body_md)

    image_url = None
    og_img = soup.find("meta", property="og:image")
    if og_img:
        image_url = og_img.get("content")

    return {
        "source_id": slug,
        "source_domain": DOMAIN,
        "source_url": url,
        "title": title,
        "author": author,
        "date_published": date_published,
        "tags": tags,
        "content_type": "coach_education",
        "source_tier": SOURCE_TIER,
        "word_count": wc,
        "image_url": image_url,
        "body_md": body_md,
    }


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------


def save_article(article: dict):
    tags_str = json.dumps(article["tags"])
    image_str = f'"{_escape_yaml(article["image_url"])}"' if article["image_url"] else "null"

    frontmatter = "\n".join([
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
        f"image_url: {image_str}",
        "---",
    ]) + "\n"

    path = os.path.join(ARTICLES_DIR, f"{article['source_id']}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(frontmatter + "\n" + article["body_md"] + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    os.makedirs(ARTICLES_DIR, exist_ok=True)

    print(f"PT Distinction Blog Scraper")
    print(f"Articles dir: {ARTICLES_DIR}")
    print()

    all_urls = fetch_blog_urls()
    if not all_urls:
        print("No URLs found. Exiting.")
        return

    print()

    scraped = 0
    skipped = 0
    failed = 0

    for i, url in enumerate(all_urls, 1):
        slug = url.rstrip("/").split("/")[-1]
        if not slug:
            failed += 1
            continue

        if os.path.isfile(os.path.join(ARTICLES_DIR, f"{slug}.md")):
            skipped += 1
            if i % PROGRESS_EVERY == 0:
                print(f"  Progress: {i}/{len(all_urls)} "
                      f"({scraped} scraped, {skipped} skipped, {failed} failed)")
            continue

        html_bytes = fetch_url(url)
        if not html_bytes:
            failed += 1
            time.sleep(DELAY)
            continue

        article = extract_article(html_bytes, url)
        if article:
            save_article(article)
            scraped += 1
        else:
            failed += 1

        if i % PROGRESS_EVERY == 0:
            print(f"  Progress: {i}/{len(all_urls)} "
                  f"({scraped} scraped, {skipped} skipped, {failed} failed)")

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
