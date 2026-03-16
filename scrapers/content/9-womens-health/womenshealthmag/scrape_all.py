#!/usr/bin/env python3
"""
Scrape womenshealthmag.com articles.

17,754 relevant URLs across food/fitness/health/weight-loss sections.
Next.js SSR with JSON-LD NewsArticle. No Cloudflare challenge.
source_tier: tier3, source_category: 9_womens_health

Uses 10 parallel workers with 1s delay per worker.
"""

import gzip
import json
import os
import re
import time
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from bs4 import BeautifulSoup
from markdownify import markdownify as md

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

DOMAIN = "womenshealthmag.com"
SITEMAP_INDEX = "https://www.womenshealthmag.com/sitemap_index.xml"
SOURCE_TIER = "tier3"
USER_AGENT = "Mozilla/5.0 (compatible; GymZilla/1.0)"
DELAY = 1.0
MAX_WORKERS = 10

# Only scrape these sections
RELEVANT_SECTIONS = {"food", "fitness", "health", "weight-loss"}

_print_lock = Lock()
_stats_lock = Lock()
_stats = {"scraped": 0, "skipped": 0, "failed": 0}


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
        req.add_header("Accept-Encoding", "gzip")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
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


def get_all_urls() -> list[str]:
    """Fetch all relevant article URLs from gzipped sitemaps."""
    print("  Fetching sitemap index...")
    data = fetch(SITEMAP_INDEX)
    if not data:
        print("ERROR: Could not fetch sitemap index")
        return []

    root = ET.fromstring(data)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    # Find content sitemaps
    sitemap_urls = []
    for sitemap in root.findall(".//sm:sitemap", ns):
        loc = sitemap.find("sm:loc", ns)
        if loc is not None:
            u = loc.text.strip() if loc.text else ""
            # Match content sitemaps (usually named with section)
            if "sitemap" in u.lower() and u != SITEMAP_INDEX:
                sitemap_urls.append(u)

    print(f"  Found {len(sitemap_urls)} sub-sitemaps")

    all_urls = []
    for sm_url in sitemap_urls:
        data = fetch(sm_url)
        if not data:
            continue

        # Try to decompress gzip
        try:
            if sm_url.endswith(".gz"):
                data = gzip.decompress(data)
        except Exception:
            pass

        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            continue

        for loc in root.findall(".//sm:loc", ns):
            u = loc.text.strip() if loc.text else ""
            # Filter to relevant sections
            path = u.replace("https://www.womenshealthmag.com/", "")
            section = path.split("/")[0] if "/" in path else ""
            if section in RELEVANT_SECTIONS:
                # Must be an article (has slug after section)
                parts = path.strip("/").split("/")
                if len(parts) >= 2:
                    all_urls.append(u)

        time.sleep(0.5)

    # Deduplicate
    all_urls = list(dict.fromkeys(all_urls))
    return all_urls


