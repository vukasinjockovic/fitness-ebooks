#!/usr/bin/env python3
"""
Scrape peterattiamd.com articles via HTML scraping (WP REST API returns 401).

Fetches post URLs from sitemap_index.xml -> post-sitemap.xml, then scrapes
each page's HTML. Extracts metadata from JSON-LD (Yoast), body from
div.entry-content, converts to markdown with YAML frontmatter.

Uses 10 parallel workers with Tor proxies (ports 61000-61099) if available,
otherwise direct connections. 0.5s delay per request.

Usage:
    python3 scrape_all.py              # Scrape all posts
    python3 scrape_all.py --limit 50   # Scrape first 50
    python3 scrape_all.py --workers 5  # Use 5 workers
    python3 scrape_all.py --no-tor     # Skip Tor, direct only
"""

import argparse
import itertools
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")
RAW_DIR = os.path.join(SCRIPT_DIR, "raw")
ERROR_LOG = os.path.join(SCRIPT_DIR, "scrape_errors.log")

# ---------------------------------------------------------------------------
# Site configuration
# ---------------------------------------------------------------------------

SITEMAP_INDEX_URL = "https://peterattiamd.com/sitemap_index.xml"
SOURCE_DOMAIN = "peterattiamd.com"
SOURCE_TIER = "tier2"

# Tor proxy configuration
TOR_PORT_START = 61000
TOR_PORT_END = 61099
TOR_HOST = "127.0.0.1"

# Performance tuning
DEFAULT_WORKERS = 10
PROGRESS_EVERY = 25
REQUEST_TIMEOUT = 30
DELAY_PER_REQUEST = 0.5

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# XML namespace used in sitemaps
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


# ---------------------------------------------------------------------------
# Tor proxy helpers
# ---------------------------------------------------------------------------

def check_tor_available() -> bool:
    """Check if at least one Tor proxy is listening."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        sock.connect((TOR_HOST, TOR_PORT_START))
        sock.close()
        return True
    except (ConnectionRefusedError, OSError):
        return False


def make_session(proxy_port: int | None = None) -> requests.Session:
    """Create a requests Session, optionally with a Tor SOCKS5 proxy."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    if proxy_port is not None:
        proxy_url = f"socks5h://{TOR_HOST}:{proxy_port}"
        session.proxies = {"http": proxy_url, "https": proxy_url}
    return session


# ---------------------------------------------------------------------------
# Sitemap parsing
# ---------------------------------------------------------------------------

def fetch_sitemap_index(session: requests.Session) -> list[str]:
    """Fetch the sitemap index and return child sitemap URLs."""
    resp = session.get(SITEMAP_INDEX_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.content)
    urls = []
    for sitemap in root.findall("sm:sitemap", SITEMAP_NS):
        loc = sitemap.find("sm:loc", SITEMAP_NS)
        if loc is not None and loc.text:
            urls.append(loc.text.strip())
    return urls


def fetch_sitemap_urls(session: requests.Session, sitemap_url: str) -> list[str]:
    """Fetch a sitemap XML and return all <loc> URLs."""
    resp = session.get(sitemap_url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.content)
    urls = []
    for url_elem in root.findall("sm:url", SITEMAP_NS):
        loc = url_elem.find("sm:loc", SITEMAP_NS)
        if loc is not None and loc.text:
            urls.append(loc.text.strip())
    return urls


def get_post_urls(session: requests.Session) -> list[str]:
    """Get all post URLs from the sitemap index -> post-sitemap.xml."""
    print(f"Fetching sitemap index: {SITEMAP_INDEX_URL}")
    child_sitemaps = fetch_sitemap_index(session)
    print(f"  Found {len(child_sitemaps)} child sitemaps")

    post_urls = []
    for sitemap_url in child_sitemaps:
        # Only process post sitemaps
        if "post-sitemap" in sitemap_url:
            print(f"  Fetching: {sitemap_url}")
            urls = fetch_sitemap_urls(session, sitemap_url)
            post_urls.extend(urls)
            print(f"    -> {len(urls)} URLs")

    print(f"Total post URLs: {len(post_urls)}")
    return post_urls


# ---------------------------------------------------------------------------
# Slug extraction
# ---------------------------------------------------------------------------

def url_to_slug(url: str) -> str:
    """Extract slug from URL path (e.g., https://peterattiamd.com/my-post/ -> my-post)."""
    path = urlparse(url).path.strip("/")
    # Take the last path segment
    parts = path.split("/")
    slug = parts[-1] if parts else "unknown"
    # Clean up any remaining special characters
    slug = re.sub(r"[^\w\-]", "-", slug).strip("-")
    return slug if slug else "unknown"


# ---------------------------------------------------------------------------
# HTML fetching with retry
# ---------------------------------------------------------------------------

