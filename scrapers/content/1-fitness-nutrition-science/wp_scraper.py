#!/usr/bin/env python3
"""
Shared WP REST API scraper module.

Provides common functionality for scraping WordPress sites via their REST API:
- Paginated post fetching
- Category/tag/author cache resolution
- HTML to markdown conversion via markdownify
- YAML frontmatter generation
- Resume support (skip existing files)
- Retry logic for transient HTTP errors

Dependencies: markdownify (pip install markdownify)
All other imports are Python stdlib.
"""

import html
import json
import os
import re
import socket
import time
import urllib.request
import urllib.error

from markdownify import markdownify as md


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------

def _escape_yaml_string(s: str) -> str:
    """Escape a string for safe inclusion in YAML double-quoted scalar."""
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    return s


def build_frontmatter(
    source_id: str,
    source_domain: str,
    source_url: str,
    title: str,
    author: str,
    date_published: str,
    tags: list[str],
    content_type: str,
    source_tier: str,
    word_count: int,
    image_url: str | None,
) -> str:
    """Build a YAML frontmatter block for a markdown article."""
    tags_str = json.dumps(tags)
    image_str = f'"{_escape_yaml_string(image_url)}"' if image_url else "null"

    lines = [
        "---",
        f'source_id: "{_escape_yaml_string(source_id)}"',
        f'source_domain: "{_escape_yaml_string(source_domain)}"',
        f'source_url: "{_escape_yaml_string(source_url)}"',
        f'title: "{_escape_yaml_string(title)}"',
        f'author: "{_escape_yaml_string(author)}"',
        f'date_published: "{_escape_yaml_string(date_published)}"',
        f"tags: {tags_str}",
        f'content_type: "{_escape_yaml_string(content_type)}"',
        f'source_tier: "{_escape_yaml_string(source_tier)}"',
        f"word_count: {word_count}",
        f"image_url: {image_str}",
        "---",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# HTML -> Markdown
# ---------------------------------------------------------------------------

_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)


def html_to_markdown(html_content: str) -> str:
    """Convert HTML to clean markdown, stripping scripts/styles."""
    # Remove script and style tags before conversion
    cleaned = _SCRIPT_STYLE_RE.sub("", html_content)
    result = md(cleaned, heading_style="ATX", strip=["img"])
    return result.strip() if result else ""


# ---------------------------------------------------------------------------
# Word count
# ---------------------------------------------------------------------------

def word_count(text: str) -> int:
    """Count words in a text string, ignoring markdown formatting."""
    # Strip markdown formatting characters
    cleaned = re.sub(r"[*_#\[\]()>~`|]", " ", text)
    words = cleaned.split()
    return len(words)


# ---------------------------------------------------------------------------
# Post processing
# ---------------------------------------------------------------------------

def process_post(
    post: dict,
    domain: str,
    source_tier: str,
    author_cache: dict[int, str],
    category_cache: dict[int, str],
    tag_cache: dict[int, str],
) -> dict:
    """Process a WP REST API post into an article dict.

    Returns a dict with keys: source_id, source_domain, source_url, title,
    author, date_published, tags, content_type, source_tier, word_count,
    image_url, body_md.
    """
    slug = post["slug"]
    link = post["link"]
    title_raw = post["title"]["rendered"]
    title_clean = html.unescape(title_raw)
    body_html = post["content"]["rendered"]
    body_md = html_to_markdown(body_html)
    wc = word_count(body_md)

    # Author: try cache first, then Yoast schema graph, then twitter_misc
    author_id = post.get("author", 0)
    author_name = author_cache.get(author_id, "Unknown")

    yoast = post.get("yoast_head_json")
    if author_name == "Unknown" and yoast and isinstance(yoast, dict):
        # Try Yoast schema @graph for Person
        graph = yoast.get("schema", {}).get("@graph", [])
        for item in graph:
            item_type = item.get("@type")
            if item_type == "Person" or (
                isinstance(item_type, list) and "Person" in item_type
            ):
                name = item.get("name")
                if name:
                    author_name = name
                    break
        # Fallback: twitter_misc "Written by"
        if author_name == "Unknown":
            twitter_misc = yoast.get("twitter_misc", {})
            if isinstance(twitter_misc, dict):
                written_by = twitter_misc.get("Written by")
                if written_by:
                    author_name = written_by

    # Date: prefer Yoast datePublished if available
    date_published = post.get("date", "")
    if yoast and isinstance(yoast, dict):
        yoast_date = yoast.get("datePublished")
        if yoast_date:
            date_published = yoast_date

    # Tags = WP categories + WP tags (resolved to names)
    all_tags = []
    for cat_id in post.get("categories", []):
        name = category_cache.get(cat_id)
        if name:
            all_tags.append(name)
    for tag_id in post.get("tags", []):
        name = tag_cache.get(tag_id)
        if name:
            all_tags.append(name)

    # Featured image
    image_url = None
    embedded = post.get("_embedded", {})
    if embedded:
        featured = embedded.get("wp:featuredmedia")
        if featured and isinstance(featured, list) and len(featured) > 0:
            image_url = featured[0].get("source_url")

    return {
        "source_id": slug,
        "source_domain": domain,
        "source_url": link,
        "title": title_clean,
        "author": author_name,
        "date_published": date_published,
        "tags": all_tags,
        "content_type": "science",
        "source_tier": source_tier,
        "word_count": wc,
        "image_url": image_url,
        "body_md": body_md,
    }


