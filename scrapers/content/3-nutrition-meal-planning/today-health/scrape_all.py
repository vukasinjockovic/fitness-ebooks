#!/usr/bin/env python3
"""
Scrape today.com/health articles via the Wayback Machine.

Live site is Akamai-protected and blocks AI crawlers. Wayback has the articles.
JSON-LD NewsArticle includes complete articleBody as plain text (easy extraction).

Usage:
    python3 scrape_all.py                  # Discover URLs + scrape all
    python3 scrape_all.py --limit 10       # Scrape first 10 URLs
    python3 scrape_all.py --scrape-only    # Skip CDX discovery, use cached urls.txt
    python3 scrape_all.py --broad          # Include all /health/* not just diet-fitness
"""

import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape as html_unescape
from threading import Lock

from bs4 import BeautifulSoup
from markdownify import markdownify as md

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")
RAW_DIR = os.path.join(SCRIPT_DIR, "raw")
URLS_FILE = os.path.join(SCRIPT_DIR, "urls.txt")
CDX_CACHE_FILE = os.path.join(SCRIPT_DIR, "cdx_cache.json")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

SOURCE_DOMAIN = "today.com"
SOURCE_TIER = "tier3"
SOURCE_CATEGORY = "3_nutrition_meal_planning"
CONTENT_TYPE = "article"

REQUEST_DELAY = 1.5
RETRY_BACKOFF = 15
MAX_WORKERS = 3

STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "and", "but", "or",
    "nor", "not", "so", "yet", "both", "either", "neither", "each", "every",
    "all", "any", "few", "more", "most", "other", "some", "such", "no",
    "of", "in", "to", "for", "with", "on", "at", "from", "by", "about",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "what", "which",
    "who", "whom", "this", "that", "these", "those", "it", "its", "if",
    "than", "too", "very", "just", "because", "while", "cause", "your",
    "you", "much", "many",
}

_print_lock = Lock()


# ---------------------------------------------------------------------------
# URL Parsing
# ---------------------------------------------------------------------------


def extract_slug(url: str) -> str:
    """Extract slug from today.com URL, removing rcna ID suffix."""
    path = urllib.parse.urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "unknown"
    slug = parts[-1]
    # Remove rcna ID suffix: "healthy-eating-tips-rcna12345" -> "healthy-eating-tips"
    slug = re.sub(r"-rcna\d+$", "", slug)
    return slug


def extract_rcna_id(url: str) -> str | None:
    """Extract the rcna numeric ID from URL for dedup."""
    match = re.search(r"rcna(\d+)", url)
    return match.group(1) if match else None


def make_source_id(slug: str, rcna_id: str | None) -> str:
    """Build source_id using slug and rcna ID for uniqueness."""
    if rcna_id:
        return f"today-{slug}-rcna{rcna_id}"
    return f"today-{slug}"


# ---------------------------------------------------------------------------
# CDX API - URL Discovery
# ---------------------------------------------------------------------------


def discover_urls_cdx(broad: bool = False) -> list[str]:
    """
    Use CDX API to discover today.com health article URLs.
    Default: /health/diet-fitness/* only (~1,676 articles)
    --broad: all /health/* (~3,135 articles)
    """
    all_urls = {}  # rcna_id -> url (dedup by rcna ID)

    if broad:
        pattern = "www.today.com/health/*"
        desc = "all /health/*"
    else:
        pattern = "www.today.com/health/diet-fitness/*"
        desc = "/health/diet-fitness/*"

    print(f"Discovering URLs via CDX API ({desc})...")
    cdx_url = (
        f"http://web.archive.org/cdx/search/cdx?"
        f"url={pattern}&fl=original&collapse=urlkey"
        f"&filter=statuscode:200&limit=0"
    )
    data = _make_request(cdx_url, timeout=180)
    if data:
        text = data.decode("utf-8", errors="replace")
        for line in text.strip().split("\n"):
            url = line.strip()
            if not url:
                continue
            # Strip query params
            url_clean = url.split("?")[0].split("#")[0]
            # Must have rcna ID (article, not section page)
            rcna_id = extract_rcna_id(url_clean)
            if rcna_id:
                # Normalize to https
                url_clean = re.sub(r"^http://", "https://", url_clean)
                # Keep the first (usually canonical) URL per rcna ID
                if rcna_id not in all_urls:
                    all_urls[rcna_id] = url_clean

    urls = sorted(all_urls.values())
    print(f"  Found {len(urls)} unique articles (by rcna ID)")
    return urls


