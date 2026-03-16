#!/usr/bin/env python3
"""
Scrape Examine.com content via the Wayback Machine.

The live site blocks all requests (Vercel Security Checkpoint), but Wayback
Machine has full server-rendered content. This scraper:

1. Reads the URL list from examine-free-urls.txt (1,401 free-tier URLs)
2. Queries CDX API for best Wayback snapshot per URL
3. Fetches raw HTML from Wayback (using id_ suffix for clean content)
4. Extracts article content, converts to markdown
5. Saves as articles/{category}-{slug}.md with YAML frontmatter

Usage:
    python3 scrape_all.py                  # Scrape all 1,401 URLs
    python3 scrape_all.py --limit 10       # Scrape first 10 URLs
    python3 scrape_all.py --sample         # Scrape 1 from each major category
    python3 scrape_all.py --limit 5 --sample  # 5 diverse samples
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
from collections import defaultdict
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
URLS_FILE = os.path.join(SCRIPT_DIR, "examine-free-urls.txt")
CDX_CACHE_FILE = os.path.join(SCRIPT_DIR, "cdx_cache.json")

USER_AGENT = (
    "GymZilla-ContentScraper/1.0 "
    "(fitness research project; polite; contact@gymzillatribe.com)"
)

# Category mapping from URL path prefix
CATEGORY_MAP = {
    "supplements": "supplements",
    "faq": "faq",
    "conditions": "conditions",
    "pregnancy-lactation": "pregnancy",
    "articles": "articles",
    "guides": "guides",
    "outcomes": "outcomes",
    "foods": "foods",
    "diets": "diets",
    "other": "other",
}

# Stop words to exclude from auto-generated tags
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

# Delay between Wayback requests (seconds)
REQUEST_DELAY = 1.5
RETRY_BACKOFF = 10
MAX_WORKERS = 5

# Thread-safe print lock
_print_lock = Lock()


# ---------------------------------------------------------------------------
# URL Parsing
# ---------------------------------------------------------------------------


def extract_category(url: str) -> str:
    """Extract category from examine.com URL path prefix."""
    path = urllib.parse.urlparse(url).path.strip("/")
    parts = path.split("/")
    if not parts:
        return "other"
    prefix = parts[0]
    # Handle hyphenated prefixes like pregnancy-lactation
    return CATEGORY_MAP.get(prefix, "other")


def extract_slug(url: str) -> str:
    """Extract the last non-empty path segment as slug."""
    path = urllib.parse.urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p]
    return parts[-1] if parts else "unknown"


def make_source_id(category: str, slug: str) -> str:
    """Build source_id from category and slug."""
    return f"{category}-{slug}"


# ---------------------------------------------------------------------------
# CDX API
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
    """
    Parse CDX JSON response and return the best (most recent) timestamp.

    CDX returns: [["timestamp","original","statuscode"], ["20250815...", ...], ...]
    With limit=-1 we get the most recent. If multiple rows, take the last data row.
    """
    if not cdx_json or len(cdx_json) < 2:
        return None
    # Last data row (skip header at index 0)
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
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                # Decompress gzip if needed (Wayback sometimes serves gzipped
                # content even with id_ suffix)
                encoding = resp.getheader("Content-Encoding", "")
                if encoding == "gzip" or (data[:2] == b"\x1f\x8b"):
                    try:
                        data = gzip.decompress(data)
                    except (gzip.BadGzipFile, OSError):
                        pass  # Not actually gzip, use raw data
                return data
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt == 0:
                with _print_lock:
                    print(f"  [RETRY] HTTP {e.code} for {url[:80]}... waiting {RETRY_BACKOFF}s")
                time.sleep(RETRY_BACKOFF)
                continue
            if e.code == 404:
                return None
            with _print_lock:
                print(f"  [ERROR] HTTP {e.code} for {url[:80]}...")
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == 0:
                with _print_lock:
                    print(f"  [RETRY] {e} for {url[:80]}... waiting {RETRY_BACKOFF}s")
                time.sleep(RETRY_BACKOFF)
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
    Extract title, body markdown, and JSON-LD from an HTML page.

    Returns dict with keys: title, body_md, json_ld
    """
    soup = BeautifulSoup(html, "lxml")

    # Extract JSON-LD
    json_ld = None
    ld_script = soup.find("script", type="application/ld+json")
    if ld_script and ld_script.string:
        try:
            json_ld = json.loads(ld_script.string)
        except json.JSONDecodeError:
            pass

    # Extract title
    title = None
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
    if not title:
        title = extract_slug(url).replace("-", " ").title()

    # Remove unwanted elements before content extraction
    for tag_name in ["nav", "footer", "aside", "header", "script", "style",
                     "noscript", "iframe", "svg"]:
        for el in soup.find_all(tag_name):
            el.decompose()

    # Also remove common class-based navigation/footer/sidebar elements
    for selector in [
        "[class*='nav']", "[class*='footer']", "[class*='sidebar']",
        "[class*='cookie']", "[class*='banner']", "[class*='modal']",
        "[id*='nav']", "[id*='footer']", "[id*='sidebar']",
    ]:
        try:
            for el in soup.select(selector):
                el.decompose()
        except Exception:
            pass

    # Find main content container — pick the one with the most text
    # On examine.com, <article> is often the changelog sidebar while <main>
    # has the real page content, so we score all candidates and pick the best.
    candidates = []
    for selector in ["main", "article", "[role='main']",
                     "[class*='article-content']", "[class*='content']",
                     "[class*='article']", ".post-content", "#content"]:
        for found in soup.select(selector):
            text_len = len(found.get_text(strip=True))
            if text_len > 50:
                candidates.append((text_len, found))

    content_el = None
    if candidates:
        # Pick the candidate with the most text
        candidates.sort(key=lambda x: x[0], reverse=True)
        content_el = candidates[0][1]

    if not content_el:
        # Fallback: use body
        content_el = soup.find("body")

    body_md = ""
    if content_el:
        # Convert to markdown
        body_html = str(content_el)
        body_md = md(body_html, heading_style="ATX", strip=["img"])
        # Clean up excessive whitespace
        body_md = re.sub(r"\n{3,}", "\n\n", body_md)
        body_md = body_md.strip()

    return {
        "title": html_unescape(title) if title else "",
        "body_md": body_md,
        "json_ld": json_ld,
    }