# ---------------------------------------------------------------------------
# WP Scraper class
# ---------------------------------------------------------------------------

class WPScraper:
    """WordPress REST API scraper with caching, resume, and retry."""

    def __init__(
        self,
        domain: str,
        base_url: str,
        source_tier: str,
        articles_dir: str,
        user_agent: str = "Mozilla/5.0 (compatible; GymZilla/1.0)",
        delay: float = 1.0,
        socks_proxy: tuple[str, int] | None = None,
    ):
        self.domain = domain
        self.base_url = base_url
        self.source_tier = source_tier
        self.articles_dir = articles_dir
        self.user_agent = user_agent
        self.delay = delay
        # Optional SOCKS5 proxy: (host, port) e.g. ("127.0.0.1", 10300)
        self.socks_proxy = socks_proxy

        # Caches for WP taxonomy lookups
        self.author_cache: dict[int, str] = {}
        self.category_cache: dict[int, str] = {}
        self.tag_cache: dict[int, str] = {}

        # Stats
        self.scraped = 0
        self.skipped = 0
        self.failed = 0

    def page_url(self, page: int) -> str:
        """Build a paginated API URL."""
        sep = "&" if "?" in self.base_url else "?"
        return f"{self.base_url}{sep}per_page=100&page={page}"

    def already_exists(self, slug: str) -> bool:
        """Check if an article file already exists (resume support)."""
        return os.path.isfile(os.path.join(self.articles_dir, f"{slug}.md"))

    def fetch_url(self, url: str, retries: int = 1) -> bytes | None:
        """Fetch a URL with retry logic for 429/5xx errors.

        Returns response body bytes on success, None on non-retryable failure.
        If socks_proxy is set on this instance, all requests are routed through
        that SOCKS5 proxy (requires PySocks: pip install PySocks).
        """
        # Install SOCKS5 proxy for the duration of this call if configured.
        # We patch socket.socket per-call and restore it afterward so other
        # threads / scrapers running concurrently are not affected.
        orig_socket = None
        if self.socks_proxy:
            try:
                import socks as _socks
                orig_socket = socket.socket
                _socks.set_default_proxy(
                    _socks.SOCKS5, self.socks_proxy[0], self.socks_proxy[1]
                )
                socket.socket = _socks.socksocket
            except ImportError:
                print("WARNING: PySocks not installed; ignoring socks_proxy. "
                      "Run: pip install PySocks")
                orig_socket = None

        try:
            for attempt in range(1 + retries):
                req = urllib.request.Request(url)
                req.add_header("User-Agent", self.user_agent)
                try:
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        return resp.read()
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        return None
                    if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                        time.sleep(2)
                        continue
                    return None
                except (urllib.error.URLError, OSError) as e:
                    if attempt < retries:
                        time.sleep(2)
                        continue
                    return None
            return None
        finally:
            if orig_socket is not None:
                socket.socket = orig_socket

    def fetch_json(self, url: str) -> list | dict | None:
        """Fetch a URL and parse JSON response."""
        data = self.fetch_url(url)
        if data is None:
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None

    def _fetch_wp_taxonomy(self, endpoint: str) -> dict[int, str]:
        """Fetch all items from a paginated WP taxonomy endpoint.

        Returns {id: name} mapping.
        """
        cache = {}
        page = 1
        while True:
            url = f"{endpoint}?per_page=100&page={page}"
            items = self.fetch_json(url)
            if not items or not isinstance(items, list) or len(items) == 0:
                break
            for item in items:
                cache[item["id"]] = html.unescape(item.get("name", ""))
            if len(items) < 100:
                break
            page += 1
            time.sleep(0.5)
        return cache

    def load_categories(self):
        """Fetch and cache all WP categories."""
        base = self.base_url.split("/wp-json/")[0]
        endpoint = f"{base}/wp-json/wp/v2/categories"
        self.category_cache = self._fetch_wp_taxonomy(endpoint)
        print(f"  Cached {len(self.category_cache)} categories")

    def load_tags(self):
        """Fetch and cache all WP tags."""
        base = self.base_url.split("/wp-json/")[0]
        endpoint = f"{base}/wp-json/wp/v2/tags"
        self.tag_cache = self._fetch_wp_taxonomy(endpoint)
        print(f"  Cached {len(self.tag_cache)} tags")

    def load_authors(self):
        """Fetch and cache all WP authors/users."""
        base = self.base_url.split("/wp-json/")[0]
        endpoint = f"{base}/wp-json/wp/v2/users"
        self.author_cache = self._fetch_wp_taxonomy(endpoint)
        print(f"  Cached {len(self.author_cache)} authors")

    def fetch_author(self, author_id: int) -> str:
        """Fetch a single author by ID if not cached."""
        if author_id in self.author_cache:
            return self.author_cache[author_id]
        base = self.base_url.split("/wp-json/")[0]
        url = f"{base}/wp-json/wp/v2/users/{author_id}"
        data = self.fetch_json(url)
        if data and isinstance(data, dict):
            name = html.unescape(data.get("name", "Unknown"))
            self.author_cache[author_id] = name
            return name
        self.author_cache[author_id] = "Unknown"
        return "Unknown"

    def save_article(self, article: dict):
        """Save an article dict as a markdown file."""
        frontmatter = build_frontmatter(
            source_id=article["source_id"],
            source_domain=article["source_domain"],
            source_url=article["source_url"],
            title=article["title"],
            author=article["author"],
            date_published=article["date_published"],
            tags=article["tags"],
            content_type=article["content_type"],
            source_tier=article["source_tier"],
            word_count=article["word_count"],
            image_url=article["image_url"],
        )
        content = frontmatter + "\n" + article["body_md"] + "\n"

        path = os.path.join(self.articles_dir, f"{article['source_id']}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def scrape_all(self):
        """Scrape all posts from the WP REST API with pagination."""
        os.makedirs(self.articles_dir, exist_ok=True)

        print(f"Scraping {self.domain}...")
        print(f"  Base URL: {self.base_url}")
        print(f"  Articles dir: {self.articles_dir}")
        print()

        # Load taxonomy caches
        print("Loading taxonomy caches...")
        self.load_categories()
        self.load_tags()
        self.load_authors()
        print()

        page = 1
        total_processed = 0

        while True:
            url = self.page_url(page)
            print(f"Fetching page {page}: {url}")

            posts = self.fetch_json(url)
            if not posts or not isinstance(posts, list) or len(posts) == 0:
                print(f"  No more posts (page {page}). Done.")
                break

            for post in posts:
                slug = post.get("slug", "")
                if not slug:
                    self.failed += 1
                    continue

                if self.already_exists(slug):
                    self.skipped += 1
                    total_processed += 1
                    continue

                try:
                    # Resolve author if not cached
                    author_id = post.get("author", 0)
                    if author_id and author_id not in self.author_cache:
                        self.fetch_author(author_id)

                    article = process_post(
                        post=post,
                        domain=self.domain,
                        source_tier=self.source_tier,
                        author_cache=self.author_cache,
                        category_cache=self.category_cache,
                        tag_cache=self.tag_cache,
                    )
                    self.save_article(article)
                    self.scraped += 1
                except Exception as e:
                    print(f"  ERROR processing {slug}: {e}")
                    self.failed += 1

                total_processed += 1

                if total_processed % 50 == 0:
                    print(f"  Progress: {total_processed} processed "
                          f"({self.scraped} scraped, {self.skipped} skipped, "
                          f"{self.failed} failed)")

            if len(posts) < 100:
                print(f"  Last page (got {len(posts)} posts).")
                break

            page += 1
            time.sleep(self.delay)

        print()
        print("=" * 60)
        print(f"SUMMARY for {self.domain}")
        print(f"  Scraped: {self.scraped}")
        print(f"  Skipped: {self.skipped}")
        print(f"  Failed:  {self.failed}")
        print(f"  Total:   {self.scraped + self.skipped + self.failed}")
        print("=" * 60)
