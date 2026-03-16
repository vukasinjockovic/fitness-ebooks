#!/usr/bin/env python3
"""
Scrape eatingwell.com editorial articles via the Wayback Machine.

Cloudflare blocked live, explicitly blocks AI crawlers.
Two URL eras:
  - Old: /article/{numeric_id}/{slug}/   (~10,000 articles)
  - New (2023+): /{slug}-{7-digit-id}    (~5,500 articles)
Excludes /recipe/* (already scraped separately).

This is the biggest Wayback job -- uses 10 parallel workers, 0.5s delay.

Usage:
    python3 scrape_all.py                  # Discover URLs + scrape all
    python3 scrape_all.py --limit 100      # Scrape first 100 URLs
    python3 scrape_all.py --scrape-only    # Skip discovery, use cached urls.txt
    python3 scrape_all.py --workers 5      # Fewer workers
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

SOURCE_DOMAIN = "eatingwell.com"
SOURCE_TIER = "tier2"
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
    """Extract slug from eatingwell URL. Handles both old and new format."""
    path = urllib.parse.urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "unknown"

    # Old format: /article/{id}/{slug}
    if parts[0] == "article" and len(parts) >= 3:
        return parts[2]  # The slug after the numeric ID

    # New format: /{slug}-{7digitid}
    slug = parts[-1]
    slug = re.sub(r"-\d{7,}$", "", slug)
    return slug


def extract_article_id(url: str) -> str | None:
    """Extract unique article ID for dedup."""
    path = urllib.parse.urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p]

    # Old format: /article/{numeric_id}/...
    if parts and parts[0] == "article" and len(parts) >= 2:
        try:
            return f"old-{parts[1]}"
        except (ValueError, IndexError):
            pass

    # New format: /{slug}-{7digitid}
    match = re.search(r"-(\d{7,})$", parts[-1] if parts else "")
    if match:
        return f"new-{match.group(1)}"

    return None


def make_source_id(slug: str) -> str:
    return f"eatingwell-{slug}"


# ---------------------------------------------------------------------------
# CDX API - URL Discovery
# ---------------------------------------------------------------------------


def discover_urls_cdx() -> list[str]:
    """
    Use CDX API to discover eatingwell.com article URLs.
    Queries both old (/article/*) and new format URLs.
    Returns deduplicated list.
    """
    all_urls = {}  # article_id -> url for dedup

    # Query 1: Old format /article/*
    print("Discovering URLs via CDX API...")
    print("  Phase 1: Old format (/article/*)...")
    cdx_url = (
        "http://web.archive.org/cdx/search/cdx?"
        "url=www.eatingwell.com/article/*&fl=original&collapse=urlkey"
        "&filter=statuscode:200&limit=0"
    )
    data = _make_request(cdx_url, timeout=300)
    old_count = 0
    if data:
        text = data.decode("utf-8", errors="replace")
        for line in text.strip().split("\n"):
            url = line.strip()
            if not url:
                continue
            url_clean = url.split("?")[0].split("#")[0]
            if _is_article_url(url_clean):
                url_clean = re.sub(r"^http://", "https://", url_clean)
                # Remove port numbers
                url_clean = re.sub(r":80/", "/", url_clean)
                aid = extract_article_id(url_clean)
                if aid and aid not in all_urls:
                    all_urls[aid] = url_clean
                    old_count += 1
    print(f"    Found {old_count} old-format article URLs")

    # Query 2: New format (broad, then filter)
    print("  Phase 2: New format (broad CDX query)...")
    cdx_url2 = (
        "http://web.archive.org/cdx/search/cdx?"
        "url=www.eatingwell.com/*&fl=original&collapse=urlkey"
        "&filter=statuscode:200&limit=0"
    )
    data2 = _make_request(cdx_url2, timeout=600)
    new_count = 0
    if data2:
        text = data2.decode("utf-8", errors="replace")
        for line in text.strip().split("\n"):
            url = line.strip()
            if not url:
                continue
            url_clean = url.split("?")[0].split("#")[0]
            url_clean = re.sub(r":80/", "/", url_clean)
            if _is_new_format_article(url_clean):
                url_clean = re.sub(r"^http://", "https://", url_clean)
                aid = extract_article_id(url_clean)
                if aid and aid not in all_urls:
                    all_urls[aid] = url_clean
                    new_count += 1
    print(f"    Found {new_count} new-format article URLs")

    urls = sorted(all_urls.values())
    print(f"  Total unique article URLs: {len(urls)}")
    return urls


def _is_article_url(url: str) -> bool:
    """Check if old-format URL is a valid article (/article/{id}/{slug})."""
    path = urllib.parse.urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p]

    if not parts or parts[0] != "article":
        return False
    if len(parts) < 2:
        return False

    # parts[1] should be numeric ID
    if not parts[1].isdigit():
        return False

    # parts[2] is the slug (optional -- some URLs are /article/{id}/)
    if len(parts) >= 3:
        slug = parts[2]
        if re.search(r"\.(jpg|jpeg|png|gif|css|js|xml)$", slug, re.I):
            return False

    return True


def _is_new_format_article(url: str) -> bool:
    """Check if URL is a new-format eatingwell article."""
    path = urllib.parse.urlparse(url).path.strip("/")

    # Must not be in excluded paths
    skip_prefixes = (
        "recipe/", "recipes/", "article/", "gallery/", "author/",
        "about-us", "privacy", "terms", "sitemap", "search",
        "category/", "tag/", "thmb/", "img/", "css/", "js/",
        "news/", "video/", "slideshow/",
    )
    for prefix in skip_prefixes:
        if path.startswith(prefix):
            return False

    # Must not be a top-level section page
    if "/" not in path and not re.search(r"-\d{7,}$", path):
        return False

    # Must have the numeric ID suffix
    if re.search(r"-\d{7,}$", path):
        return True

    return False


# ---------------------------------------------------------------------------
# CDX Timestamp & HTTP
# ---------------------------------------------------------------------------


def build_cdx_url_ts(url: str) -> str:
    params = urllib.parse.urlencode({
        "url": url,
        "output": "json",
        "fl": "timestamp,original,statuscode",
        "filter": "statuscode:200",
        "limit": -1,
    })
    return f"http://web.archive.org/cdx/search/cdx?{params}"


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
    cdx_url = build_cdx_url_ts(url)
    data = _make_request(cdx_url)
    if not data:
        return None
    try:
        cdx_json = json.loads(data)
        if not cdx_json or len(cdx_json) < 2:
            return None
        return cdx_json[-1][0]
    except (json.JSONDecodeError, IndexError, KeyError):
        return None


def build_wayback_url(timestamp: str, original_url: str) -> str:
    return f"https://web.archive.org/web/{timestamp}id_/{original_url}"


def fetch_wayback_page(timestamp: str, url: str) -> bytes | None:
    wb_url = build_wayback_url(timestamp, url)
    return _make_request(wb_url, timeout=60)


# ---------------------------------------------------------------------------
# Content Extraction
# ---------------------------------------------------------------------------


def extract_content(html: str, url: str) -> dict:
    """Extract content from eatingwell.com article. Dotdash Meredith CMS."""
    soup = BeautifulSoup(html, "lxml")

    # JSON-LD
    json_ld = None
    author = None
    date_published = None

    for ld_script in soup.find_all("script", type="application/ld+json"):
        if not ld_script.string:
            continue
        try:
            parsed = json.loads(ld_script.string)
            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                if not isinstance(item, dict):
                    continue
                schema_type = str(item.get("@type", ""))
                if "Article" in schema_type or "NewsArticle" in schema_type:
                    json_ld = item
                    author_data = item.get("author")
                    if isinstance(author_data, dict):
                        author = author_data.get("name")
                    elif isinstance(author_data, list) and author_data:
                        first = author_data[0]
                        if isinstance(first, dict):
                            author = first.get("name")
                        elif isinstance(first, str):
                            author = first
                    date_published = item.get("datePublished")
                    break
        except json.JSONDecodeError:
            pass

    # Title
    title = None
    if json_ld:
        title = json_ld.get("headline")
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
    if not title:
        og_title = soup.find("meta", property="og:title")
        if og_title:
            title = og_title.get("content", "")
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
    if not title:
        title = extract_slug(url).replace("-", " ").title()

    # Find content FIRST before removing elements (Dotdash Meredith has unusual DOM)
    candidates = []
    for selector in [".mntl-sc-page", ".article__body", ".article-body",
                     ".article-content", ".loc.article-content",
                     "article", "main", "[role='main']",
                     "#article__body"]:
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

    body_md = ""
    if content_el:
        # Clean within extracted element only
        for tag_name in ["nav", "script", "style", "noscript", "iframe", "svg"]:
            for el in content_el.find_all(tag_name):
                el.decompose()
        body_md = md(str(content_el), heading_style="ATX", strip=["img"])
        body_md = re.sub(r"\n{3,}", "\n\n", body_md)
        body_md = body_md.strip()

    return {
        "title": html_unescape(title) if title else "",
        "body_md": body_md,
        "json_ld": json_ld,
        "author": author,
        "date_published": date_published,
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
    tags = ["nutrition", "healthy-eating"]
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
    source_id = make_source_id(slug)

    if already_exists(articles_dir, source_id):
        return {"source_id": source_id, "status": "skipped"}

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

    time.sleep(REQUEST_DELAY)
    html_bytes = fetch_wayback_page(timestamp, url)
    if not html_bytes:
        with _print_lock:
            print(f"  [SKIP] Failed to fetch {url}")
        return {"source_id": source_id, "status": "fetch_failed"}

    html = html_bytes.decode("utf-8", errors="replace")

    os.makedirs(raw_dir, exist_ok=True)
    raw_path = os.path.join(raw_dir, f"{source_id}.html")
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(html)

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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Scrape eatingwell.com articles via Wayback Machine")
    parser.add_argument("--limit", type=int, default=0, help="Limit to first N URLs (0 = all)")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help=f"Thread pool size (default: {MAX_WORKERS})")
    parser.add_argument("--no-cache", action="store_true", help="Ignore CDX cache")
    parser.add_argument("--scrape-only", action="store_true", help="Skip discovery, use cached urls.txt")
    args = parser.parse_args()

    # Phase 1: URL Discovery
    if args.scrape_only and os.path.exists(URLS_FILE):
        urls = load_urls(URLS_FILE)
        print(f"Loaded {len(urls)} URLs from {URLS_FILE}")
    else:
        urls = discover_urls_cdx()
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

            if i % 50 == 0 or i == len(urls):
                with _print_lock:
                    print(f"Progress: {i}/{len(urls)} "
                          f"(success={stats['success']}, skipped={stats['skipped']}, "
                          f"failed={stats['failed']})")

                # Periodic cache save every 200 URLs
                if i % 200 == 0:
                    save_cdx_cache(CDX_CACHE_FILE, cdx_cache)

    save_cdx_cache(CDX_CACHE_FILE, cdx_cache)
    print(f"Saved CDX cache ({len(cdx_cache)} entries)")

    print(f"\n=== SCRAPE COMPLETE: eatingwell.com articles ===")
    print(f"Total URLs: {stats['total']}")
    print(f"Success: {stats['success']}")
    print(f"Skipped (existing): {stats['skipped']}")
    print(f"Failed: {stats['failed']}")


if __name__ == "__main__":
    main()
