#!/usr/bin/env python3
"""
Scrape verywellfit.com nutrition articles via the Wayback Machine.

Live site is Cloudflare-blocked. Wayback has ~43% capture rate for nutrition URLs.
Full JSON-LD with Recipe/Article schema available.

Usage:
    python3 scrape_all.py                  # Discover URLs + scrape all
    python3 scrape_all.py --limit 10       # Scrape first 10 URLs
    python3 scrape_all.py --scrape-only    # Skip CDX discovery, use cached URLs
    python3 scrape_all.py --workers 5      # Custom worker count
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

SOURCE_DOMAIN = "verywellfit.com"
SOURCE_TIER = "tier2"
SOURCE_CATEGORY = "3_nutrition_meal_planning"
CONTENT_TYPE = "article"

# Wayback settings
REQUEST_DELAY = 1.5
RETRY_BACKOFF = 15
MAX_WORKERS = 3

# CDX API patterns for nutrition content (exclude recipe-only pages)
CDX_PATTERNS = [
    "verywellfit.com/nutrition-*",
    "verywellfit.com/what-is-*",
    "verywellfit.com/how-to-*",
    "verywellfit.com/best-*",
    "verywellfit.com/healthy-*",
    "verywellfit.com/weight-*",
    "verywellfit.com/calories-*",
    "verywellfit.com/protein-*",
    "verywellfit.com/vitamin-*",
    "verywellfit.com/foods-*",
    "verywellfit.com/diet-*",
    "verywellfit.com/eating-*",
    "verywellfit.com/meal-*",
    "verywellfit.com/macros-*",
    "verywellfit.com/supplements-*",
    "verywellfit.com/fiber-*",
    "verywellfit.com/sugar-*",
    "verywellfit.com/carbs-*",
    "verywellfit.com/fat-*",
    "verywellfit.com/keto-*",
    "verywellfit.com/vegan-*",
    "verywellfit.com/vegetarian-*",
    "verywellfit.com/paleo-*",
    "verywellfit.com/mediterranean-*",
    "verywellfit.com/intermittent-*",
    "verywellfit.com/fasting-*",
]

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
    """Extract the last non-empty path segment as slug."""
    path = urllib.parse.urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "unknown"
    slug = parts[-1]
    # Remove numeric ID suffix (e.g., "healthy-eating-4589012" -> "healthy-eating")
    slug = re.sub(r"-\d{5,}$", "", slug)
    return slug


def make_source_id(slug: str) -> str:
    """Build source_id from slug."""
    return f"verywellfit-{slug}"


# ---------------------------------------------------------------------------
# CDX API - URL Discovery
# ---------------------------------------------------------------------------


def discover_urls_cdx() -> list[str]:
    """
    Use CDX API to discover all verywellfit.com article URLs.
    Returns deduplicated list of original URLs (by numeric article ID).
    """
    all_urls = {}  # article_id -> canonical_url

    print("Discovering URLs via CDX API...")
    cdx_url = (
        "http://web.archive.org/cdx/search/cdx?"
        "url=www.verywellfit.com/*&fl=original&collapse=urlkey"
        "&filter=statuscode:200&limit=0"
    )
    print("  Querying CDX (broad, may take 1-2 minutes)...")
    data = _make_request(cdx_url, timeout=300)
    if data:
        text = data.decode("utf-8", errors="replace")
        for line in text.strip().split("\n"):
            url = line.strip()
            if not url:
                continue
            # Strip query params for dedup
            url_clean = url.split("?")[0].split("#")[0]
            if _is_nutrition_article_url(url_clean):
                # Normalize
                url_clean = re.sub(r"^http://", "https://", url_clean)
                # Extract numeric ID for dedup
                match = re.search(r"-(\d{5,})$", urllib.parse.urlparse(url_clean).path.strip("/"))
                if match:
                    aid = match.group(1)
                    if aid not in all_urls:
                        all_urls[aid] = url_clean
        print(f"  Found {len(all_urls)} unique article URLs")

    return sorted(all_urls.values())


def _is_nutrition_article_url(url: str) -> bool:
    """Check if URL looks like a nutrition/health article (not recipe, not category page)."""
    path = urllib.parse.urlparse(url).path.strip("/")

    # Must have a path
    if not path:
        return False

    # Skip recipe URLs
    if "/recipe/" in path or "/recipes/" in path:
        return False

    # Skip category/tag index pages
    if path in ("", "nutrition", "fitness", "weight-loss", "food-and-cooking"):
        return False

    # Skip non-article paths
    skip_prefixes = (
        "recipe/", "recipes/", "author/", "about-us", "privacy",
        "terms", "sitemap", "search", "category/", "tag/",
        "thmb/", "img/", "css/", "js/",
    )
    for prefix in skip_prefixes:
        if path.startswith(prefix):
            return False

    # Must have the numeric ID suffix pattern (verywellfit standard)
    # e.g., "healthy-eating-tips-4589012"
    if re.search(r"-\d{5,}$", path):
        return True

    return False


# ---------------------------------------------------------------------------
# CDX Timestamp Lookup
# ---------------------------------------------------------------------------


def build_cdx_url(url: str) -> str:
    """Build a CDX API query URL for the given page URL."""
    params = urllib.parse.urlencode({
        "url": url,
        "output": "json",
        "fl": "timestamp,original,statuscode",
        "filter": "statuscode:200",
        "limit": -1,
    })
    return f"http://web.archive.org/cdx/search/cdx?{params}"


def parse_cdx_response(cdx_json: list) -> str | None:
    """Parse CDX JSON response and return the best (most recent) timestamp."""
    if not cdx_json or len(cdx_json) < 2:
        return None
    last_row = cdx_json[-1]
    return last_row[0]


def build_wayback_url(timestamp: str, original_url: str) -> str:
    """Build a Wayback fetch URL with id_ suffix (no toolbar injection)."""
    return f"https://web.archive.org/web/{timestamp}id_/{original_url}"


# ---------------------------------------------------------------------------
# HTTP Helpers
# ---------------------------------------------------------------------------


def _make_request(url: str, timeout: int = 30) -> bytes | None:
    """Make an HTTP request with retry on 429/503. Handles gzip decompression."""
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
    """Query CDX API for the best Wayback timestamp for a URL."""
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
    Extract title, body markdown, author, date, and JSON-LD from an HTML page.
    Verywellfit uses Dotdash Meredith CMS with rich JSON-LD.
    """
    soup = BeautifulSoup(html, "lxml")

    # Extract JSON-LD (may be multiple scripts)
    json_ld = None
    for ld_script in soup.find_all("script", type="application/ld+json"):
        if not ld_script.string:
            continue
        try:
            parsed = json.loads(ld_script.string)
            # Handle @graph pattern (Dotdash Meredith uses this)
            items = []
            if isinstance(parsed, dict):
                if "@graph" in parsed:
                    items = [i for i in parsed["@graph"] if isinstance(i, dict)]
                else:
                    items = [parsed]
            elif isinstance(parsed, list):
                items = [i for i in parsed if isinstance(i, dict)]

            for item in items:
                schema_type = str(item.get("@type", ""))
                # Accept Article, MedicalWebPage, WebPage with headline
                if "Article" in schema_type or "Page" in schema_type:
                    json_ld = item
                    break
                # Fallback: any item with headline
                if item.get("headline") and not json_ld:
                    json_ld = item
            if json_ld:
                break
        except (json.JSONDecodeError, TypeError):
            pass

    # Extract metadata from JSON-LD
    author = None
    date_published = None
    date_modified = None
    if json_ld:
        # Author
        author_data = json_ld.get("author")
        if isinstance(author_data, dict):
            author = author_data.get("name")
        elif isinstance(author_data, list) and author_data:
            author = author_data[0].get("name") if isinstance(author_data[0], dict) else str(author_data[0])
        # Dates
        date_published = json_ld.get("datePublished")
        date_modified = json_ld.get("dateModified")

    # Extract title
    title = None
    if json_ld:
        title = json_ld.get("headline")
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

    # Find main content FIRST (before removing elements that may be ancestors)
    # Dotdash Meredith has unusual DOM: <header> wraps <main> wraps <article>
    candidates = []
    for selector in [".mntl-sc-page", ".article-body-content", ".article__body",
                     ".article-body", "#article__body",
                     "article", "main", "[role='main']",
                     ".loc.article-content",
                     "#mntl-sc-page_1-0"]:
        for found in soup.select(selector):
            text_len = len(found.get_text(strip=True))
            if text_len > 50:
                candidates.append((text_len, found))

    content_el = None
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        content_el = candidates[0][1]

    if not content_el:
        content_el = soup.find("body")

    body_md = ""
    if content_el:
        # Clean the extracted content element (not the whole soup)
        for tag_name in ["nav", "script", "style", "noscript", "iframe", "svg"]:
            for el in content_el.find_all(tag_name):
                el.decompose()
        for selector in [".cookie-banner", ".modal", ".ad-unit"]:
            try:
                for el in content_el.select(selector):
                    el.decompose()
            except Exception:
                pass

        body_html = str(content_el)
        body_md = md(body_html, heading_style="ATX", strip=["img"])
        body_md = re.sub(r"\n{3,}", "\n\n", body_md)
        body_md = body_md.strip()

    return {
        "title": html_unescape(title) if title else "",
        "body_md": body_md,
        "json_ld": json_ld,
        "author": author,
        "date_published": date_published,
        "date_modified": date_modified,
    }


