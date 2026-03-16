#!/usr/bin/env python3
"""
Scrape Bodybuilding.com articles via the Wayback Machine.

The live site migrated to Shopify -- all /content/* URLs redirect to shop.
Articles only exist in Wayback Machine. This scraper:

1. Reads the URL+timestamp list from bb-article-urls-with-timestamps.txt
   (3,577 articles with pre-resolved Wayback timestamps)
2. Fetches raw HTML from Wayback (using id_ suffix for clean content)
3. Extracts article content from BBCMS article template
4. Extracts metadata from coreDataLayer JS object
5. Saves as articles/{slug}.md with YAML frontmatter

Usage:
    python3 scrape_all.py                  # Scrape all 3,577 URLs
    python3 scrape_all.py --limit 10       # Scrape first 10 URLs
    python3 scrape_all.py --workers 10     # 10 parallel workers (default)
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
URLS_FILE = os.path.join(SCRIPT_DIR, "bb-article-urls-with-timestamps.txt")

USER_AGENT = (
    "GymZilla-ContentScraper/1.0 "
    "(fitness research project; polite; contact@gymzillatribe.com)"
)

# Delay between Wayback requests (seconds)
REQUEST_DELAY = 1.0
RETRY_BACKOFF = 15
MAX_WORKERS = 5

# Stop words for tag generation
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
    "you", "much", "many", "best", "top", "get", "make", "like",
}

# Thread-safe print lock
_print_lock = Lock()


# ---------------------------------------------------------------------------
# URL Parsing
# ---------------------------------------------------------------------------


def load_urls_with_timestamps(filepath: str) -> list[tuple[str, str]]:
    """
    Load URLs with pre-resolved Wayback timestamps.

    File format: {timestamp} {url}
    Returns list of (timestamp, url) tuples.
    """
    entries = []
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Format: "20230204153430 https://www.bodybuilding.com/content/..."
            # Split on first space only (URL won't contain spaces)
            parts = line.split(None, 1)
            if len(parts) == 2:
                timestamp, url = parts
                # Validate timestamp is numeric
                if timestamp.isdigit():
                    entries.append((timestamp, url))
                else:
                    # Maybe reversed: url timestamp
                    entries.append((url, timestamp))
            else:
                # Single item -- treat as URL, will need CDX lookup
                entries.append(("", line))
    return entries


def extract_slug(url: str) -> str:
    """Extract the last non-empty path segment as slug, without .html extension."""
    path = urllib.parse.urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "unknown"
    slug = parts[-1]
    # Remove .html extension
    if slug.endswith(".html"):
        slug = slug[:-5]
    return slug


def make_source_id(slug: str) -> str:
    """Build source_id from slug."""
    return f"bb-{slug}"


# ---------------------------------------------------------------------------
# Wayback Machine
# ---------------------------------------------------------------------------


def build_wayback_url(timestamp: str, original_url: str) -> str:
    """Build a Wayback fetch URL with id_ suffix (no toolbar injection)."""
    return f"https://web.archive.org/web/{timestamp}id_/{original_url}"


def _make_request(url: str, timeout: int = 60) -> bytes | None:
    """Make an HTTP request with retry on 429/503. Handles gzip decompression."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                # Decompress gzip if needed
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


# ---------------------------------------------------------------------------
# Metadata Extraction (coreDataLayer)
# ---------------------------------------------------------------------------


def extract_core_data_layer(html: str) -> dict:
    """
    Extract metadata from the coreDataLayer JavaScript object.

    Pattern: coreDataLayer.bbArticleTitle = "value";
    """
    meta = {}
    # Match: coreDataLayer.keyName = "value";
    pattern = r'coreDataLayer\.(\w+)\s*=\s*"([^"]*)"'
    for match in re.finditer(pattern, html):
        key, value = match.group(1), match.group(2)
        meta[key] = html_unescape(value)
    return meta


def extract_og_meta(soup) -> dict:
    """Extract OpenGraph meta tags."""
    og = {}
    for tag in soup.find_all("meta", property=True):
        prop = tag.get("property", "")
        if prop.startswith("og:"):
            og[prop] = tag.get("content", "")
        elif prop.startswith("article:"):
            og[prop] = tag.get("content", "")
    return og


# ---------------------------------------------------------------------------
# Content Extraction
# ---------------------------------------------------------------------------


