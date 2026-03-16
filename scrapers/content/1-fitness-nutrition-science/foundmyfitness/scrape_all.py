#!/usr/bin/env python3
"""
Scrape FoundMyFitness (foundmyfitness.com) episodes and topics.

Fetches gzipped sitemaps from S3, extracts /episodes/* and /topics/* URLs,
downloads HTML, extracts metadata from HTML tags (no JSON-LD available),
converts body to markdown with YAML frontmatter.

Platform: Custom Rails app on Heroku
Expected: ~1,043 episodes + 70 topics = ~1,113 articles

Usage:
    python3 scrape_all.py              # Scrape all articles
    python3 scrape_all.py --limit 50   # Scrape first 50
    python3 scrape_all.py --workers 5  # Use 5 parallel workers
"""

import gzip
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup
from markdownify import markdownify as md

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")
RAW_DIR = os.path.join(SCRIPT_DIR, "raw")

SITEMAP_INDEX_URL = (
    "https://s3.amazonaws.com/foundmyfitness.production/sitemaps/sitemap.xml.gz"
)
SOURCE_DOMAIN = "foundmyfitness.com"
SOURCE_TIER = "tier2"
DEFAULT_AUTHOR = "Rhonda Patrick, Ph.D."

DEFAULT_WORKERS = 10
PROGRESS_EVERY = 25
REQUEST_TIMEOUT = 30
DELAY_PER_REQUEST = 1.0

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Sitemap XML namespace
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

# URL path patterns to keep
KEEP_PATTERNS = re.compile(r"^https?://(?:www\.)?foundmyfitness\.com/(episodes|topics)/[^/]+")

# Date patterns in page body text
DATE_PATTERNS = [
    # "Posted on March 15, 2019"
    re.compile(r"Posted on\s+(\w+ \d{1,2},?\s+\d{4})"),
    # "Published: March 15, 2019"
    re.compile(r"Published:?\s+(\w+ \d{1,2},?\s+\d{4})"),
    # "Mar 15, 2019"
    re.compile(r"(\w{3,9}\s+\d{1,2},?\s+\d{4})"),
]

DATE_FORMATS = [
    "%B %d, %Y",   # March 15, 2019
    "%B %d %Y",    # March 15 2019
    "%b %d, %Y",   # Mar 15, 2019
    "%b %d %Y",    # Mar 15 2019
]


# ---------------------------------------------------------------------------
# Thread-safe Stats
# ---------------------------------------------------------------------------

class Stats:
    """Thread-safe statistics tracker."""

    def __init__(self):
        self._lock = threading.Lock()
        self.success = 0
        self.failed = 0
        self.skipped = 0
        self._started = time.time()

    def record_success(self):
        with self._lock:
            self.success += 1

    def record_failure(self):
        with self._lock:
            self.failed += 1

    def record_skip(self):
        with self._lock:
            self.skipped += 1

    @property
    def total(self):
        with self._lock:
            return self.success + self.failed + self.skipped

    def summary(self) -> str:
        with self._lock:
            elapsed = time.time() - self._started
            rate = self.success / elapsed if elapsed > 0 else 0
            return (
                f"Success: {self.success} | Failed: {self.failed} | "
                f"Skipped: {self.skipped} | "
                f"Rate: {rate:.1f}/sec | Elapsed: {elapsed:.0f}s"
            )


# ---------------------------------------------------------------------------
# Sitemap Parsing (Gzipped)
# ---------------------------------------------------------------------------

def _safe_decompress(data: bytes) -> bytes:
    """Decompress gzip data, or return as-is if already plain XML."""
    try:
        return gzip.decompress(data)
    except gzip.BadGzipFile:
        return data


def parse_sitemap_index(gzipped_data: bytes) -> list[str]:
    """Decompress gzipped sitemap index and extract child sitemap URLs.

    Args:
        gzipped_data: Raw gzipped bytes of the sitemap index.

    Returns:
        List of child sitemap URLs.
    """
    xml_bytes = _safe_decompress(gzipped_data)
    root = ET.fromstring(xml_bytes)
    urls = []
    for sitemap in root.findall("sm:sitemap", NS):
        loc = sitemap.find("sm:loc", NS)
        if loc is not None and loc.text:
            urls.append(loc.text.strip())
    return urls