# ---------------------------------------------------------------------------
# CDX Timestamp & HTTP
# ---------------------------------------------------------------------------


def build_cdx_url(url: str) -> str:
    """Build a CDX API query URL."""
    params = urllib.parse.urlencode({
        "url": url,
        "output": "json",
        "fl": "timestamp,original,statuscode",
        "filter": "statuscode:200",
        "limit": -1,
    })
    return f"http://web.archive.org/cdx/search/cdx?{params}"


def parse_cdx_response(cdx_json: list) -> str | None:
    """Parse CDX JSON response and return the most recent timestamp."""
    if not cdx_json or len(cdx_json) < 2:
        return None
    return cdx_json[-1][0]


def build_wayback_url(timestamp: str, original_url: str) -> str:
    """Build a Wayback fetch URL with id_ suffix."""
    return f"https://web.archive.org/web/{timestamp}id_/{original_url}"


def _make_request(url: str, timeout: int = 30) -> bytes | None:
    """Make an HTTP request with retry on 429/503."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                encoding = resp.getheader("Content-Encoding", "")
                if encoding == "gzip" or (data[:2] == b"\x1f\x8b"):
                    try:
                        data = gzip.decompress(data)
                    except (gzip.BadGzipFile, OSError):
                        pass
                return data
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 2:
                wait = RETRY_BACKOFF * (attempt + 1)
                with _print_lock:
                    print(f"  [RETRY] HTTP {e.code} for {url[:80]}... waiting {wait}s")
                time.sleep(wait)
                continue
            if e.code == 404:
                return None
            with _print_lock:
                print(f"  [ERROR] HTTP {e.code} for {url[:80]}...")
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < 2:
                wait = RETRY_BACKOFF * (attempt + 1)
                with _print_lock:
                    print(f"  [RETRY] {e} for {url[:80]}... waiting {wait}s")
                time.sleep(wait)
                continue
            with _print_lock:
                print(f"  [ERROR] {e} for {url[:80]}...")
            return None
    return None


def fetch_cdx_timestamp(url: str) -> str | None:
    """Query CDX API for the best Wayback timestamp."""
    cdx_url = build_cdx_url(url)
    data = _make_request(cdx_url)
    if not data:
        return None
    try:
        cdx_json = json.loads(data)
        return parse_cdx_response(cdx_json)
    except (json.JSONDecodeError, IndexError, KeyError):
        return None


def fetch_wayback_page(timestamp: str, url: str) -> bytes | None:
    """Fetch a Wayback snapshot page."""
    wb_url = build_wayback_url(timestamp, url)
    return _make_request(wb_url, timeout=60)


# ---------------------------------------------------------------------------
# Content Extraction
# ---------------------------------------------------------------------------


def extract_content(html: str, url: str) -> dict:
    """
    Extract content from today.com article.
    Key insight: JSON-LD NewsArticle includes complete articleBody as plain text.
    """
    soup = BeautifulSoup(html, "lxml")

    # Extract JSON-LD -- look for NewsArticle
    json_ld = None
    article_body_text = None
    author = None
    author_title = None
    date_published = None
    date_modified = None
    headline = None

    for ld_script in soup.find_all("script", type="application/ld+json"):
        if not ld_script.string:
            continue
        try:
            parsed = json.loads(ld_script.string)
            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                if not isinstance(item, dict):
                    continue
                schema_type = item.get("@type", "")
                if "NewsArticle" in str(schema_type) or "Article" in str(schema_type):
                    json_ld = item
                    headline = item.get("headline")
                    article_body_text = item.get("articleBody")
                    date_published = item.get("datePublished")
                    date_modified = item.get("dateModified")
                    # Author extraction
                    author_data = item.get("author")
                    if isinstance(author_data, dict):
                        author = author_data.get("name")
                        author_title = author_data.get("jobTitle")
                    elif isinstance(author_data, list) and author_data:
                        first = author_data[0]
                        if isinstance(first, dict):
                            author = first.get("name")
                            author_title = first.get("jobTitle")
                    break
        except json.JSONDecodeError:
            pass

    # Extract title
    title = headline
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
    if not title:
        title = extract_slug(url).replace("-", " ").title()

    # Body: prefer articleBody from JSON-LD (complete plain text)
    body_md = ""
    if article_body_text and len(article_body_text) > 100:
        # articleBody is plain text, convert to basic markdown
        body_md = article_body_text.strip()
    else:
        # Fallback: parse HTML content
        # Find content FIRST before removing elements
        candidates = []
        for selector in ["article", "main", "[role='main']",
                         ".article-body", ".article__body",
                         ".article-content", "#article-body"]:
            for found in soup.select(selector):
                text_len = len(found.get_text(strip=True))
                if text_len > 100:
                    candidates.append((text_len, found))

        content_el = None
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            content_el = candidates[0][1]

        if not content_el:
            content_el = soup.find("body")

        if content_el:
            # Clean within extracted element only
            for tag_name in ["nav", "script", "style", "noscript", "iframe", "svg"]:
                for el in content_el.find_all(tag_name):
                    el.decompose()
            body_html = str(content_el)
            body_md = md(body_html, heading_style="ATX", strip=["img"])
            body_md = re.sub(r"\n{3,}", "\n\n", body_md)
            body_md = body_md.strip()

    return {
        "title": html_unescape(title) if title else "",
        "body_md": body_md,
        "json_ld": json_ld,
        "author": author,
        "author_title": author_title,
        "date_published": date_published,
        "date_modified": date_modified,
    }


# ---------------------------------------------------------------------------
# Frontmatter & Tags
# ---------------------------------------------------------------------------


def word_count(text: str) -> int:
    if not text:
        return 0
    clean = re.sub(r"[*_#\[\]()>`~|]", " ", text)
    return len(clean.split())


def generate_tags(slug: str) -> list[str]:
    tags = ["health", "nutrition"]
    slug_words = [w.lower() for w in slug.split("-") if len(w) > 2]
    slug_words = [w for w in slug_words if w not in STOP_WORDS]
    for word in slug_words:
        if word not in tags:
            tags.append(word)
    return tags


def build_frontmatter(
    source_id: str,
    source_url: str,
    title: str,
    author: str | None,
    author_title: str | None,
    date_published: str | None,
    tags: list[str],
    wc: int,
    wayback_timestamp: str,
) -> str:
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    tags_str = json.dumps(tags)
    author_str = f'"{author}"' if author else "null"
    date_str = f'"{date_published}"' if date_published else "null"

    lines = [
        "---",
        f'source_id: "{source_id}"',
        f'source_domain: "{SOURCE_DOMAIN}"',
        f'source_url: "{source_url}"',
        f'title: "{safe_title}"',
        f"author: {author_str}",
        f"date_published: {date_str}",
        f"tags: {tags_str}",
        f'content_type: "{CONTENT_TYPE}"',
        f'source_tier: "{SOURCE_TIER}"',
        f'source_category: "{SOURCE_CATEGORY}"',
        f"word_count: {wc}",
        "image_url: null",
        f'wayback_timestamp: "{wayback_timestamp}"',
        "---",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def already_exists(articles_dir: str, source_id: str) -> bool:
    return os.path.exists(os.path.join(articles_dir, f"{source_id}.md"))


def save_cdx_cache(cache_path: str, cache: dict) -> None:
    with open(cache_path, "w") as f:
        json.dump(cache, f, indent=2)


def load_cdx_cache(cache_path: str) -> dict:
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_urls(urls_file: str, urls: list[str]) -> None:
    with open(urls_file, "w") as f:
        for url in urls:
            f.write(url + "\n")


def load_urls(urls_file: str) -> list[str]:
    if not os.path.exists(urls_file):
        return []
    with open(urls_file, "r") as f:
        return [line.strip() for line in f if line.strip()]


# ---------------------------------------------------------------------------
# Scraping Pipeline
# ---------------------------------------------------------------------------


def scrape_single(
    url: str,
    cdx_cache: dict,
    articles_dir: str,
    raw_dir: str,
    cdx_cache_lock: Lock,
) -> dict | None:
    slug = extract_slug(url)
    rcna_id = extract_rcna_id(url)
    source_id = make_source_id(slug, rcna_id)

    if already_exists(articles_dir, source_id):
        return {"source_id": source_id, "status": "skipped"}

    # Get CDX timestamp
    with cdx_cache_lock:
        cached_ts = cdx_cache.get(url)

    if cached_ts:
        timestamp = cached_ts
    else:
        time.sleep(REQUEST_DELAY)
        timestamp = fetch_cdx_timestamp(url)
        if timestamp:
            with cdx_cache_lock:
                cdx_cache[url] = timestamp

    if not timestamp:
        with _print_lock:
            print(f"  [SKIP] No Wayback snapshot for {url}")
        return {"source_id": source_id, "status": "no_snapshot"}

    # Fetch page
    time.sleep(REQUEST_DELAY)
    html_bytes = fetch_wayback_page(timestamp, url)
    if not html_bytes:
        with _print_lock:
            print(f"  [SKIP] Failed to fetch {url}")
        return {"source_id": source_id, "status": "fetch_failed"}

    html = html_bytes.decode("utf-8", errors="replace")

    # Save raw
    os.makedirs(raw_dir, exist_ok=True)
    raw_path = os.path.join(raw_dir, f"{source_id}.html")
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Extract
    content = extract_content(html, url)
    if not content["body_md"] or len(content["body_md"]) < 100:
        with _print_lock:
            print(f"  [SKIP] Empty/minimal content for {url}")
        return {"source_id": source_id, "status": "empty_content"}

    tags = generate_tags(slug)
    wc = word_count(content["body_md"])
    frontmatter = build_frontmatter(
        source_id=source_id,
        source_url=url,
        title=content["title"],
        author=content["author"],
        author_title=content.get("author_title"),
        date_published=content["date_published"],
        tags=tags,
        wc=wc,
        wayback_timestamp=timestamp,
    )

    os.makedirs(articles_dir, exist_ok=True)
    article_path = os.path.join(articles_dir, f"{source_id}.md")
    with open(article_path, "w", encoding="utf-8") as f:
        f.write(frontmatter)
        f.write("\n")
        f.write(content["body_md"])
        f.write("\n")

    with _print_lock:
        print(f"  [OK] {source_id} ({wc} words)")

    return {
        "source_id": source_id,
        "status": "success",
        "title": content["title"],
        "word_count": wc,
        "timestamp": timestamp,
        "path": article_path,
    }


def main():
    parser = argparse.ArgumentParser(description="Scrape today.com/health via Wayback Machine")
    parser.add_argument("--limit", type=int, default=0, help="Limit to first N URLs (0 = all)")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help=f"Thread pool size (default: {MAX_WORKERS})")
    parser.add_argument("--no-cache", action="store_true", help="Ignore CDX cache")
    parser.add_argument("--scrape-only", action="store_true", help="Skip URL discovery, use cached urls.txt")
    parser.add_argument("--broad", action="store_true", help="Include all /health/* not just diet-fitness")
    args = parser.parse_args()

    # Phase 1: URL Discovery
    if args.scrape_only and os.path.exists(URLS_FILE):
        urls = load_urls(URLS_FILE)
        print(f"Loaded {len(urls)} URLs from {URLS_FILE}")
    else:
        urls = discover_urls_cdx(broad=args.broad)
        if urls:
            save_urls(URLS_FILE, urls)
            print(f"Saved {len(urls)} URLs to {URLS_FILE}")
        else:
            print("No URLs discovered. Exiting.")
            sys.exit(1)

    if args.limit > 0:
        urls = urls[:args.limit]
        print(f"Limit mode: processing first {len(urls)} URLs")

    cdx_cache = {} if args.no_cache else load_cdx_cache(CDX_CACHE_FILE)
    if cdx_cache:
        print(f"Loaded CDX cache with {len(cdx_cache)} entries")

    cdx_cache_lock = Lock()
    os.makedirs(ARTICLES_DIR, exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)

    stats = {"success": 0, "skipped": 0, "failed": 0, "total": len(urls)}
    results = []

    print(f"\nScraping {len(urls)} URLs with {args.workers} workers...")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {}
        for url in urls:
            f = executor.submit(
                scrape_single, url, cdx_cache, ARTICLES_DIR, RAW_DIR,
                cdx_cache_lock,
            )
            futures[f] = url

        for i, future in enumerate(as_completed(futures), 1):
            url = futures[future]
            try:
                result = future.result()
            except Exception as e:
                with _print_lock:
                    print(f"  [ERROR] Exception for {url}: {e}")
                result = {"source_id": "unknown", "status": "error"}

            if result:
                results.append(result)
                if result["status"] == "success":
                    stats["success"] += 1
                elif result["status"] == "skipped":
                    stats["skipped"] += 1
                else:
                    stats["failed"] += 1

            if i % 25 == 0 or i == len(urls):
                with _print_lock:
                    print(f"Progress: {i}/{len(urls)} "
                          f"(success={stats['success']}, skipped={stats['skipped']}, "
                          f"failed={stats['failed']})")

    save_cdx_cache(CDX_CACHE_FILE, cdx_cache)
    print(f"Saved CDX cache ({len(cdx_cache)} entries)")

    print("\n=== SCRAPE COMPLETE: today.com/health ===")
    print(f"Total URLs: {stats['total']}")
    print(f"Success: {stats['success']}")
    print(f"Skipped (existing): {stats['skipped']}")
    print(f"Failed: {stats['failed']}")


if __name__ == "__main__":
    main()
