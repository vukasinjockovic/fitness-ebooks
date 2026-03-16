#!/usr/bin/env python3
"""
Scrape trainerize.com/blog via sitemap + HTML scraping.

Site: trainerize.com/blog
Estimated posts: 1,076
Source tier: tier2
Category: 10_coach_education
WP REST API is blocked (401), must use sitemap + HTML parse.
Content selector: div.entry-txt
JSON-LD available for metadata.

Usage:
    python3 scrape_all.py
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape as html_unescape

from bs4 import BeautifulSoup
from markdownify import markdownify as md

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

DOMAIN = "trainerize.com"
SITEMAP_URLS = [
    "https://trainerize.com/blog/post-sitemap.xml",
    "https://trainerize.com/blog/post-sitemap2.xml",
]
SOURCE_TIER = "tier2"
SOURCE_CATEGORY = "10_coach_education"
USER_AGENT = "Mozilla/5.0 (compatible; GymZilla/1.0)"
REQUEST_TIMEOUT = 30
DELAY = 1.0
PROGRESS_EVERY = 50

# ---------------------------------------------------------------------------
# HTML -> Markdown
# ---------------------------------------------------------------------------

_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)


def html_to_markdown(html_content: str) -> str:
    cleaned = _SCRIPT_STYLE_RE.sub("", html_content)
    result = md(cleaned, heading_style="ATX", strip=["img"])
    return result.strip() if result else ""


def word_count(text: str) -> int:
    cleaned = re.sub(r"[*_#\[\]()>~`|]", " ", text)
    return len(cleaned.split())


def _escape_yaml(s: str) -> str:
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    return s


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def fetch_url(url: str, retries: int = 2) -> bytes | None:
    for attempt in range(1 + retries):
        req = urllib.request.Request(url)
        req.add_header("User-Agent", USER_AGENT)
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                wait = 5 * (attempt + 1)
                print(f"  HTTP {e.code} for {url[:80]}... retrying in {wait}s")
                time.sleep(wait)
                continue
            print(f"  HTTP {e.code} for {url[:80]}")
            return None
        except (urllib.error.URLError, OSError) as e:
            if attempt < retries:
                time.sleep(3)
                continue
            print(f"  Error fetching {url[:80]}: {e}")
            return None
    return None


# ---------------------------------------------------------------------------
# Sitemap parsing
# ---------------------------------------------------------------------------


def parse_sitemap(xml_bytes: bytes) -> list[str]:
    """Parse a sitemap XML and return all <loc> URLs."""
    urls = []
    try:
        root = ET.fromstring(xml_bytes)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for url_el in root.findall(".//sm:url/sm:loc", ns):
            if url_el.text:
                urls.append(url_el.text.strip())
    except ET.ParseError as e:
        print(f"  Sitemap parse error: {e}")
    return urls


def fetch_all_post_urls() -> list[str]:
    """Fetch all post URLs from sitemaps."""
    all_urls = []
    for sitemap_url in SITEMAP_URLS:
        print(f"  Fetching sitemap: {sitemap_url}")
        data = fetch_url(sitemap_url)
        if data:
            urls = parse_sitemap(data)
            print(f"    Found {len(urls)} URLs")
            all_urls.extend(urls)
        else:
            print(f"    Failed to fetch sitemap")
        time.sleep(0.5)
    return all_urls


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------


def extract_article(html_bytes: bytes, url: str) -> dict | None:
    """Extract article content from HTML page."""
    html_str = html_bytes.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html_str, "lxml")

    # Title: prefer og:title (h1 is just "Fitness Business Blog" site name)
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title:
        title = og_title.get("content", "")
    if not title:
        # Fallback to entry-title class (contains categories + title)
        entry_title = soup.select_one(".entry-title")
        if entry_title:
            title = entry_title.get_text(strip=True)
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            # Strip " • Fitness Business Blog" suffix
            title = title_tag.get_text(strip=True).split("•")[0].strip()
    title = html_unescape(title) if title else ""

    # Content from div.entry-txt
    content_el = soup.select_one("div.entry-txt")
    if not content_el:
        # Fallback selectors
        content_el = soup.select_one("article .entry-content")
        if not content_el:
            content_el = soup.select_one("article")
    if not content_el:
        return None

    body_md = html_to_markdown(str(content_el))
    if not body_md or len(body_md) < 50:
        return None

    # JSON-LD metadata (Yoast uses @graph format)
    author = "Trainerize"
    date_published = ""
    tags = []
    person_map = {}  # @id -> name for resolving author references

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            ld = json.loads(script.string)
            if isinstance(ld, list):
                ld = ld[0]
            if not isinstance(ld, dict):
                continue

            # Handle @graph format (Yoast)
            items = ld.get("@graph", [ld])

            # First pass: collect Person entries
            for item in items:
                item_type = item.get("@type", "")
                if isinstance(item_type, list):
                    item_type = " ".join(item_type)
                if "Person" in item_type:
                    pid = item.get("@id", "")
                    pname = item.get("name", "")
                    if pid and pname:
                        person_map[pid] = pname

            # Second pass: find Article/BlogPosting
            for item in items:
                item_type = item.get("@type", "")
                if isinstance(item_type, list):
                    item_type = " ".join(item_type)
                if any(t in item_type for t in ("Article", "BlogPosting", "NewsArticle")):
                    date_published = item.get("datePublished", date_published)
                    a = item.get("author", {})
                    if isinstance(a, dict):
                        # May be a reference (@id) or direct name
                        aname = a.get("name", "")
                        if aname:
                            author = aname
                        elif a.get("@id") and a["@id"] in person_map:
                            author = person_map[a["@id"]]
                    elif isinstance(a, list) and a:
                        author = a[0].get("name", author)
                    kw = item.get("keywords", [])
                    if isinstance(kw, str):
                        tags = [t.strip() for t in kw.split(",")]
                    elif isinstance(kw, list):
                        tags = kw
        except (json.JSONDecodeError, TypeError):
            continue

    # Fallback: meta tags for author/date
    if not date_published:
        date_meta = soup.find("meta", property="article:published_time")
        if date_meta:
            date_published = date_meta.get("content", "")

    if author == "Trainerize":
        author_meta = soup.find("meta", attrs={"name": "author"})
        if author_meta:
            author = author_meta.get("content", author)

    # Slug from URL
    slug = url.rstrip("/").split("/")[-1]
    if not slug:
        slug = "unknown"

    wc = word_count(body_md)

    # Featured image
    image_url = None
    og_img = soup.find("meta", property="og:image")
    if og_img:
        image_url = og_img.get("content")

    return {
        "source_id": slug,
        "source_domain": DOMAIN,
        "source_url": url,
        "title": title,
        "author": author,
        "date_published": date_published,
        "tags": tags,
        "content_type": "coach_education",
        "source_tier": SOURCE_TIER,
        "word_count": wc,
        "image_url": image_url,
        "body_md": body_md,
    }


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------


def save_article(article: dict):
    tags_str = json.dumps(article["tags"])
    image_str = f'"{_escape_yaml(article["image_url"])}"' if article["image_url"] else "null"

    frontmatter = "\n".join([
        "---",
        f'source_id: "{_escape_yaml(article["source_id"])}"',
        f'source_domain: "{_escape_yaml(article["source_domain"])}"',
        f'source_url: "{_escape_yaml(article["source_url"])}"',
        f'title: "{_escape_yaml(article["title"])}"',
        f'author: "{_escape_yaml(article["author"])}"',
        f'date_published: "{_escape_yaml(article["date_published"])}"',
        f"tags: {tags_str}",
        f'content_type: "{_escape_yaml(article["content_type"])}"',
        f'source_tier: "{_escape_yaml(article["source_tier"])}"',
        f'word_count: {article["word_count"]}',
        f"image_url: {image_str}",
        "---",
    ]) + "\n"

    path = os.path.join(ARTICLES_DIR, f"{article['source_id']}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(frontmatter + "\n" + article["body_md"] + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    os.makedirs(ARTICLES_DIR, exist_ok=True)

    print(f"Trainerize Blog Scraper")
    print(f"Articles dir: {ARTICLES_DIR}")
    print()

    # Fetch all URLs from sitemaps
    print("Fetching post URLs from sitemaps...")
    all_urls = fetch_all_post_urls()
    print(f"Total URLs: {len(all_urls)}")
    print()

    scraped = 0
    skipped = 0
    failed = 0

    for i, url in enumerate(all_urls, 1):
        slug = url.rstrip("/").split("/")[-1]
        if not slug:
            failed += 1
            continue

        # Resume support
        if os.path.isfile(os.path.join(ARTICLES_DIR, f"{slug}.md")):
            skipped += 1
            if i % PROGRESS_EVERY == 0:
                print(f"  Progress: {i}/{len(all_urls)} "
                      f"({scraped} scraped, {skipped} skipped, {failed} failed)")
            continue

        html_bytes = fetch_url(url)
        if not html_bytes:
            failed += 1
            time.sleep(DELAY)
            continue

        article = extract_article(html_bytes, url)
        if article:
            save_article(article)
            scraped += 1
        else:
            failed += 1

        if i % PROGRESS_EVERY == 0:
            print(f"  Progress: {i}/{len(all_urls)} "
                  f"({scraped} scraped, {skipped} skipped, {failed} failed)")

        time.sleep(DELAY)

    print()
    print("=" * 60)
    print(f"SUMMARY for {DOMAIN}")
    print(f"  Scraped: {scraped}")
    print(f"  Skipped: {skipped}")
    print(f"  Failed:  {failed}")
    print(f"  Total:   {scraped + skipped + failed}")
    print("=" * 60)


if __name__ == "__main__":
    main()
