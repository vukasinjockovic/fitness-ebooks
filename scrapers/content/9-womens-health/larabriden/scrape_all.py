#!/usr/bin/env python3
"""
Scrape larabriden.com via Wayback Machine.

Live site blocks everything with Cloudflare 403.
Wayback captures through Aug 2025 confirmed.
CDX API for URL discovery + Wayback fetch with id_ suffix.
WordPress + Yoast -- look for JSON-LD in Wayback snapshots.

Site: larabriden.com
Posts: ~110
Source tier: tier2

Usage:
    python3 scrape_all.py              # Scrape all
    python3 scrape_all.py --limit 10   # First 10 only
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
from threading import Lock

from bs4 import BeautifulSoup
from markdownify import markdownify as md

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")
RAW_DIR = os.path.join(SCRIPT_DIR, "raw")
CDX_CACHE_FILE = os.path.join(SCRIPT_DIR, "cdx_cache.json")
URLS_CACHE_FILE = os.path.join(SCRIPT_DIR, "discovered_urls.json")

SOURCE_DOMAIN = "larabriden.com"
SOURCE_CATEGORY = "9_womens_health"
SOURCE_TIER = "tier2"
DELAY = 1.5
REQUEST_TIMEOUT = 60

USER_AGENT = (
    "GymZilla-ContentScraper/1.0 "
    "(fitness research project; polite; contact@gymzillatribe.com)"
)

# URL patterns to exclude (not articles)
EXCLUDE_PATTERNS = [
    r"/tag/",
    r"/category/",
    r"/page/\d+",
    r"/author/",
    r"/wp-content/",
    r"/wp-admin/",
    r"/wp-json/",
    r"/feed/",
    r"/comments/",
    r"\?",
    r"/sitemap",
    r"\.xml$",
    r"\.css$",
    r"\.js$",
    r"\.jpg$",
    r"\.png$",
    r"\.gif$",
    r"\.pdf$",
]


def _make_request(url: str, timeout: int = 60) -> bytes | None:
    """Make HTTP request with retry."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(2):
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
            if e.code in (429, 503) and attempt == 0:
                print(f"    [RETRY] HTTP {e.code}, waiting 10s...")
                time.sleep(10)
                continue
            if e.code == 404:
                return None
            print(f"    [ERROR] HTTP {e.code} for {url[:80]}...")
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == 0:
                print(f"    [RETRY] {e}, waiting 10s...")
                time.sleep(10)
                continue
            print(f"    [ERROR] {e} for {url[:80]}...")
            return None
    return None


def discover_urls_via_cdx() -> list[str]:
    """Use CDX API to discover all larabriden.com post URLs."""
    if os.path.isfile(URLS_CACHE_FILE):
        with open(URLS_CACHE_FILE, "r") as f:
            cached = json.load(f)
        if cached:
            print(f"  Loaded {len(cached)} URLs from cache")
            return cached

    print("  Querying CDX API for larabriden.com URLs...")
    cdx_url = (
        "http://web.archive.org/cdx/search/cdx"
        "?url=larabriden.com/*"
        "&output=json"
        "&fl=original"
        "&filter=statuscode:200"
        "&collapse=urlkey"
    )

    data = _make_request(cdx_url, timeout=120)
    if not data:
        print("    CDX query failed!")
        return []

    try:
        rows = json.loads(data)
    except json.JSONDecodeError:
        print("    CDX JSON parse failed!")
        return []

    if len(rows) < 2:
        print("    No CDX results")
        return []

    print(f"    CDX returned {len(rows) - 1} rows")

    # Known non-post slugs to exclude
    skip_slugs = {
        "blog", "about", "contact", "resources", "books", "podcast",
        "privacy-policy", "terms", "disclaimer", "shop", "cart",
        "my-account", "checkout", "contact-lara", "book-sales",
        "amazon-logo", "body-literacy", "basic-body-literacy",
    }

    # Parse and filter URLs -- only single-segment paths with hyphens (posts)
    slugs = set()
    for row in rows[1:]:
        original_url = row[0]
        # Skip URLs with query params
        if "?" in original_url or "#" in original_url:
            continue
        parsed = urllib.parse.urlparse(original_url)
        path = parsed.path.strip("/")
        if not path:
            continue

        # Skip non-article paths
        skip = False
        for pattern in EXCLUDE_PATTERNS:
            if re.search(pattern, original_url, re.I):
                skip = True
                break
        if skip:
            continue

        parts = path.split("/")
        # Posts are single-segment paths with hyphens
        if len(parts) == 1 and "-" in parts[0] and parts[0] not in skip_slugs:
            slugs.add(parts[0])

    post_urls = [f"https://www.larabriden.com/{slug}/" for slug in sorted(slugs)]

    print(f"    Found {len(post_urls)} unique post URLs")

    # Save cache
    with open(URLS_CACHE_FILE, "w") as f:
        json.dump(post_urls, f, indent=2)

    return post_urls