def extract_jsonld(soup: BeautifulSoup) -> dict | None:
    """Extract NewsArticle JSON-LD."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict):
                if data.get("@type") in ("NewsArticle", "Article"):
                    return data
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") in ("NewsArticle", "Article"):
                        return item
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def scrape_article(url: str) -> dict | None:
    """Scrape a single WH article."""
    data = fetch(url)
    if not data:
        return None

    html_str = data.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html_str, "lxml")

    # JSON-LD for metadata
    jsonld = extract_jsonld(soup)

    title = ""
    author = "Women's Health"
    date_pub = ""
    image_url = None

    if jsonld:
        title = jsonld.get("headline", "")
        date_pub = jsonld.get("datePublished", "")
        auth = jsonld.get("author")
        if isinstance(auth, dict):
            author = auth.get("name", author)
        elif isinstance(auth, list) and auth:
            names = [a.get("name", "") for a in auth if isinstance(a, dict)]
            author = ", ".join(n for n in names if n) or author
        img = jsonld.get("image")
        if isinstance(img, dict):
            image_url = img.get("url")
        elif isinstance(img, list) and img:
            image_url = img[0].get("url") if isinstance(img[0], dict) else img[0]
        elif isinstance(img, str):
            image_url = img

    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
    if not title:
        return None

    # Section as tag
    path = url.replace("https://www.womenshealthmag.com/", "")
    section = path.split("/")[0] if "/" in path else ""
    tags = [section] if section else []

    # Article section from jsonld
    if jsonld and jsonld.get("articleSection"):
        sect = jsonld.get("articleSection")
        if isinstance(sect, str) and sect.lower() not in tags:
            tags.append(sect.lower())

    # Body content
    body_el = soup.find(class_=re.compile(r"article-body-content|article-body"))
    if not body_el:
        body_el = soup.find("article")
    if not body_el:
        body_el = soup.find(class_=re.compile(r"content-container"))
    if not body_el:
        return None

    for tag in body_el.find_all(["script", "style", "nav", "footer", "aside"]):
        tag.decompose()
    # Remove ad containers and related content
    for tag in body_el.find_all(class_=re.compile(r"ad-|related|newsletter|embed")):
        tag.decompose()

    body_md = md(str(body_el), heading_style="ATX", strip=["img"]).strip()
    if not body_md or len(body_md) < 200:
        return None

    wc = len(re.sub(r"[*_#\[\]()>~`|]", " ", body_md).split())
    slug = url.rstrip("/").split("/")[-1]

    return {
        "source_id": f"{section}-{slug}" if section else slug,
        "source_domain": DOMAIN,
        "source_url": url,
        "title": title,
        "author": author,
        "date_published": date_pub,
        "tags": tags,
        "content_type": "health",
        "source_tier": SOURCE_TIER,
        "word_count": wc,
        "image_url": image_url,
        "body_md": body_md,
    }


def process_url(url: str):
    """Process a single URL (thread worker)."""
    slug = url.rstrip("/").split("/")[-1]
    path = url.replace("https://www.womenshealthmag.com/", "")
    section = path.split("/")[0] if "/" in path else ""
    source_id = f"{section}-{slug}" if section else slug
    filepath = os.path.join(ARTICLES_DIR, f"{source_id}.md")

    if os.path.isfile(filepath):
        with _stats_lock:
            _stats["skipped"] += 1
        return

    time.sleep(DELAY)

    article = scrape_article(url)
    if article is None:
        with _stats_lock:
            _stats["failed"] += 1
        return

    content = build_frontmatter(article) + "\n" + article["body_md"] + "\n"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    with _stats_lock:
        _stats["scraped"] += 1
        total = _stats["scraped"] + _stats["skipped"] + _stats["failed"]
        if total % 200 == 0:
            with _print_lock:
                print(f"  Progress: {total} processed "
                      f"(scraped={_stats['scraped']} skipped={_stats['skipped']} "
                      f"failed={_stats['failed']})")


def main():
    os.makedirs(ARTICLES_DIR, exist_ok=True)

    print(f"Scraping {DOMAIN}...")
    urls = get_all_urls()
    print(f"  Total relevant URLs: {len(urls)}")
    print(f"  Using {MAX_WORKERS} parallel workers, {DELAY}s delay per request")
    print()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_url, url): url for url in urls}
        try:
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    with _stats_lock:
                        _stats["failed"] += 1
        except KeyboardInterrupt:
            print("\n  Interrupted! Waiting for in-flight requests...")
            executor.shutdown(wait=False, cancel_futures=True)

    print()
    print("=" * 60)
    print(f"SUMMARY for {DOMAIN}")
    print(f"  Scraped: {_stats['scraped']}")
    print(f"  Skipped: {_stats['skipped']}")
    print(f"  Failed:  {_stats['failed']}")
    print(f"  Total:   {_stats['scraped'] + _stats['skipped'] + _stats['failed']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