def fetch_page(url: str, proxy_port: int | None, retry_port: int | None) -> str | None:
    """Fetch a page with one retry on failure.

    Returns HTML string or None.
    """
    for attempt, port in enumerate([proxy_port, retry_port]):
        if attempt > 0:
            time.sleep(5)  # backoff before retry
        session = make_session(port)
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if resp.status_code == 404:
                return None
            if resp.status_code in (429, 500, 502, 503, 504):
                # Retry on rate limit or server error
                continue
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.HTTPError:
            if attempt == 0:
                continue
            return None
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                requests.exceptions.ProxyError, OSError):
            if attempt == 0:
                continue
            return None
        finally:
            session.close()
    return None


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------

def extract_jsonld(html: str) -> dict | None:
    """Extract the Article/BlogPosting JSON-LD from Yoast schema.

    Yoast typically embeds a @graph array with multiple types.
    We look for Article, BlogPosting, or NewsArticle.
    """
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue

        # Direct object
        if isinstance(data, dict):
            if _is_article(data):
                return data
            # Yoast @graph
            if "@graph" in data:
                for item in data["@graph"]:
                    if isinstance(item, dict) and _is_article(item):
                        return item
        # Array
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and _is_article(item):
                    return item

    return None


def _is_article(data: dict) -> bool:
    """Check if JSON-LD @type indicates an article."""
    type_val = data.get("@type", "")
    article_types = {"Article", "BlogPosting", "NewsArticle", "WebPage"}
    if isinstance(type_val, str):
        return type_val in article_types
    if isinstance(type_val, list):
        return bool(set(type_val) & article_types)
    return False