def get_wayback_timestamp(url: str, cdx_cache: dict) -> str | None:
    """Get best Wayback timestamp for a URL."""
    if url in cdx_cache:
        return cdx_cache[url]

    cdx_url = urllib.parse.urlencode({
        "url": url,
        "output": "json",
        "fl": "timestamp,statuscode",
        "filter": "statuscode:200",
        "limit": -1,  # most recent
    })
    full_url = f"http://web.archive.org/cdx/search/cdx?{cdx_url}"

    data = _make_request(full_url)
    if not data:
        return None

    try:
        rows = json.loads(data)
        if len(rows) >= 2:
            timestamp = rows[-1][0]
            cdx_cache[url] = timestamp
            return timestamp
    except (json.JSONDecodeError, IndexError):
        pass

    return None


def fetch_wayback_page(timestamp: str, url: str) -> str | None:
    """Fetch page from Wayback Machine."""
    wb_url = f"https://web.archive.org/web/{timestamp}id_/{url}"
    data = _make_request(wb_url)
    if data:
        return data.decode("utf-8", errors="replace")
    return None


def extract_jsonld(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            if "@graph" in data:
                for item in data["@graph"]:
                    t = item.get("@type", "")
                    types = {"Article", "BlogPosting", "NewsArticle"}
                    if t in types or (isinstance(t, list) and set(t) & types):
                        return item
            t = data.get("@type", "")
            if t in ("Article", "BlogPosting", "NewsArticle"):
                return data
    return None


def extract_date(raw: str) -> str:
    if not raw:
        return ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else raw


def extract_body(soup: BeautifulSoup) -> str:
    """Extract body from WordPress article."""
    # Remove unwanted elements
    for tag in soup.find_all(["nav", "footer", "header", "aside", "script",
                              "style", "noscript", "iframe", "svg"]):
        tag.decompose()

    # Try entry-content (standard WP)
    content_div = soup.find("div", class_="entry-content")
    if not content_div:
        content_div = soup.find(class_="post-content")
    if not content_div:
        content_div = soup.find("article")
    if not content_div:
        # Fallback: main
        content_div = soup.find("main")

    if not content_div:
        return ""

    # Remove social sharing, comments, related posts
    for cls in ["sharedaddy", "sd-sharing", "comments", "related-posts",
                "newsletter", "essb_links", "wp-block-buttons"]:
        for el in content_div.find_all(class_=cls):
            el.decompose()

    body_md = md(str(content_div), heading_style="ATX", strip=["img"])
    body_md = re.sub(r"\n{3,}", "\n\n", body_md)
    return body_md.strip()


def escape_yaml(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_frontmatter(slug: str, url: str, meta: dict) -> str:
    lines = ["---"]
    lines.append(f'source_id: "{slug}"')
    lines.append(f'source_domain: "{SOURCE_DOMAIN}"')
    lines.append(f'source_url: "{url}"')
    lines.append(f'title: "{escape_yaml(meta.get("title", slug))}"')
    if meta.get("author"):
        lines.append(f'author: "{escape_yaml(meta["author"])}"')
    if meta.get("date_published"):
        lines.append(f'date_published: "{meta["date_published"]}"')
    if meta.get("date_modified"):
        lines.append(f'date_modified: "{meta["date_modified"]}"')
    if meta.get("tags"):
        tag_list = ", ".join(f'"{escape_yaml(t)}"' for t in meta["tags"])
        lines.append(f"tags: [{tag_list}]")
    lines.append(f'content_type: "science"')
    lines.append(f'source_tier: "{SOURCE_TIER}"')
    if meta.get("image_url"):
        lines.append(f'image_url: "{meta["image_url"]}"')
    if meta.get("word_count"):
        lines.append(f'word_count: {meta["word_count"]}')
    if meta.get("wayback_timestamp"):
        lines.append(f'wayback_timestamp: "{meta["wayback_timestamp"]}"')
    lines.append("---")
    return "\n".join(lines)


def scrape_article(url: str, cdx_cache: dict) -> dict | None:
    slug_parts = urllib.parse.urlparse(url).path.strip("/").split("/")
    slug = slug_parts[-1] if slug_parts else "unknown"
    article_path = os.path.join(ARTICLES_DIR, f"{slug}.md")

    if os.path.isfile(article_path):
        return {"status": "skipped", "slug": slug}

    # Get Wayback timestamp
    time.sleep(DELAY)
    timestamp = get_wayback_timestamp(url, cdx_cache)
    if not timestamp:
        return {"status": "no_snapshot", "slug": slug}

    # Fetch from Wayback
    time.sleep(DELAY)
    html = fetch_wayback_page(timestamp, url)
    if not html:
        return {"status": "fetch_failed", "slug": slug}

    # Save raw
    os.makedirs(RAW_DIR, exist_ok=True)
    with open(os.path.join(RAW_DIR, f"{slug}.html"), "w", encoding="utf-8") as f:
        f.write(html)

    soup = BeautifulSoup(html, "lxml")
    body_md = extract_body(soup)
    if not body_md or len(body_md) < 100:
        return {"status": "empty", "slug": slug}

    # JSON-LD metadata (Yoast)
    jsonld = extract_jsonld(soup)

    title = ""
    author = "Dr. Lara Briden"
    date_published = ""
    date_modified = ""
    image_url = ""
    tags = ["hormonal-health"]

    if jsonld:
        title = jsonld.get("headline", "") or jsonld.get("name", "")
        a = jsonld.get("author", {})
        if isinstance(a, dict):
            author = a.get("name", author)
        date_published = extract_date(jsonld.get("datePublished", ""))
        date_modified = extract_date(jsonld.get("dateModified", ""))
        section = jsonld.get("articleSection", "")
        if isinstance(section, str) and section:
            tags.append(section)
        elif isinstance(section, list):
            tags.extend([s for s in section if s])
        img = jsonld.get("image", "")
        if isinstance(img, dict):
            image_url = img.get("url", "")
        elif isinstance(img, str):
            image_url = img

    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else slug.replace("-", " ").title()

    if not image_url:
        og = soup.find("meta", property="og:image")
        if og:
            image_url = og.get("content", "")

    wc = len(re.sub(r"[*_#\[\]()>`~|]", " ", body_md).split())
    tags = list(set(tags))

    meta_dict = {
        "title": title,
        "author": author,
        "date_published": date_published,
        "date_modified": date_modified,
        "tags": tags,
        "image_url": image_url,
        "word_count": wc,
        "wayback_timestamp": timestamp,
    }

    frontmatter = build_frontmatter(slug, url, meta_dict)
    os.makedirs(ARTICLES_DIR, exist_ok=True)
    with open(article_path, "w", encoding="utf-8") as f:
        f.write(frontmatter)
        f.write("\n\n")
        f.write(body_md)
        f.write("\n")

    return {"status": "success", "slug": slug, "title": title, "word_count": wc, "timestamp": timestamp}


def load_cdx_cache() -> dict:
    if os.path.isfile(CDX_CACHE_FILE):
        with open(CDX_CACHE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_cdx_cache(cache: dict):
    with open(CDX_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Scrape larabriden.com via Wayback Machine")
    parser.add_argument("--limit", type=int, default=0, help="Limit to N posts (0=all)")
    parser.add_argument("--no-cache", action="store_true", help="Ignore caches")
    args = parser.parse_args()

    os.makedirs(ARTICLES_DIR, exist_ok=True)

    if args.no_cache:
        for f in [CDX_CACHE_FILE, URLS_CACHE_FILE]:
            if os.path.isfile(f):
                os.remove(f)

    # Step 1: Discover URLs via CDX
    print("=== Phase 1: URL Discovery via Wayback CDX ===")
    urls = discover_urls_via_cdx()
    print(f"  Discovered {len(urls)} post URLs")

    if args.limit > 0:
        urls = urls[:args.limit]
        print(f"  Limited to {len(urls)} posts")

    if not urls:
        print("ERROR: No URLs found!")
        sys.exit(1)

    # Step 2: Scrape each via Wayback
    print(f"\n=== Phase 2: Scraping {len(urls)} articles via Wayback ===")
    cdx_cache = load_cdx_cache()
    stats = {"success": 0, "skipped": 0, "failed": 0}

    for i, url in enumerate(urls, 1):
        result = scrape_article(url, cdx_cache)
        if result:
            if result["status"] == "success":
                stats["success"] += 1
                print(f"  [{i}/{len(urls)}] OK: {result.get('title', '')[:60]} ({result.get('word_count', 0)}w)")
            elif result["status"] == "skipped":
                stats["skipped"] += 1
            else:
                stats["failed"] += 1
                print(f"  [{i}/{len(urls)}] FAIL ({result['status']}): {result['slug']}")

        # Save CDX cache periodically
        if i % 10 == 0:
            save_cdx_cache(cdx_cache)
            print(f"  Progress: {i}/{len(urls)} (ok={stats['success']}, skip={stats['skipped']}, fail={stats['failed']})")

    save_cdx_cache(cdx_cache)

    print(f"\n=== COMPLETE ===")
    print(f"  Success:  {stats['success']}")
    print(f"  Skipped:  {stats['skipped']}")
    print(f"  Failed:   {stats['failed']}")
    print(f"  Total:    {len(urls)}")


if __name__ == "__main__":
    main()
