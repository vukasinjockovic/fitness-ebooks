#!/usr/bin/env python3
"""
Scrape Renaissance Periodization (rpstrength.com) blog articles.

Fetches sitemap, extracts blog article/video/podcast URLs, downloads HTML,
extracts metadata from JSON-LD (BlogPosting) and body from
div.article-template__content.rte, converts to markdown with YAML frontmatter.

Platform: Shopify (server-rendered HTML, clean semantic markup)
Expected: ~371 articles

Usage:
    python3 scrape_all.py              # Scrape all articles
    python3 scrape_all.py --limit 50   # Scrape first 50
    python3 scrape_all.py --workers 5  # Use 5 parallel workers
"""

import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

SITEMAP_URL = "https://rpstrength.com/sitemap.xml"
SOURCE_DOMAIN = "rpstrength.com"
SOURCE_TIER = "tier1"

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

# Blog path patterns to include
BLOG_PATTERNS = re.compile(
    r"^https://rpstrength\.com/blogs/(articles|videos|podcasts)/[^/]+$"
)

# Index pages to exclude (no slug after category)
INDEX_PATTERN = re.compile(
    r"^https://rpstrength\.com/blogs/(articles|videos|podcasts)/?$"
)


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
# Sitemap Parsing
# ---------------------------------------------------------------------------

def find_blogs_sitemap_url(sitemap_xml: str) -> str | None:
    """Find the blogs sitemap URL in the sitemap index.

    Looks for a <loc> containing 'sitemap_blogs' in the sitemap index XML.

    Args:
        sitemap_xml: The sitemap index XML content.

    Returns:
        The URL of the blogs sitemap, or None if not found.
    """
    root = ET.fromstring(sitemap_xml)
    for sitemap in root.findall("sm:sitemap", NS):
        loc = sitemap.find("sm:loc", NS)
        if loc is not None and loc.text and "sitemap_blogs" in loc.text:
            return loc.text.strip()
    return None


def extract_article_urls(blogs_sitemap_xml: str) -> list[str]:
    """Extract blog article URLs from the blogs sitemap.

    Filters to only blog article/video/podcast URLs with actual slugs,
    excluding index pages and non-blog URLs.

    Args:
        blogs_sitemap_xml: The blogs sitemap XML content.

    Returns:
        List of article URLs.
    """
    root = ET.fromstring(blogs_sitemap_xml)
    urls = []
    for url_elem in root.findall("sm:url", NS):
        loc = url_elem.find("sm:loc", NS)
        if loc is not None and loc.text:
            url = loc.text.strip()
            # Must match blog pattern and NOT be an index page
            if BLOG_PATTERNS.match(url) and not INDEX_PATTERN.match(url):
                urls.append(url)
    return urls


# ---------------------------------------------------------------------------
# Metadata Extraction
# ---------------------------------------------------------------------------

def extract_metadata(html: str) -> dict:
    """Extract metadata from HTML, preferring JSON-LD BlogPosting.

    Looks for JSON-LD script blocks and extracts BlogPosting data.
    Falls back to h1 and meta tags if no JSON-LD is available.

    Args:
        html: The full HTML content of the page.

    Returns:
        Dict with keys: title, author, date_published, date_modified,
        image_url.
    """
    soup = BeautifulSoup(html, "lxml")
    meta = {
        "title": None,
        "author": None,
        "date_published": None,
        "date_modified": None,
        "image_url": None,
    }

    # Try JSON-LD first - look for BlogPosting type
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, dict) and data.get("@type") == "BlogPosting":
                meta["title"] = data.get("headline", meta["title"])
                author = data.get("author")
                if isinstance(author, dict):
                    meta["author"] = author.get("name")
                elif isinstance(author, str):
                    meta["author"] = author
                meta["date_published"] = data.get("datePublished")
                meta["date_modified"] = data.get("dateModified")
                meta["image_url"] = data.get("image")
                break
        except (json.JSONDecodeError, TypeError):
            continue

    # Fallback: h1 for title
    if not meta["title"]:
        h1 = soup.find("h1", class_="article-template__title")
        if h1:
            meta["title"] = h1.get_text(strip=True)
        else:
            h1 = soup.find("h1")
            if h1:
                meta["title"] = h1.get_text(strip=True)

    # Fallback: <title> tag
    if not meta["title"]:
        title_tag = soup.find("title")
        if title_tag:
            # Strip site name suffix
            meta["title"] = title_tag.get_text(strip=True).split(" - ")[0].strip()

    return meta