def extract_urls_from_gzipped_sitemap(gzipped_data: bytes) -> list[str]:
    """Decompress gzipped sitemap and extract all <loc> URLs.

    Args:
        gzipped_data: Raw gzipped bytes of a sitemap file.

    Returns:
        List of URLs from the sitemap.
    """
    xml_bytes = _safe_decompress(gzipped_data)
    root = ET.fromstring(xml_bytes)
    urls = []
    for url_elem in root.findall("sm:url", NS):
        loc = url_elem.find("sm:loc", NS)
        if loc is not None and loc.text:
            urls.append(loc.text.strip())
    return urls


def filter_content_urls(urls: list[str]) -> list[str]:
    """Filter URLs to only /episodes/* and /topics/*.

    Excludes /stories/*, /about, /login, and any other paths.

    Args:
        urls: List of all sitemap URLs.

    Returns:
        Filtered list containing only episode and topic URLs.
    """
    return [url for url in urls if KEEP_PATTERNS.match(url)]


# ---------------------------------------------------------------------------
# Metadata Extraction
# ---------------------------------------------------------------------------

def extract_metadata(html: str) -> dict:
    """Extract metadata from HTML using tags and body text.

    No JSON-LD is available on FoundMyFitness. Extracts:
    - Title from <h1> or <title>
    - Date from body text (e.g., "Posted on March 15, 2019")
    - Author hardcoded as Rhonda Patrick, Ph.D.
    - Description from meta tags
    - Image from og:image

    Args:
        html: The full HTML content of the page.

    Returns:
        Dict with keys: title, author, date_published, description, image_url.
    """
    soup = BeautifulSoup(html, "lxml")
    meta = {
        "title": None,
        "author": DEFAULT_AUTHOR,
        "date_published": None,
        "description": None,
        "image_url": None,
    }

    # Title: prefer h1, fall back to <title>
    h1 = soup.find("h1")
    if h1:
        meta["title"] = h1.get_text(strip=True)
    else:
        title_tag = soup.find("title")
        if title_tag:
            # Strip site name suffix
            raw = title_tag.get_text(strip=True)
            meta["title"] = raw.split(" - FoundMyFitness")[0].strip()

    # Date: parse from body text
    body_text = soup.get_text()
    for pattern in DATE_PATTERNS:
        match = pattern.search(body_text)
        if match:
            date_str = match.group(1).replace(",", ", ").strip()
            # Normalize multiple spaces
            date_str = re.sub(r"\s+", " ", date_str)
            # Remove trailing comma if present
            date_str = date_str.rstrip(",").strip()
            parsed_date = _try_parse_date(date_str)
            if parsed_date:
                meta["date_published"] = parsed_date.strftime("%Y-%m-%d")
                break

    # Description: meta description or og:description
    desc_tag = soup.find("meta", attrs={"name": "description"})
    if desc_tag and desc_tag.get("content"):
        meta["description"] = desc_tag["content"]
    else:
        og_desc = soup.find("meta", attrs={"property": "og:description"})
        if og_desc and og_desc.get("content"):
            meta["description"] = og_desc["content"]

    # Image: og:image
    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image and og_image.get("content"):
        meta["image_url"] = og_image["content"]

    return meta


def _try_parse_date(date_str: str) -> datetime | None:
    """Try parsing a date string with multiple format patterns.

    Args:
        date_str: Date string like "March 15, 2019".

    Returns:
        Parsed datetime or None.
    """
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Body Extraction
# ---------------------------------------------------------------------------