def extract_metadata(jsonld: dict | None, soup: BeautifulSoup) -> dict:
    """Extract metadata from JSON-LD and/or meta tags.

    Returns a dict with: title, author, date_published, date_modified,
    tags, image_url, content_type.
    """
    meta = {
        "title": "",
        "author": "",
        "date_published": "",
        "date_modified": "",
        "tags": [],
        "image_url": "",
        "content_type": "science",
    }

    # Prefer JSON-LD metadata
    if jsonld:
        meta["title"] = jsonld.get("headline", "") or jsonld.get("name", "")

        # Author can be nested
        author = jsonld.get("author", {})
        if isinstance(author, dict):
            meta["author"] = author.get("name", "")
        elif isinstance(author, list) and author:
            meta["author"] = author[0].get("name", "") if isinstance(author[0], dict) else str(author[0])
        elif isinstance(author, str):
            meta["author"] = author

        meta["date_published"] = _extract_date(jsonld.get("datePublished", ""))
        meta["date_modified"] = _extract_date(jsonld.get("dateModified", ""))

        # Keywords from JSON-LD
        keywords = jsonld.get("keywords", [])
        if isinstance(keywords, str):
            meta["tags"] = [k.strip() for k in keywords.split(",") if k.strip()]
        elif isinstance(keywords, list):
            meta["tags"] = [str(k).strip() for k in keywords if k]

        # Article section
        section = jsonld.get("articleSection", "")
        if isinstance(section, list) and section:
            meta["tags"] = list(set(meta["tags"] + [str(s) for s in section]))

        # Image
        image = jsonld.get("image", {})
        if isinstance(image, dict):
            meta["image_url"] = image.get("url", "") or image.get("@id", "")
        elif isinstance(image, str):
            meta["image_url"] = image
        elif isinstance(image, list) and image:
            first = image[0]
            meta["image_url"] = first.get("url", "") if isinstance(first, dict) else str(first)

    # Fallback to meta tags
    if not meta["title"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            meta["title"] = og_title["content"]
        else:
            title_tag = soup.find("title")
            if title_tag:
                meta["title"] = title_tag.get_text(strip=True)

    if not meta["author"]:
        author_meta = soup.find("meta", attrs={"name": "author"})
        if author_meta and author_meta.get("content"):
            meta["author"] = author_meta["content"]

    if not meta["date_published"]:
        pub_meta = soup.find("meta", property="article:published_time")
        if pub_meta and pub_meta.get("content"):
            meta["date_published"] = _extract_date(pub_meta["content"])

    if not meta["date_modified"]:
        mod_meta = soup.find("meta", property="article:modified_time")
        if mod_meta and mod_meta.get("content"):
            meta["date_modified"] = _extract_date(mod_meta["content"])

    if not meta["image_url"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            meta["image_url"] = og_image["content"]

    return meta


def _extract_date(raw: str) -> str:
    """Normalize a date string to YYYY-MM-DD if possible."""
    if not raw:
        return ""
    # ISO format: 2024-06-15T10:00:00+00:00 -> 2024-06-15
    match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if match:
        return match.group(1)
    return raw


def extract_body(soup: BeautifulSoup) -> tuple[str, bool]:
    """Extract body HTML from div.entry-content.

    Returns (markdown_body, is_paywalled).
    """
    is_paywalled = False

    # Check for paywall indicator
    paywall_div = soup.find(class_="wp-block-memberpress-protected-content")
    if paywall_div:
        is_paywalled = True

    content_div = soup.find("div", class_="entry-content")
    if not content_div:
        return "", is_paywalled

    # Remove unwanted elements before conversion
    for unwanted in content_div.find_all(["script", "style", "noscript", "iframe"]):
        unwanted.decompose()

    # Remove social sharing, newsletter signup, related posts
    for cls in ["sharedaddy", "sd-sharing", "jp-relatedposts",
                "newsletter-signup", "wp-block-buttons",
                "mepr-unauthorized-excerpt"]:
        for el in content_div.find_all(class_=cls):
            el.decompose()

    body_html = str(content_div)
    body_md = md(body_html, heading_style="ATX", strip=["img"])

    # Clean up excessive whitespace
    body_md = re.sub(r"\n{3,}", "\n\n", body_md)
    body_md = body_md.strip()

    return body_md, is_paywalled


# ---------------------------------------------------------------------------
# Markdown file output
# ---------------------------------------------------------------------------

def build_frontmatter(slug: str, url: str, meta: dict, is_paywalled: bool) -> str:
    """Build YAML frontmatter string."""
    lines = ["---"]
    lines.append(f'source_id: "{slug}"')
    lines.append(f'source_domain: "{SOURCE_DOMAIN}"')
    lines.append(f'source_url: "{url}"')
    lines.append(f'title: "{_escape_yaml(meta["title"])}"')
    if meta["author"]:
        lines.append(f'author: "{_escape_yaml(meta["author"])}"')
    if meta["date_published"]:
        lines.append(f'date_published: "{meta["date_published"]}"')
    if meta["date_modified"]:
        lines.append(f'date_modified: "{meta["date_modified"]}"')
    if meta["tags"]:
        tag_list = ", ".join(f'"{_escape_yaml(t)}"' for t in meta["tags"])
        lines.append(f"tags: [{tag_list}]")
    if meta["content_type"]:
        lines.append(f'content_type: "{meta["content_type"]}"')
    lines.append(f'source_tier: "{SOURCE_TIER}"')
    if meta["image_url"]:
        lines.append(f'image_url: "{meta["image_url"]}"')
    if is_paywalled:
        lines.append("paywalled: true")
    lines.append("---")
    return "\n".join(lines)


def _escape_yaml(s: str) -> str:
    """Escape characters that would break YAML double-quoted strings."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def save_article(slug: str, url: str, meta: dict, body_md: str, is_paywalled: bool) -> None:
    """Save article as markdown with YAML frontmatter."""
    frontmatter = build_frontmatter(slug, url, meta, is_paywalled)
    content = f"{frontmatter}\n\n{body_md}\n"

    out_path = os.path.join(ARTICLES_DIR, f"{slug}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)


def save_raw_html(slug: str, html: str) -> None:
    """Save raw HTML for potential reprocessing."""
    out_path = os.path.join(RAW_DIR, f"{slug}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


# ---------------------------------------------------------------------------
# Thread-safe stats
# ---------------------------------------------------------------------------

class Stats:
    """Thread-safe statistics counter."""

    def __init__(self):
        self.lock = threading.Lock()
        self.success = 0
        self.failed = 0
        self.skipped = 0
        self.paywalled = 0
        self.errors: list[str] = []
        self.processed = 0
        self.start_time = time.time()

    def record_success(self, paywalled: bool = False):
        with self.lock:
            self.success += 1
            self.processed += 1
            if paywalled:
                self.paywalled += 1

    def record_failed(self, msg: str):
        with self.lock:
            self.failed += 1
            self.errors.append(msg)
            self.processed += 1

    def record_skipped(self):
        with self.lock:
            self.skipped += 1
            self.processed += 1

    def get_processed(self) -> int:
        with self.lock:
            return self.processed

    def elapsed(self) -> float:
        return time.time() - self.start_time


# ---------------------------------------------------------------------------
# Scrape one URL
# ---------------------------------------------------------------------------

def scrape_one(
    url: str,
    proxy_port: int | None,
    retry_port: int | None,
    stats: Stats,
) -> None:
    """Scrape a single article URL."""
    slug = url_to_slug(url)

    # Resume support: skip if already exists
    article_path = os.path.join(ARTICLES_DIR, f"{slug}.md")
    if os.path.isfile(article_path):
        stats.record_skipped()
        return

    # Fetch
    html = fetch_page(url, proxy_port, retry_port)
    if html is None:
        stats.record_failed(f"{url}: fetch failed (404 or network error)")
        return

    # Save raw HTML
    save_raw_html(slug, html)

    # Parse
    soup = BeautifulSoup(html, "lxml")
    jsonld = extract_jsonld(html)
    meta = extract_metadata(jsonld, soup)
    body_md, is_paywalled = extract_body(soup)

    if not body_md and not is_paywalled:
        stats.record_failed(f"{url}: no body content found")
        return

    # Save markdown
    save_article(slug, url, meta, body_md, is_paywalled)
    stats.record_success(paywalled=is_paywalled)

    # Rate limit
    time.sleep(DELAY_PER_REQUEST)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scrape peterattiamd.com articles (HTML scraping via sitemap)"
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Max articles to scrape (0 = all, default: all)"
    )
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_WORKERS,
        help=f"Number of parallel workers (default: {DEFAULT_WORKERS})"
    )
    parser.add_argument(
        "--no-tor", action="store_true",
        help="Skip Tor proxies, use direct connections only"
    )
    args = parser.parse_args()

    os.makedirs(ARTICLES_DIR, exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)

    # Check Tor availability
    use_tor = False
    if not args.no_tor:
        use_tor = check_tor_available()
        if use_tor:
            print(f"Tor proxies available (ports {TOR_PORT_START}-{TOR_PORT_END})")
        else:
            print("Tor proxies not available, using direct connections")

    # Fetch sitemap URLs (always direct for sitemap itself)
    index_session = make_session()
    try:
        post_urls = get_post_urls(index_session)
    finally:
        index_session.close()

    if not post_urls:
        print("ERROR: No post URLs found in sitemap")
        sys.exit(1)

    if args.limit > 0:
        post_urls = post_urls[: args.limit]

    total = len(post_urls)
    print(f"\nTotal URLs to process: {total}")

    # Check existing articles for resume
    already_exist = sum(
        1 for url in post_urls
        if os.path.isfile(os.path.join(ARTICLES_DIR, f"{url_to_slug(url)}.md"))
    )
    print(f"Already scraped: {already_exist}")
    print(f"Remaining: {total - already_exist}")

    if already_exist == total:
        print("Nothing to scrape - all articles already done!")
        return

    # Build proxy assignments
    if use_tor:
        all_ports = list(range(TOR_PORT_START, TOR_PORT_END + 1))
        port_cycle = itertools.cycle(all_ports)
        assignments = []
        for url in post_urls:
            primary = next(port_cycle)
            retry = TOR_PORT_START + ((primary - TOR_PORT_START + 50) % len(all_ports))
            assignments.append((url, primary, retry))
    else:
        assignments = [(url, None, None) for url in post_urls]

    # Scrape
    stats = Stats()
    workers = min(args.workers, total)
    proxy_desc = f"across {TOR_PORT_END - TOR_PORT_START + 1} Tor proxies" if use_tor else "direct"
    print(f"Starting scrape with {workers} workers ({proxy_desc})")
    print(f"Progress every {PROGRESS_EVERY} items\n")

    last_progress = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for url, primary, retry in assignments:
            future = executor.submit(scrape_one, url, primary, retry, stats)
            futures[future] = url

        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                url = futures[future]
                stats.record_failed(f"{url}: unexpected error: {e}")

            processed = stats.get_processed()
            if processed >= last_progress + PROGRESS_EVERY:
                last_progress = processed - (processed % PROGRESS_EVERY)
                elapsed = stats.elapsed()
                rate = processed / elapsed if elapsed > 1 else 0
                with stats.lock:
                    print(
                        f"[{stats.processed}/{total}] "
                        f"success={stats.success} failed={stats.failed} "
                        f"skipped={stats.skipped} paywalled={stats.paywalled} "
                        f"({rate:.1f} req/s, {elapsed:.0f}s)"
                    )

    # Summary
    elapsed = stats.elapsed()
    print(f"\n{'=' * 60}")
    print(f"Scraping complete! ({elapsed:.1f}s)")
    print(f"  Total URLs:        {total}")
    print(f"  Newly scraped:     {stats.success}")
    print(f"  Paywalled:         {stats.paywalled}")
    print(f"  Failed:            {stats.failed}")
    print(f"  Skipped (exist):   {stats.skipped}")
    if stats.processed > 0 and elapsed > 1:
        print(f"  Throughput:        {stats.processed / elapsed:.1f} req/s")

    if stats.errors:
        with open(ERROR_LOG, "w") as f:
            for msg in stats.errors:
                f.write(msg + "\n")
        print(f"\n  Error log: {ERROR_LOG}")
        for msg in stats.errors[:10]:
            print(f"    - {msg}")
        if len(stats.errors) > 10:
            print(f"    ... and {len(stats.errors) - 10} more (see log)")


if __name__ == "__main__":
    main()