# ---------------------------------------------------------------------------
# Body Extraction
# ---------------------------------------------------------------------------

def extract_body_markdown(html: str) -> str:
    """Extract article body and convert to markdown.

    Looks for div.article-template__content.rte with itemprop="articleBody".

    Args:
        html: The full HTML content of the page.

    Returns:
        Markdown string of the article body, or empty string if not found.
    """
    soup = BeautifulSoup(html, "lxml")

    # Primary selector: div.article-template__content.rte
    content_div = soup.find("div", class_="article-template__content")
    if content_div is None:
        content_div = soup.find("div", attrs={"itemprop": "articleBody"})

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
    """Extract slug from RP blog URL.

    Args:
        url: Full URL like https://rpstrength.com/blogs/articles/my-slug

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

    # Build YAML frontmatter
    lines = [
        "---",
        f'source_id: "{slug}"',
        f"source_domain: {SOURCE_DOMAIN}",
        f'source_url: "{url}"',
        f'title: "{_yaml_escape(meta["title"] or slug)}"',
    ]

    if meta["author"]:
        lines.append(f'author: "{_yaml_escape(meta["author"])}"')

    if meta["date_published"]:
        lines.append(f'date_published: "{meta["date_published"]}"')

    if meta["date_modified"]:
        lines.append(f'date_modified: "{meta["date_modified"]}"')

    if meta["image_url"]:
        lines.append(f'image_url: "{meta["image_url"]}"')

    lines.append(f"source_tier: {SOURCE_TIER}")
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
    """Fetch a URL with retry support.

    Args:
        session: requests.Session to use.
        url: URL to fetch.
        retries: Number of retries on failure.

    Returns:
        HTML content or None on failure.
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

    parser = argparse.ArgumentParser(description="Scrape RP blog articles")
    parser.add_argument("--limit", type=int, default=0, help="Max articles (0=all)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Parallel workers")
    args = parser.parse_args()

    os.makedirs(ARTICLES_DIR, exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)

    session = _get_session()

    # Step 1: Fetch sitemap index
    print(f"Fetching sitemap from {SITEMAP_URL}...")
    sitemap_xml = fetch_url(session, SITEMAP_URL)
    if not sitemap_xml:
        print("ERROR: Could not fetch sitemap index", file=sys.stderr)
        sys.exit(1)

    # Step 2: Find blogs sitemap
    blogs_url = find_blogs_sitemap_url(sitemap_xml)
    if not blogs_url:
        print("ERROR: Could not find blogs sitemap in index", file=sys.stderr)
        sys.exit(1)
    print(f"Found blogs sitemap: {blogs_url}")

    # Step 3: Fetch blogs sitemap and extract URLs
    blogs_xml = fetch_url(session, blogs_url)
    if not blogs_xml:
        print("ERROR: Could not fetch blogs sitemap", file=sys.stderr)
        sys.exit(1)

    urls = extract_article_urls(blogs_xml)
    print(f"Found {len(urls)} article URLs")

    if args.limit > 0:
        urls = urls[:args.limit]
        print(f"Limited to {len(urls)} URLs")

    # Step 4: Scrape articles in parallel
    stats = Stats()
    print(f"Starting scrape with {args.workers} workers...")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(process_article, session, url, stats): url
            for url in urls
        }
        for i, future in enumerate(as_completed(futures), 1):
            future.result()  # propagate exceptions
            if i % PROGRESS_EVERY == 0:
                print(f"  [{i}/{len(urls)}] {stats.summary()}")

    # Step 5: Print summary
    print("\n" + "=" * 60)
    print("SCRAPE COMPLETE")
    print(stats.summary())
    print(f"Articles saved to: {ARTICLES_DIR}")
    print(f"Raw HTML saved to: {RAW_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