def extract_body_markdown(html: str) -> str:
    """Extract article body and convert to markdown.

    Looks for .summary-content or .truncated-summary container.

    Args:
        html: The full HTML content of the page.

    Returns:
        Markdown string of the article body, or empty string if not found.
    """
    soup = BeautifulSoup(html, "lxml")

    # Try .summary-content first, then .truncated-summary
    content_div = soup.find("div", class_="summary-content")
    if content_div is None:
        content_div = soup.find("div", class_="truncated-summary")

    if content_div is None:
        return ""

    # Convert to markdown
    markdown = md(str(content_div), heading_style="ATX", strip=["img"])
    # Clean up excessive whitespace
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    return markdown


# ---------------------------------------------------------------------------
# Slug Extraction
# ---------------------------------------------------------------------------

def url_to_slug(url: str) -> str:
    """Extract slug from FMF URL.

    Args:
        url: Full URL like https://www.foundmyfitness.com/episodes/my-slug

    Returns:
        The slug portion (e.g., 'my-slug').
    """
    path = urlparse(url).path.rstrip("/")
    return path.split("/")[-1]


# ---------------------------------------------------------------------------
# File Output
# ---------------------------------------------------------------------------

def build_markdown_file(html: str, url: str) -> str:
    """Build a complete markdown file with YAML frontmatter and body.

    Args:
        html: The full HTML of the article page.
        url: The canonical URL of the article.

    Returns:
        String with YAML frontmatter + markdown body.
    """
    meta = extract_metadata(html)
    body = extract_body_markdown(html)
    slug = url_to_slug(url)

    # Determine content type from URL path
    path = urlparse(url).path
    content_prefix = "episode" if "/episodes/" in path else "topic"

    lines = [
        "---",
        f'source_id: "{slug}"',
        f"source_domain: {SOURCE_DOMAIN}",
        f'source_url: "{url}"',
        f'title: "{_yaml_escape(meta["title"] or slug)}"',
        f'author: "{_yaml_escape(meta["author"])}"',
    ]

    if meta["date_published"]:
        lines.append(f'date_published: "{meta["date_published"]}"')

    if meta["description"]:
        lines.append(f'summary: "{_yaml_escape(meta["description"])}"')

    if meta["image_url"]:
        lines.append(f'image_url: "{meta["image_url"]}"')

    lines.append(f"source_tier: {SOURCE_TIER}")
    lines.append(f'content_prefix: "{content_prefix}"')
    lines.append("---")
    lines.append("")
    lines.append(body)

    return "\n".join(lines)


def _yaml_escape(s: str) -> str:
    """Escape double quotes in a YAML string value."""
    if s is None:
        return ""
    return s.replace('"', '\\"')


def is_already_scraped(slug: str, articles_dir: str = None) -> bool:
    """Check if an article has already been scraped.

    Args:
        slug: The article slug.
        articles_dir: Path to articles directory. Defaults to ARTICLES_DIR.

    Returns:
        True if the markdown file already exists.
    """
    if articles_dir is None:
        articles_dir = ARTICLES_DIR
    return os.path.isfile(os.path.join(articles_dir, f"{slug}.md"))


