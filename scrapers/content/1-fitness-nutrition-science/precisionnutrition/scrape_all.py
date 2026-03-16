#!/usr/bin/env python3
"""
Scrape precisionnutrition.com articles via HTML scraping (WP REST API returns 401).

Fetches post URLs from sitemap_index.xml -> post-sitemap.xml, post-sitemap2.xml,
and pn-food-sitemap.xml. Scrapes each page's HTML, extracts metadata from
JSON-LD (Yoast) or meta tags, body from div.post_content.pn-wysiwyg, converts
to markdown with YAML frontmatter.

Uses 10 parallel workers with Tor proxies (ports 61000-61099) if available,
otherwise direct connections. 0.5s delay per request.

Usage:
    python3 scrape_all.py              # Scrape all posts + food items
    python3 scrape_all.py --limit 50   # Scrape first 50
    python3 scrape_all.py --workers 5  # Use 5 workers
    python3 scrape_all.py --no-tor     # Skip Tor, direct only
    python3 scrape_all.py --posts-only # Skip food items
    python3 scrape_all.py --food-only  # Only food items
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

SITEMAP_INDEX_URL = "https://www.precisionnutrition.com/sitemap_index.xml"
SOURCE_DOMAIN = "precisionnutrition.com"
SOURCE_TIER = "tier1"

# Sitemaps to fetch
POST_SITEMAP_PATTERNS = ["post-sitemap"]     # matches post-sitemap.xml, post-sitemap2.xml
FOOD_SITEMAP_PATTERNS = ["pn-food-sitemap"]  # matches pn-food-sitemap.xml

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


def _matches_any(sitemap_url: str, patterns: list[str]) -> bool:
    """Check if a sitemap URL matches any of the given patterns."""
    basename = sitemap_url.rstrip("/").split("/")[-1]
    return any(p in basename for p in patterns)


def get_urls_by_type(session: requests.Session, include_posts: bool, include_food: bool) -> tuple[list[str], list[str]]:
    """Get post and food URLs from the sitemap index.

    Returns (post_urls, food_urls).
    """
    print(f"Fetching sitemap index: {SITEMAP_INDEX_URL}")
    child_sitemaps = fetch_sitemap_index(session)
    print(f"  Found {len(child_sitemaps)} child sitemaps")

    post_urls = []
    food_urls = []

    for sitemap_url in child_sitemaps:
        if include_posts and _matches_any(sitemap_url, POST_SITEMAP_PATTERNS):
            print(f"  Fetching posts: {sitemap_url}")
            urls = fetch_sitemap_urls(session, sitemap_url)
            post_urls.extend(urls)
            print(f"    -> {len(urls)} URLs")

        elif include_food and _matches_any(sitemap_url, FOOD_SITEMAP_PATTERNS):
            print(f"  Fetching food items: {sitemap_url}")
            urls = fetch_sitemap_urls(session, sitemap_url)
            food_urls.extend(urls)
            print(f"    -> {len(urls)} URLs")

    print(f"Total: {len(post_urls)} posts + {len(food_urls)} food items = {len(post_urls) + len(food_urls)}")
    return post_urls, food_urls


# ---------------------------------------------------------------------------
# Slug extraction
# ---------------------------------------------------------------------------

def url_to_slug(url: str, is_food: bool = False) -> str:
    """Extract slug from URL path.

    For food items, prepend 'food-' to the slug.
    """
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    slug = parts[-1] if parts else "unknown"
    slug = re.sub(r"[^\w\-]", "-", slug).strip("-")
    if not slug:
        slug = "unknown"
    if is_food:
        slug = f"food-{slug}"
    return slug


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
    """Extract the Article/BlogPosting JSON-LD from Yoast schema."""
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue

        if isinstance(data, dict):
            if _is_article(data):
                return data
            if "@graph" in data:
                for item in data["@graph"]:
                    if isinstance(item, dict) and _is_article(item):
                        return item
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


def extract_metadata(jsonld: dict | None, soup: BeautifulSoup, is_food: bool = False) -> dict:
    """Extract metadata from JSON-LD and/or meta tags."""
    meta = {
        "title": "",
        "author": "",
        "date_published": "",
        "date_modified": "",
        "tags": [],
        "image_url": "",
        "content_type": "how_to" if is_food else "science",
    }

    # Prefer JSON-LD metadata
    if jsonld:
        meta["title"] = jsonld.get("headline", "") or jsonld.get("name", "")

        author = jsonld.get("author", {})
        if isinstance(author, dict):
            meta["author"] = author.get("name", "")
        elif isinstance(author, list) and author:
            meta["author"] = author[0].get("name", "") if isinstance(author[0], dict) else str(author[0])
        elif isinstance(author, str):
            meta["author"] = author

        meta["date_published"] = _extract_date(jsonld.get("datePublished", ""))
        meta["date_modified"] = _extract_date(jsonld.get("dateModified", ""))

        keywords = jsonld.get("keywords", [])
        if isinstance(keywords, str):
            meta["tags"] = [k.strip() for k in keywords.split(",") if k.strip()]
        elif isinstance(keywords, list):
            meta["tags"] = [str(k).strip() for k in keywords if k]

        section = jsonld.get("articleSection", "")
        if isinstance(section, list) and section:
            meta["tags"] = list(set(meta["tags"] + [str(s) for s in section]))

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
        # Also check article:author meta
        if not meta["author"]:
            author_meta2 = soup.find("meta", property="article:author")
            if author_meta2 and author_meta2.get("content"):
                meta["author"] = author_meta2["content"]

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
    match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if match:
        return match.group(1)
    return raw


def extract_body(soup: BeautifulSoup) -> str:
    """Extract body HTML from div.post_content.pn-wysiwyg and convert to markdown.

    Falls back to div.entry-content if primary selector not found.
    """
    # Primary: PN-specific content container
    content_div = soup.find("div", class_=lambda c: c and "post_content" in c and "pn-wysiwyg" in c)

    # Fallback: standard WP container
    if not content_div:
        content_div = soup.find("div", class_="post_content")
    if not content_div:
        content_div = soup.find("div", class_="entry-content")
    if not content_div:
        # Last resort: look for article body
        content_div = soup.find("article")

    if not content_div:
        return ""

    # Remove unwanted elements
    for unwanted in content_div.find_all(["script", "style", "noscript", "iframe"]):
        unwanted.decompose()

    # Remove social sharing, newsletter signup, related content
    for cls in ["sharedaddy", "sd-sharing", "jp-relatedposts",
                "newsletter-signup", "wp-block-buttons",
                "pn-cta", "pn-cta-box", "pn-signup",
                "social-share", "article-share"]:
        for el in content_div.find_all(class_=cls):
            el.decompose()

    body_html = str(content_div)
    body_md = md(body_html, heading_style="ATX", strip=["img"])

    # Clean up excessive whitespace
    body_md = re.sub(r"\n{3,}", "\n\n", body_md)
    body_md = body_md.strip()

    return body_md


# ---------------------------------------------------------------------------
# Markdown file output
# ---------------------------------------------------------------------------

def build_frontmatter(slug: str, url: str, meta: dict, is_food: bool = False) -> str:
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
    lines.append(f'content_type: "{meta["content_type"]}"')
    lines.append(f'source_tier: "{SOURCE_TIER}"')
    if meta["image_url"]:
        lines.append(f'image_url: "{meta["image_url"]}"')
    if is_food:
        lines.append("is_food_item: true")
    lines.append("---")
    return "\n".join(lines)


def _escape_yaml(s: str) -> str:
    """Escape characters that would break YAML double-quoted strings."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def save_article(slug: str, url: str, meta: dict, body_md: str, is_food: bool = False) -> None:
    """Save article as markdown with YAML frontmatter."""
    frontmatter = build_frontmatter(slug, url, meta, is_food)
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
        self.food_items = 0
        self.errors: list[str] = []
        self.processed = 0
        self.start_time = time.time()

    def record_success(self, is_food: bool = False):
        with self.lock:
            self.success += 1
            self.processed += 1
            if is_food:
                self.food_items += 1

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
    is_food: bool,
    proxy_port: int | None,
    retry_port: int | None,
    stats: Stats,
) -> None:
    """Scrape a single article or food item URL."""
    slug = url_to_slug(url, is_food=is_food)

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
    meta = extract_metadata(jsonld, soup, is_food=is_food)
    body_md = extract_body(soup)

    if not body_md:
        stats.record_failed(f"{url}: no body content found")
        return

    # Save markdown
    save_article(slug, url, meta, body_md, is_food=is_food)
    stats.record_success(is_food=is_food)

    # Rate limit
    time.sleep(DELAY_PER_REQUEST)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scrape precisionnutrition.com articles (HTML scraping via sitemap)"
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Max items to scrape (0 = all, default: all)"
    )
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_WORKERS,
        help=f"Number of parallel workers (default: {DEFAULT_WORKERS})"
    )
    parser.add_argument(
        "--no-tor", action="store_true",
        help="Skip Tor proxies, use direct connections only"
    )
    parser.add_argument(
        "--posts-only", action="store_true",
        help="Only scrape blog posts, skip food items"
    )
    parser.add_argument(
        "--food-only", action="store_true",
        help="Only scrape food items, skip blog posts"
    )
    args = parser.parse_args()

    if args.posts_only and args.food_only:
        print("ERROR: Cannot use both --posts-only and --food-only")
        sys.exit(1)

    os.makedirs(ARTICLES_DIR, exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)

    include_posts = not args.food_only
    include_food = not args.posts_only

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
        post_urls, food_urls = get_urls_by_type(index_session, include_posts, include_food)
    finally:
        index_session.close()

    # Build combined work list: (url, is_food)
    work_items: list[tuple[str, bool]] = []
    for url in post_urls:
        work_items.append((url, False))
    for url in food_urls:
        work_items.append((url, True))

    if not work_items:
        print("ERROR: No URLs found in sitemap")
        sys.exit(1)

    if args.limit > 0:
        work_items = work_items[: args.limit]

    total = len(work_items)
    print(f"\nTotal URLs to process: {total}")

    # Check existing articles for resume
    already_exist = sum(
        1 for url, is_food in work_items
        if os.path.isfile(os.path.join(ARTICLES_DIR, f"{url_to_slug(url, is_food)}.md"))
    )
    print(f"Already scraped: {already_exist}")
    print(f"Remaining: {total - already_exist}")

    if already_exist == total:
        print("Nothing to scrape - all items already done!")
        return

    # Build proxy assignments
    if use_tor:
        all_ports = list(range(TOR_PORT_START, TOR_PORT_END + 1))
        port_cycle = itertools.cycle(all_ports)
        assignments = []
        for url, is_food in work_items:
            primary = next(port_cycle)
            retry = TOR_PORT_START + ((primary - TOR_PORT_START + 50) % len(all_ports))
            assignments.append((url, is_food, primary, retry))
    else:
        assignments = [(url, is_food, None, None) for url, is_food in work_items]

    # Scrape
    stats = Stats()
    workers = min(args.workers, total)
    proxy_desc = f"across {TOR_PORT_END - TOR_PORT_START + 1} Tor proxies" if use_tor else "direct"
    print(f"Starting scrape with {workers} workers ({proxy_desc})")
    print(f"Progress every {PROGRESS_EVERY} items\n")

    last_progress = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for url, is_food, primary, retry in assignments:
            future = executor.submit(scrape_one, url, is_food, primary, retry, stats)
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
                        f"skipped={stats.skipped} food={stats.food_items} "
                        f"({rate:.1f} req/s, {elapsed:.0f}s)"
                    )

    # Summary
    elapsed = stats.elapsed()
    print(f"\n{'=' * 60}")
    print(f"Scraping complete! ({elapsed:.1f}s)")
    print(f"  Total URLs:        {total}")
    print(f"  Newly scraped:     {stats.success}")
    print(f"    Posts:           {stats.success - stats.food_items}")
    print(f"    Food items:      {stats.food_items}")
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