def extract_content(html: str, url: str) -> dict:
    """
    Extract title, body markdown, metadata from a BB.com article HTML page.

    Returns dict with keys: title, author, category, date_published,
    date_modified, body_md, description, image_url, read_time, page_type
    """
    soup = BeautifulSoup(html, "lxml")

    # 1. Extract coreDataLayer metadata
    cdl = extract_core_data_layer(html)

    # 2. Extract OG metadata
    og = extract_og_meta(soup)

    # 3. Title: prefer coreDataLayer, then OG, then h1, then <title>
    title = cdl.get("bbArticleTitle", "")
    if not title:
        title = og.get("og:title", "")
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

    # 4. Author
    author = cdl.get("bbContentAuthor", "")
    if not author:
        author_link = soup.find("link", rel="author")
        if author_link and author_link.get("href"):
            # Extract author name from URL slug
            author_slug = author_link["href"].rstrip("/").split("/")[-1]
            author = author_slug.replace("-", " ").title()
    if not author:
        author = og.get("article:author", "")

    # 5. Category, dates, etc. from coreDataLayer
    category = cdl.get("bbArticleCategory", "")
    date_published = cdl.get("bbContentPublishDate", "")
    date_modified = cdl.get("bbContentModifiedDate", "")
    read_time = cdl.get("bbReadTimeEstimate", "")
    page_type = cdl.get("bbPageSubType", "article")

    # 6. Description and image from OG
    description = og.get("og:description", "")
    image_url = og.get("og:image", "")

    # 7. Extract article body
    # Primary selector: the article content div
    body_el = soup.select_one(
        'div.BBCMS__content--article-content[itemprop="articleBody"]'
    )

    if not body_el:
        # Fallback: try just the class
        body_el = soup.select_one("div.BBCMS__content--article-content")

    if not body_el:
        # Broader fallback: find main content area
        for selector in ["main", "article", "[role='main']",
                         "[class*='article-content']", "[class*='content']"]:
            found = soup.select(selector)
            if found:
                # Pick the one with the most text
                best = max(found, key=lambda el: len(el.get_text(strip=True)))
                if len(best.get_text(strip=True)) > 100:
                    body_el = best
                    break

    if not body_el:
        body_el = soup.find("body")

    body_md = ""
    if body_el:
        # Remove comment sections, sidebars, ads before converting
        for sel in ["div.BBCMS__content--comments", "div.BBCMS__rsb",
                     "[class*='sidebar']", "[class*='comment']",
                     "[class*='ad-']", "[class*='social-share']",
                     "script", "style", "noscript", "iframe"]:
            try:
                for el in body_el.select(sel):
                    el.decompose()
            except Exception:
                pass

        body_html = str(body_el)
        body_md = md(body_html, heading_style="ATX", strip=["img"])
        # Clean up excessive whitespace
        body_md = re.sub(r"\n{3,}", "\n\n", body_md)
        body_md = body_md.strip()

    return {
        "title": html_unescape(title) if title else "",
        "author": author,
        "category": category,
        "date_published": date_published if date_published else None,
        "date_modified": date_modified if date_modified else None,
        "description": description,
        "image_url": image_url,
        "read_time": read_time,
        "page_type": page_type,
        "body_md": body_md,
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
    tags = []
    if category:
        tags.append(category)
    # Split slug on hyphens, filter stop words and short fragments
    slug_words = [w.lower() for w in slug.split("-") if len(w) > 2]
    slug_words = [w for w in slug_words if w not in STOP_WORDS]
    for word in slug_words[:8]:  # Limit to 8 slug-derived tags
        if word not in tags:
            tags.append(word)
    return tags


def build_frontmatter(
    source_id: str,
    source_url: str,
    title: str,
    author: str,
    category: str,
    date_published: str | None,
    date_modified: str | None,
    description: str,
    tags: list[str],
    wc: int,
    wayback_timestamp: str,
    image_url: str,
    page_type: str,
) -> str:
    """Build YAML frontmatter string."""
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    safe_desc = description.replace("\\", "\\\\").replace('"', '\\"')
    safe_author = author.replace("\\", "\\\\").replace('"', '\\"')
    tags_str = json.dumps(tags)

    lines = [
        "---",
        f'source_id: "{source_id}"',
        'source_domain: "bodybuilding.com"',
        f'source_url: "{source_url}"',
        f'title: "{safe_title}"',
        f'author: "{safe_author}"',
        f'date_published: {json.dumps(date_published)}',
        f'date_modified: {json.dumps(date_modified)}',
        f'description: "{safe_desc}"',
        f'category: "{category}"',
        f'page_type: "{page_type}"',
        f"tags: {tags_str}",
        'content_type: "training"',
        'source_tier: "tier2"',
        'source_category: "2_bodybuilding_training"',
        f"word_count: {wc}",
        f'image_url: {json.dumps(image_url if image_url else None)}',
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


# ---------------------------------------------------------------------------
# Scraping Pipeline
# ---------------------------------------------------------------------------


def scrape_single(
    timestamp: str,
    url: str,
    articles_dir: str,
    raw_dir: str,
) -> dict | None:
    """
    Scrape a single URL. Returns article metadata dict or None on failure.
    """
    slug = extract_slug(url)
    source_id = make_source_id(slug)

    # Resume support: skip if already scraped
    if already_exists(articles_dir, source_id):
        return {"source_id": source_id, "status": "skipped"}

    # Small delay to be polite to Wayback
    time.sleep(REQUEST_DELAY)

    # Fetch the page using pre-resolved timestamp
    wb_url = build_wayback_url(timestamp, url)
    html_bytes = _make_request(wb_url)
    if not html_bytes:
        with _print_lock:
            print(f"  [SKIP] Failed to fetch {url}")
        return {"source_id": source_id, "status": "fetch_failed"}

    html = html_bytes.decode("utf-8", errors="replace")

    # Save raw HTML
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
    tags = generate_tags(content["category"], slug)
    wc = word_count(content["body_md"])
    frontmatter = build_frontmatter(
        source_id=source_id,
        source_url=url,
        title=content["title"],
        author=content["author"],
        category=content["category"],
        date_published=content["date_published"],
        date_modified=content["date_modified"],
        description=content["description"],
        tags=tags,
        wc=wc,
        wayback_timestamp=timestamp,
        image_url=content["image_url"],
        page_type=content["page_type"],
    )

    # Save article markdown
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
        "category": content["category"],
        "author": content["author"],
        "timestamp": timestamp,
        "path": article_path,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Scrape Bodybuilding.com articles via Wayback Machine"
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Limit to first N URLs (0 = all)"
    )
    parser.add_argument(
        "--workers", type=int, default=MAX_WORKERS,
        help=f"Thread pool size (default: {MAX_WORKERS})"
    )
    args = parser.parse_args()

    # Load URLs with pre-resolved timestamps
    entries = load_urls_with_timestamps(URLS_FILE)
    print(f"Loaded {len(entries)} URL+timestamp entries from {URLS_FILE}")

    if args.limit > 0:
        entries = entries[:args.limit]
        print(f"Limit mode: processing first {len(entries)} URLs")

    # Ensure output dirs exist
    os.makedirs(ARTICLES_DIR, exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)

    # Process URLs
    stats = {"success": 0, "skipped": 0, "failed": 0, "total": len(entries)}
    results = []
    start_time = time.time()

    if args.workers > 1 and len(entries) > 1:
        # Parallel processing
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {}
            for timestamp, url in entries:
                f = executor.submit(
                    scrape_single, timestamp, url, ARTICLES_DIR, RAW_DIR,
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

                if i % 100 == 0 or i == len(entries):
                    elapsed = time.time() - start_time
                    rate = i / elapsed if elapsed > 0 else 0
                    with _print_lock:
                        print(
                            f"Progress: {i}/{len(entries)} "
                            f"(success={stats['success']}, skipped={stats['skipped']}, "
                            f"failed={stats['failed']}) "
                            f"[{rate:.1f} URLs/sec, {elapsed:.0f}s elapsed]"
                        )
    else:
        # Sequential processing
        for i, (timestamp, url) in enumerate(entries, 1):
            try:
                result = scrape_single(timestamp, url, ARTICLES_DIR, RAW_DIR)
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

            if i % 25 == 0 or i == len(entries):
                elapsed = time.time() - start_time
                print(
                    f"Progress: {i}/{len(entries)} "
                    f"(success={stats['success']}, skipped={stats['skipped']}, "
                    f"failed={stats['failed']}) [{elapsed:.0f}s elapsed]"
                )

    # Summary
    elapsed = time.time() - start_time
    print(f"\n=== SCRAPE COMPLETE ===")
    print(f"Total: {stats['total']}")
    print(f"Success: {stats['success']}")
    print(f"Skipped (existing): {stats['skipped']}")
    print(f"Failed: {stats['failed']}")
    print(f"Time: {elapsed:.1f}s ({elapsed/60:.1f} minutes)")
    if stats["success"] > 0:
        print(f"Rate: {stats['success']/elapsed:.1f} articles/sec")

    # Print a few details of successful scrapes
    successes = [r for r in results if r.get("status") == "success"]
    if successes:
        show = successes[:5]
        print(f"\n=== SAMPLE SUCCESSFUL SCRAPES ({len(show)} of {len(successes)}) ===")
        for r in show:
            print(
                f"  [{r.get('category', 'unknown')}] {r['title'][:60]} "
                f"({r['word_count']} words, by {r.get('author', 'unknown')[:30]})"
            )

    # Print failure breakdown
    failures = [r for r in results if r.get("status") not in ("success", "skipped")]
    if failures:
        from collections import Counter
        fail_types = Counter(r["status"] for r in failures)
        print(f"\n=== FAILURE BREAKDOWN ===")
        for ftype, count in fail_types.most_common():
            print(f"  {ftype}: {count}")


if __name__ == "__main__":
    main()