def save_article(
    html: str,
    url: str,
    slug: str,
    articles_dir: str = None,
    raw_dir: str = None,
) -> None:
    """Save article as markdown file and raw HTML.

    Args:
        html: The full HTML of the article page.
        url: The canonical URL.
        slug: The article slug for filename.
        articles_dir: Path to articles output directory.
        raw_dir: Path to raw HTML output directory.
    """
    if articles_dir is None:
        articles_dir = ARTICLES_DIR
    if raw_dir is None:
        raw_dir = RAW_DIR

    # Save raw HTML
    raw_path = os.path.join(raw_dir, f"{slug}.html")
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Save markdown
    md_content = build_markdown_file(html, url)
    md_path = os.path.join(articles_dir, f"{slug}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)


# ---------------------------------------------------------------------------
# HTTP Fetching
# ---------------------------------------------------------------------------

def _get_session() -> "requests.Session":
    """Create an HTTP session with the configured User-Agent."""
    import requests
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def fetch_url(session, url: str, retries: int = 1) -> str | None:
    """Fetch a URL with retry support (text response).

    Args:
        session: requests.Session to use.
        url: URL to fetch.
        retries: Number of retries on failure.

    Returns:
        Text content or None on failure.
    """
    for attempt in range(1 + retries):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
            else:
                print(f"  [ERROR] {url}: {e}", file=sys.stderr)
                return None


def fetch_binary(session, url: str, retries: int = 1) -> bytes | None:
    """Fetch a URL with retry support (binary response for gzipped files).

    Args:
        session: requests.Session to use.
        url: URL to fetch.
        retries: Number of retries on failure.

    Returns:
        Raw bytes or None on failure.
    """
    for attempt in range(1 + retries):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
            else:
                print(f"  [ERROR] {url}: {e}", file=sys.stderr)
                return None


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def process_article(
    session, url: str, stats: Stats,
    articles_dir: str = None, raw_dir: str = None,
) -> None:
    """Fetch and save a single article.

    Args:
        session: requests.Session to use.
        url: Article URL.
        stats: Stats tracker.
        articles_dir: Output directory for markdown files.
        raw_dir: Output directory for raw HTML files.
    """
    slug = url_to_slug(url)

    if is_already_scraped(slug, articles_dir):
        stats.record_skip()
        return

    html = fetch_url(session, url)
    if html is None:
        stats.record_failure()
        return

    try:
        save_article(html, url, slug, articles_dir, raw_dir)
        stats.record_success()
    except Exception as e:
        print(f"  [ERROR] Saving {slug}: {e}", file=sys.stderr)
        stats.record_failure()

    time.sleep(DELAY_PER_REQUEST)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """Main entry point for the scraper."""
    import argparse

    parser = argparse.ArgumentParser(description="Scrape FoundMyFitness articles")
    parser.add_argument("--limit", type=int, default=0, help="Max articles (0=all)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Parallel workers")
    args = parser.parse_args()

    os.makedirs(ARTICLES_DIR, exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)

    session = _get_session()

    # Step 1: Fetch gzipped sitemap index from S3
    print(f"Fetching sitemap index from {SITEMAP_INDEX_URL}...")
    index_gz = fetch_binary(session, SITEMAP_INDEX_URL)
    if not index_gz:
        print("ERROR: Could not fetch sitemap index", file=sys.stderr)
        sys.exit(1)

    # Step 2: Parse sitemap index to get child sitemap URLs
    child_sitemap_urls = parse_sitemap_index(index_gz)
    print(f"Found {len(child_sitemap_urls)} child sitemaps")

    # Step 3: Fetch each child sitemap and collect all URLs
    all_urls = []
    for sm_url in child_sitemap_urls:
        print(f"  Fetching {sm_url}...")
        sm_gz = fetch_binary(session, sm_url)
        if sm_gz:
            urls = extract_urls_from_gzipped_sitemap(sm_gz)
            all_urls.extend(urls)
            print(f"    Found {len(urls)} URLs")
        else:
            print(f"    FAILED to fetch {sm_url}", file=sys.stderr)

    print(f"Total URLs found: {len(all_urls)}")

    # Step 4: Filter to episodes and topics only
    content_urls = filter_content_urls(all_urls)
    print(f"Content URLs (episodes + topics): {len(content_urls)}")

    if args.limit > 0:
        content_urls = content_urls[:args.limit]
        print(f"Limited to {len(content_urls)} URLs")

    # Step 5: Scrape articles in parallel
    stats = Stats()
    print(f"Starting scrape with {args.workers} workers...")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(process_article, session, url, stats): url
            for url in content_urls
        }
        for i, future in enumerate(as_completed(futures), 1):
            future.result()  # propagate exceptions
            if i % PROGRESS_EVERY == 0:
                print(f"  [{i}/{len(content_urls)}] {stats.summary()}")

    # Step 6: Print summary
    print("\n" + "=" * 60)
    print("SCRAPE COMPLETE")
    print(stats.summary())
    print(f"Articles saved to: {ARTICLES_DIR}")
    print(f"Raw HTML saved to: {RAW_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