# ---------------------------------------------------------------------------
# Frontmatter & Tags
# ---------------------------------------------------------------------------


def word_count(text: str) -> int:
    """Count words in text (strips markdown formatting)."""
    if not text:
        return 0
    clean = re.sub(r"[*_#\[\]()>`~|]", " ", text)
    return len(clean.split())


def generate_tags(category: str, slug: str) -> list[str]:
    """Generate tags from category and slug keywords."""
    tags = [category]
    # Split slug on hyphens, filter stop words and short fragments
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
    tags: list[str],
    word_count: int,
    wayback_timestamp: str,
) -> str:
    """Build YAML frontmatter string."""
    # Escape title for YAML (replace " with \\")
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    tags_str = json.dumps(tags)
    lines = [
        "---",
        f'source_id: "{source_id}"',
        'source_domain: "examine.com"',
        f'source_url: "{source_url}"',
        f'title: "{safe_title}"',
        'author: "Examine.com Editorial Team"',
        "date_published: null",
        f"tags: {tags_str}",
        'content_type: "science"',
        'source_tier: "tier1"',
        f"word_count: {word_count}",
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
    """Load CDX timestamp cache from JSON file. Returns empty dict if missing."""
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def load_urls(urls_file: str) -> list[str]:
    """Load URLs from the text file, one per line."""
    with open(urls_file, "r") as f:
        return [line.strip() for line in f if line.strip()]


# ---------------------------------------------------------------------------
# Sample Selection
# ---------------------------------------------------------------------------


def select_sample(urls: list[str], count: int = 5) -> list[str]:
    """
    Select a diverse sample of URLs, picking one from each category.

    Prioritizes: supplements, faq, conditions, guides, articles.
    Then fills remaining slots from other categories.
    """
    by_category = defaultdict(list)
    for url in urls:
        cat = extract_category(url)
        by_category[cat].append(url)

    # Priority order for sampling
    priority = [
        "supplements", "faq", "conditions", "guides", "articles",
        "foods", "diets", "outcomes", "pregnancy", "other",
    ]

    selected = []
    used_categories = set()

    # First pass: one from each priority category
    for cat in priority:
        if len(selected) >= count:
            break
        if cat in by_category and cat not in used_categories:
            selected.append(by_category[cat][0])
            used_categories.add(cat)

    # Second pass: fill remaining from any category
    if len(selected) < count:
        for cat in priority:
            if len(selected) >= count:
                break
            for url in by_category.get(cat, []):
                if url not in selected:
                    selected.append(url)
                    if len(selected) >= count:
                        break

    return selected[:count]


# ---------------------------------------------------------------------------
# Scraping Pipeline
# ---------------------------------------------------------------------------


def scrape_single(
    url: str,
    cdx_cache: dict,
    articles_dir: str,
    raw_dir: str,
    cdx_cache_path: str,
    cdx_cache_lock: Lock,
) -> dict | None:
    """
    Scrape a single URL. Returns article metadata dict or None on failure.
    """
    category = extract_category(url)
    slug = extract_slug(url)
    source_id = make_source_id(category, slug)

    # Resume support: skip if already scraped
    if already_exists(articles_dir, source_id):
        return {"source_id": source_id, "status": "skipped"}

    # Get CDX timestamp (from cache or API)
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
    tags = generate_tags(category, slug)
    wc = word_count(content["body_md"])
    frontmatter = build_frontmatter(
        source_id=source_id,
        source_url=url,
        title=content["title"],
        tags=tags,
        word_count=wc,
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

    return {
        "source_id": source_id,
        "status": "success",
        "title": content["title"],
        "word_count": wc,
        "category": category,
        "timestamp": timestamp,
        "path": article_path,
    }


def main():
    parser = argparse.ArgumentParser(description="Scrape Examine.com via Wayback Machine")
    parser.add_argument("--limit", type=int, default=0, help="Limit to first N URLs (0 = all)")
    parser.add_argument("--sample", action="store_true", help="Pick diverse samples across categories")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help=f"Thread pool size (default: {MAX_WORKERS})")
    parser.add_argument("--no-cache", action="store_true", help="Ignore CDX cache, re-fetch all timestamps")
    args = parser.parse_args()

    # Load URLs
    urls = load_urls(URLS_FILE)
    print(f"Loaded {len(urls)} URLs from {URLS_FILE}")

    # Apply --sample and --limit
    if args.sample:
        count = args.limit if args.limit > 0 else 5
        urls = select_sample(urls, count=count)
        print(f"Sample mode: selected {len(urls)} URLs across categories")
    elif args.limit > 0:
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

    # Process URLs
    stats = {"success": 0, "skipped": 0, "failed": 0, "total": len(urls)}
    results = []

    if args.workers > 1 and len(urls) > 1:
        # Parallel processing
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {}
            for url in urls:
                f = executor.submit(
                    scrape_single, url, cdx_cache, ARTICLES_DIR, RAW_DIR,
                    CDX_CACHE_FILE, cdx_cache_lock,
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
    else:
        # Sequential processing (single URL or --workers=1)
        for i, url in enumerate(urls, 1):
            try:
                result = scrape_single(
                    url, cdx_cache, ARTICLES_DIR, RAW_DIR,
                    CDX_CACHE_FILE, cdx_cache_lock,
                )
            except Exception as e:
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
                print(f"Progress: {i}/{len(urls)} "
                      f"(success={stats['success']}, skipped={stats['skipped']}, "
                      f"failed={stats['failed']})")

    # Save CDX cache
    save_cdx_cache(CDX_CACHE_FILE, cdx_cache)
    print(f"Saved CDX cache ({len(cdx_cache)} entries) to {CDX_CACHE_FILE}")

    # Summary
    print("\n=== SCRAPE COMPLETE ===")
    print(f"Total: {stats['total']}")
    print(f"Success: {stats['success']}")
    print(f"Skipped (existing): {stats['skipped']}")
    print(f"Failed: {stats['failed']}")

    # Print details of successful scrapes
    successes = [r for r in results if r.get("status") == "success"]
    if successes:
        print(f"\n=== SUCCESSFUL SCRAPES ({len(successes)}) ===")
        for r in successes:
            print(f"  [{r['category']}] {r['title'][:60]} "
                  f"({r['word_count']} words, ts={r['timestamp']})")
            # Print first 500 chars of the file
            if os.path.exists(r["path"]):
                with open(r["path"], "r") as f:
                    content = f.read()
                print(f"  --- First 500 chars ---")
                print(f"  {content[:500]}")
                print(f"  --- End preview ---\n")


if __name__ == "__main__":
    main()