# ---------------------------------------------------------------------------
# Frontmatter & Tags
# ---------------------------------------------------------------------------


def word_count(text: str) -> int:
    """Count words in text."""
    if not text:
        return 0
    clean = re.sub(r"[*_#\[\]()>`~|]", " ", text)
    return len(clean.split())


def generate_tags(slug: str) -> list[str]:
    """Generate tags from slug keywords."""
    tags = ["nutrition"]
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
    """Build YAML frontmatter string."""
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
    """Check if an article file already exists (for resume support)."""
    return os.path.exists(os.path.join(articles_dir, f"{source_id}.md"))


def save_cdx_cache(cache_path: str, cache: dict) -> None:
    """Save CDX timestamp cache to JSON file."""
    with open(cache_path, "w") as f:
        json.dump(cache, f, indent=2)


def load_cdx_cache(cache_path: str) -> dict:
    """Load CDX timestamp cache from JSON file."""
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_urls(urls_file: str, urls: list[str]) -> None:
    """Save discovered URLs to text file."""
    with open(urls_file, "w") as f:
        for url in urls:
            f.write(url + "\n")


def load_urls(urls_file: str) -> list[str]:
    """Load URLs from text file."""
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
    """Scrape a single URL. Returns article metadata dict or None on failure."""
    slug = extract_slug(url)
    source_id = make_source_id(slug)

    # Resume support
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

    # Fetch the page
    time.sleep(REQUEST_DELAY)
    html_bytes = fetch_wayback_page(timestamp, url)
    if not html_bytes:
        with _print_lock:
            print(f"  [SKIP] Failed to fetch {url}")
        return {"source_id": source_id, "status": "fetch_failed"}

    html = html_bytes.decode("utf-8", errors="replace")

    # Save raw HTML
    os.makedirs(raw_dir, exist_ok=True)
    raw_path = os.path.join(raw_dir, f"{source_id}.html")
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Extract content
    content = extract_content(html, url)
    if not content["body_md"] or len(content["body_md"]) < 50:
        with _print_lock:
            print(f"  [SKIP] Empty/minimal content for {url}")
        return {"source_id": source_id, "status": "empty_content"}

    # Build article
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

    # Save article markdown
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
    parser = argparse.ArgumentParser(description="Scrape verywellfit.com via Wayback Machine")
    parser.add_argument("--limit", type=int, default=0, help="Limit to first N URLs (0 = all)")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help=f"Thread pool size (default: {MAX_WORKERS})")
    parser.add_argument("--no-cache", action="store_true", help="Ignore CDX cache")
    parser.add_argument("--scrape-only", action="store_true", help="Skip URL discovery, use cached urls.txt")
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

    # Apply --limit
    if args.limit > 0:
        urls = urls[:args.limit]
        print(f"Limit mode: processing first {len(urls)} URLs")

    # Load CDX cache
    cdx_cache = {} if args.no_cache else load_cdx_cache(CDX_CACHE_FILE)
    if cdx_cache:
        print(f"Loaded CDX cache with {len(cdx_cache)} entries")

    cdx_cache_lock = Lock()

    # Ensure output dirs exist
    os.makedirs(ARTICLES_DIR, exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)

    # Phase 2: Scrape
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

    # Save CDX cache
    save_cdx_cache(CDX_CACHE_FILE, cdx_cache)
    print(f"Saved CDX cache ({len(cdx_cache)} entries)")

    # Summary
    print("\n=== SCRAPE COMPLETE: verywellfit.com ===")
    print(f"Total URLs: {stats['total']}")
    print(f"Success: {stats['success']}")
    print(f"Skipped (existing): {stats['skipped']}")
    print(f"Failed: {stats['failed']}")


if __name__ == "__main__":
    main()
