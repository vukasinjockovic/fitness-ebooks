#!/usr/bin/env python3
"""
Scrape muscleandstrength.com articles and recipes via the Wayback Machine.

The live site blocks all automated access (Cloudflare). Wayback Machine has
full content with JSON-LD structured data. This scraper:

1. Discovers URLs from Wayback-cached sitemaps (Phase 1)
2. Looks up latest Wayback snapshot for each URL via CDX API (Phase 2)
3. Fetches raw HTML, extracts content + JSON-LD structured data
4. Saves as articles/{slug}.md with YAML frontmatter

Usage:
    python3 scrape_all.py                        # Full pipeline: discover + scrape
    python3 scrape_all.py --limit 10             # Scrape first 10 URLs
    python3 scrape_all.py --workers 10           # 10 parallel workers (default)
    python3 scrape_all.py --skip-discovery       # Skip URL discovery, use cached URLs
    python3 scrape_all.py --articles-only        # Only scrape articles (skip recipes)
    python3 scrape_all.py --recipes-only         # Only scrape recipes (skip articles)
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
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
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
URLS_CACHE_FILE = os.path.join(SCRIPT_DIR, "discovered_urls.json")
CDX_CACHE_FILE = os.path.join(SCRIPT_DIR, "cdx_cache.json")

USER_AGENT = (
    "GymZilla-ContentScraper/1.0 "
    "(fitness research project; polite; contact@gymzillatribe.com)"
)

# Sitemap URLs in Wayback (Feb 2024 snapshot has the most complete sitemap)
SITEMAP_WAYBACK_URLS = [
    "https://web.archive.org/web/20240315045704id_/https://www.muscleandstrength.com/sitemap.xml?page=1",
    "https://web.archive.org/web/20240315045733id_/https://www.muscleandstrength.com/sitemap.xml?page=2",
]

# Content type prefixes we want to scrape
WANTED_PREFIXES = {
    "articles": "article",
    "recipes": "recipe",
    "expert-guides": "article",
    "workouts": "workout",
    "interviews": "article",
    "transformations": "article",
    "natural": "article",
}

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
# HTTP Helpers
# ---------------------------------------------------------------------------


def _make_request(url: str, timeout: int = 60) -> bytes | None:
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


# ---------------------------------------------------------------------------
# Phase 1: URL Discovery from Wayback Sitemaps
# ---------------------------------------------------------------------------


def discover_urls_from_sitemaps() -> dict[str, list[str]]:
    """
    Fetch sitemaps from Wayback and extract article/recipe URLs.
    Returns dict mapping content_type -> list of URLs.
    """
    all_urls = defaultdict(list)
    seen = set()

    for sitemap_url in SITEMAP_WAYBACK_URLS:
        print(f"Fetching sitemap: {sitemap_url[:80]}...")
        time.sleep(1)
        data = _make_request(sitemap_url, timeout=120)
        if not data:
            print(f"  [WARN] Failed to fetch sitemap")
            continue

        xml_text = data.decode("utf-8", errors="replace")

        # Parse XML -- sitemaps use <url><loc>...</loc></url>
        # Handle namespace
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            # Try to clean up Wayback injections
            # Remove any HTML comments or Wayback toolbar
            xml_text = re.sub(r'<!--.*?-->', '', xml_text, flags=re.DOTALL)
            try:
                root = ET.fromstring(xml_text)
            except ET.ParseError as e:
                print(f"  [ERROR] XML parse failed: {e}")
                # Fallback: regex extract URLs
                urls = re.findall(
                    r'<loc>(https?://www\.muscleandstrength\.com/[^<]+)</loc>',
                    xml_text
                )
                for url in urls:
                    url = url.strip()
                    if url not in seen:
                        seen.add(url)
                        ctype = classify_url(url)
                        if ctype:
                            all_urls[ctype].append(url)
                continue

        # Handle XML namespaces
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = root.findall(".//sm:loc", ns)
        if not locs:
            # Try without namespace
            locs = root.findall(".//loc")

        for loc in locs:
            url = loc.text.strip() if loc.text else ""
            if not url or url in seen:
                continue
            seen.add(url)
            ctype = classify_url(url)
            if ctype:
                all_urls[ctype].append(url)

    print(f"\nURL Discovery Results:")
    for ctype, urls in sorted(all_urls.items()):
        print(f"  {ctype}: {len(urls)} URLs")
    print(f"  Total: {sum(len(v) for v in all_urls.values())} URLs")

    return dict(all_urls)


def classify_url(url: str) -> str | None:
    """Classify a muscleandstrength.com URL by content type. Returns None if unwanted."""
    path = urllib.parse.urlparse(url).path.strip("/")
    parts = path.split("/")
    if not parts or not parts[0]:
        return None

    prefix = parts[0]
    if prefix in WANTED_PREFIXES:
        return WANTED_PREFIXES[prefix]

    return None


def save_discovered_urls(urls_by_type: dict, filepath: str):
    """Save discovered URLs to JSON cache."""
    with open(filepath, "w") as f:
        json.dump(urls_by_type, f, indent=2)
    print(f"Saved {sum(len(v) for v in urls_by_type.values())} URLs to {filepath}")


def load_discovered_urls(filepath: str) -> dict | None:
    """Load discovered URLs from JSON cache."""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


# ---------------------------------------------------------------------------
# Phase 2: CDX Lookup
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


def fetch_cdx_timestamp(url: str) -> str | None:
    """Query CDX API for the best (most recent) Wayback timestamp for a URL."""
    cdx_url = build_cdx_url(url)
    data = _make_request(cdx_url, timeout=30)
    if not data:
        return None
    try:
        cdx_json = json.loads(data)
        if not cdx_json or len(cdx_json) < 2:
            return None
        # Last data row (skip header at index 0)
        return cdx_json[-1][0]
    except (json.JSONDecodeError, IndexError, KeyError):
        return None


def build_wayback_url(timestamp: str, original_url: str) -> str:
    """Build a Wayback fetch URL with id_ suffix (no toolbar injection)."""
    return f"https://web.archive.org/web/{timestamp}id_/{original_url}"


def save_cdx_cache(cache: dict):
    """Save CDX timestamp cache to JSON file."""
    with open(CDX_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def load_cdx_cache() -> dict:
    """Load CDX timestamp cache from JSON file."""
    if not os.path.exists(CDX_CACHE_FILE):
        return {}
    try:
        with open(CDX_CACHE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


# ---------------------------------------------------------------------------
# URL Parsing
# ---------------------------------------------------------------------------


def extract_slug(url: str) -> str:
    """Extract slug from URL path, stripping .html extension."""
    path = urllib.parse.urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "unknown"
    slug = parts[-1]
    # Remove .html extension
    if slug.endswith(".html"):
        slug = slug[:-5]
    return slug


def extract_section(url: str) -> str:
    """Extract the first path segment (articles, recipes, workouts, etc.)."""
    path = urllib.parse.urlparse(url).path.strip("/")
    parts = path.split("/")
    return parts[0] if parts else "unknown"


def make_source_id(section: str, slug: str) -> str:
    """Build source_id from section and slug."""
    return f"mas-{section}-{slug}"


# ---------------------------------------------------------------------------
# Content Extraction
# ---------------------------------------------------------------------------


def extract_json_ld(soup) -> dict | None:
    """Extract JSON-LD structured data."""
    ld_scripts = soup.find_all("script", type="application/ld+json")
    for script in ld_scripts:
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
            # Could be a list or a single object
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") in (
                        "Article", "Recipe", "NewsArticle", "BlogPosting"
                    ):
                        return item
            elif isinstance(data, dict):
                if data.get("@type") in (
                    "Article", "Recipe", "NewsArticle", "BlogPosting"
                ):
                    return data
                # Check @graph
                if "@graph" in data:
                    for item in data["@graph"]:
                        if isinstance(item, dict) and item.get("@type") in (
                            "Article", "Recipe", "NewsArticle", "BlogPosting"
                        ):
                            return item
        except json.JSONDecodeError:
            continue
    return None


def extract_recipe_from_jsonld(json_ld: dict) -> dict:
    """Extract recipe-specific data from JSON-LD Recipe schema."""
    recipe = {}
    recipe["ingredients"] = json_ld.get("recipeIngredient", [])

    # Instructions can be text or HowToStep objects
    instructions_raw = json_ld.get("recipeInstructions", [])
    instructions = []
    if isinstance(instructions_raw, str):
        # HTML or plain text
        instructions = [instructions_raw]
    elif isinstance(instructions_raw, list):
        for step in instructions_raw:
            if isinstance(step, str):
                instructions.append(step)
            elif isinstance(step, dict):
                text = step.get("text", step.get("name", ""))
                if text:
                    instructions.append(text)
    recipe["instructions"] = instructions

    # Nutrition
    nutrition = json_ld.get("nutrition", {})
    if isinstance(nutrition, dict):
        recipe["nutrition"] = {
            "calories": nutrition.get("calories", ""),
            "protein": nutrition.get("proteinContent", ""),
            "carbs": nutrition.get("carbohydrateContent", ""),
            "fat": nutrition.get("fatContent", ""),
            "fiber": nutrition.get("fiberContent", ""),
            "sugar": nutrition.get("sugarContent", ""),
            "sodium": nutrition.get("sodiumContent", ""),
        }
    else:
        recipe["nutrition"] = {}

    recipe["prep_time"] = json_ld.get("prepTime", "")
    recipe["cook_time"] = json_ld.get("cookTime", "")
    recipe["total_time"] = json_ld.get("totalTime", "")
    recipe["yield"] = json_ld.get("recipeYield", "")
    recipe["category"] = json_ld.get("recipeCategory", "")

    return recipe


def extract_content(html: str, url: str, content_type: str) -> dict:
    """
    Extract title, body markdown, metadata from a M&S page.

    Returns dict with all metadata and body content.
    """
    soup = BeautifulSoup(html, "lxml")

    # 1. JSON-LD structured data
    json_ld = extract_json_ld(soup)

    # 2. Title
    title = ""
    if json_ld:
        title = json_ld.get("headline", json_ld.get("name", ""))
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

    # 3. Author
    author = ""
    if json_ld:
        author_data = json_ld.get("author", {})
        if isinstance(author_data, dict):
            author = author_data.get("name", "")
        elif isinstance(author_data, list) and author_data:
            author = author_data[0].get("name", "") if isinstance(author_data[0], dict) else str(author_data[0])
        elif isinstance(author_data, str):
            author = author_data

    # 4. Dates
    date_published = None
    date_modified = None
    if json_ld:
        date_published = json_ld.get("datePublished")
        date_modified = json_ld.get("dateModified")

    # 5. Description
    description = ""
    if json_ld:
        description = json_ld.get("description", "")
    if not description:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            description = meta_desc.get("content", "")

    # 6. Image
    image_url = ""
    if json_ld:
        img = json_ld.get("image", "")
        if isinstance(img, list):
            image_url = img[0] if img else ""
        elif isinstance(img, dict):
            image_url = img.get("url", "")
        else:
            image_url = str(img)

    # 7. Recipe-specific data
    recipe_data = None
    if content_type == "recipe" and json_ld and json_ld.get("@type") == "Recipe":
        recipe_data = extract_recipe_from_jsonld(json_ld)

    # 8. Extract article body
    body_el = None

    # M&S uses Drupal-style selectors
    for selector in [
        "div.field-name-body",
        "div.node-article .field-items",
        "div.node-recipe .field-items",
        "article .field-name-body",
        "div.field-name-field-recipe",
        ".node-article",
        ".node-recipe",
        "article",
        "main",
        "[role='main']",
    ]:
        found = soup.select(selector)
        if found:
            best = max(found, key=lambda el: len(el.get_text(strip=True)))
            if len(best.get_text(strip=True)) > 100:
                body_el = best
                break

    if not body_el:
        body_el = soup.find("body")

    body_md = ""
    if body_el:
        # Remove unwanted elements
        for sel in [
            "[class*='sidebar']", "[class*='comment']",
            "[class*='ad-']", "[class*='social']",
            "[class*='related']", "[class*='promo']",
            "script", "style", "noscript", "iframe",
            "nav", "footer", "header",
        ]:
            try:
                for el in body_el.select(sel):
                    el.decompose()
            except Exception:
                pass

        body_html = str(body_el)
        body_md = md(body_html, heading_style="ATX", strip=["img"])
        body_md = re.sub(r"\n{3,}", "\n\n", body_md)
        body_md = body_md.strip()

    # For recipes, append structured recipe data as markdown
    if recipe_data and recipe_data.get("ingredients"):
        recipe_md = "\n\n## Ingredients\n\n"
        for ing in recipe_data["ingredients"]:
            # Clean up any HTML in ingredients
            clean_ing = re.sub(r'<[^>]+>', '', str(ing)).strip()
            if clean_ing:
                recipe_md += f"- {clean_ing}\n"

        if recipe_data.get("instructions"):
            recipe_md += "\n## Instructions\n\n"
            for i, step in enumerate(recipe_data["instructions"], 1):
                clean_step = re.sub(r'<[^>]+>', '', str(step)).strip()
                if clean_step:
                    recipe_md += f"{i}. {clean_step}\n"

        if recipe_data.get("nutrition"):
            nutr = recipe_data["nutrition"]
            recipe_md += "\n## Nutrition Facts\n\n"
            for key, val in nutr.items():
                if val:
                    recipe_md += f"- **{key.title()}:** {val}\n"

        # Append recipe data if body doesn't already contain it
        if "Ingredients" not in body_md[:500]:
            body_md += recipe_md

    return {
        "title": html_unescape(title) if title else "",
        "author": author,
        "date_published": date_published,
        "date_modified": date_modified,
        "description": description,
        "image_url": image_url,
        "body_md": body_md,
        "content_type": content_type,
        "recipe_data": recipe_data,
        "has_json_ld": json_ld is not None,
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


def generate_tags(section: str, slug: str, content_type: str) -> list[str]:
    """Generate tags from section, slug, and content type."""
    tags = [content_type]
    if section and section != content_type:
        tags.append(section)
    slug_words = [w.lower() for w in slug.split("-") if len(w) > 2]
    slug_words = [w for w in slug_words if w not in STOP_WORDS]
    for word in slug_words[:8]:
        if word not in tags:
            tags.append(word)
    return tags


def build_frontmatter(
    source_id: str,
    source_url: str,
    title: str,
    author: str,
    date_published: str | None,
    date_modified: str | None,
    description: str,
    content_type: str,
    tags: list[str],
    wc: int,
    wayback_timestamp: str,
    image_url: str,
    recipe_data: dict | None = None,
) -> str:
    """Build YAML frontmatter string."""
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    safe_desc = description.replace("\\", "\\\\").replace('"', '\\"')
    safe_author = author.replace("\\", "\\\\").replace('"', '\\"')
    tags_str = json.dumps(tags)

    lines = [
        "---",
        f'source_id: "{source_id}"',
        'source_domain: "muscleandstrength.com"',
        f'source_url: "{source_url}"',
        f'title: "{safe_title}"',
        f'author: "{safe_author}"',
        f'date_published: {json.dumps(date_published)}',
        f'date_modified: {json.dumps(date_modified)}',
        f'description: "{safe_desc}"',
        f'content_type: "{content_type}"',
        f"tags: {tags_str}",
        'source_tier: "tier2"',
        'source_category: "2_bodybuilding_training"',
        f"word_count: {wc}",
        f'image_url: {json.dumps(image_url if image_url else None)}',
        f'wayback_timestamp: "{wayback_timestamp}"',
    ]

    # Add recipe nutrition to frontmatter if available
    if recipe_data and recipe_data.get("nutrition"):
        nutr = recipe_data["nutrition"]
        lines.append(f'nutrition_calories: {json.dumps(nutr.get("calories", ""))}')
        lines.append(f'nutrition_protein: {json.dumps(nutr.get("protein", ""))}')
        lines.append(f'nutrition_carbs: {json.dumps(nutr.get("carbs", ""))}')
        lines.append(f'nutrition_fat: {json.dumps(nutr.get("fat", ""))}')
    if recipe_data and recipe_data.get("yield"):
        lines.append(f'recipe_yield: {json.dumps(recipe_data["yield"])}')

    lines.append("---")
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
    url: str,
    content_type: str,
    cdx_cache: dict,
    cdx_cache_lock: Lock,
    articles_dir: str,
    raw_dir: str,
) -> dict | None:
    """Scrape a single URL. Returns article metadata dict or None on failure."""
    section = extract_section(url)
    slug = extract_slug(url)
    source_id = make_source_id(section, slug)

    # Resume support
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
    content = extract_content(html, url, content_type)
    if not content["body_md"] or len(content["body_md"]) < 50:
        with _print_lock:
            print(f"  [SKIP] Empty/minimal content for {url}")
        return {"source_id": source_id, "status": "empty_content"}

    # Build article
    tags = generate_tags(section, slug, content_type)
    wc = word_count(content["body_md"])
    frontmatter = build_frontmatter(
        source_id=source_id,
        source_url=url,
        title=content["title"],
        author=content["author"],
        date_published=content["date_published"],
        date_modified=content["date_modified"],
        description=content["description"],
        content_type=content_type,
        tags=tags,
        wc=wc,
        wayback_timestamp=timestamp,
        image_url=content["image_url"],
        recipe_data=content.get("recipe_data"),
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
        "content_type": content_type,
        "author": content["author"],
        "timestamp": timestamp,
        "path": article_path,
        "has_json_ld": content["has_json_ld"],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Scrape muscleandstrength.com via Wayback Machine"
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Limit to first N URLs per type (0 = all)"
    )
    parser.add_argument(
        "--workers", type=int, default=MAX_WORKERS,
        help=f"Thread pool size (default: {MAX_WORKERS})"
    )
    parser.add_argument(
        "--skip-discovery", action="store_true",
        help="Skip URL discovery, use cached URLs"
    )
    parser.add_argument(
        "--articles-only", action="store_true",
        help="Only scrape articles (skip recipes)"
    )
    parser.add_argument(
        "--recipes-only", action="store_true",
        help="Only scrape recipes (skip articles)"
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Ignore CDX cache, re-fetch all timestamps"
    )
    args = parser.parse_args()

    # Phase 1: URL Discovery
    urls_by_type = None
    if args.skip_discovery:
        urls_by_type = load_discovered_urls(URLS_CACHE_FILE)
        if urls_by_type:
            print(f"Loaded cached URLs from {URLS_CACHE_FILE}")
        else:
            print("No cached URLs found, running discovery...")

    if not urls_by_type:
        print("=== Phase 1: URL Discovery from Wayback Sitemaps ===")
        urls_by_type = discover_urls_from_sitemaps()
        save_discovered_urls(urls_by_type, URLS_CACHE_FILE)

    # Filter by content type if requested
    if args.articles_only:
        urls_by_type = {k: v for k, v in urls_by_type.items() if k != "recipe"}
    elif args.recipes_only:
        urls_by_type = {k: v for k, v in urls_by_type.items() if k == "recipe"}

    # Build flat list of (url, content_type) pairs
    all_entries = []
    for ctype, urls in sorted(urls_by_type.items()):
        for url in urls:
            all_entries.append((url, ctype))

    print(f"\nTotal URLs to process: {len(all_entries)}")
    for ctype, urls in sorted(urls_by_type.items()):
        count = len(urls)
        if args.limit > 0:
            count = min(count, args.limit)
        print(f"  {ctype}: {count}")

    # Apply limit per type
    if args.limit > 0:
        limited = []
        type_counts = Counter()
        for url, ctype in all_entries:
            if type_counts[ctype] < args.limit:
                limited.append((url, ctype))
                type_counts[ctype] += 1
        all_entries = limited
        print(f"After limit: {len(all_entries)} URLs")

    # Load CDX cache
    cdx_cache = {} if args.no_cache else load_cdx_cache()
    if cdx_cache:
        print(f"Loaded CDX cache with {len(cdx_cache)} entries")
    cdx_cache_lock = Lock()

    # Ensure output dirs exist
    os.makedirs(ARTICLES_DIR, exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)

    # Phase 2: Scrape
    print(f"\n=== Phase 2: Scraping {len(all_entries)} URLs with {args.workers} workers ===")
    stats = {"success": 0, "skipped": 0, "failed": 0, "total": len(all_entries)}
    results = []
    start_time = time.time()

    if args.workers > 1 and len(all_entries) > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {}
            for url, ctype in all_entries:
                f = executor.submit(
                    scrape_single, url, ctype, cdx_cache, cdx_cache_lock,
                    ARTICLES_DIR, RAW_DIR,
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

                if i % 100 == 0 or i == len(all_entries):
                    elapsed = time.time() - start_time
                    rate = i / elapsed if elapsed > 0 else 0
                    with _print_lock:
                        print(
                            f"Progress: {i}/{len(all_entries)} "
                            f"(success={stats['success']}, skipped={stats['skipped']}, "
                            f"failed={stats['failed']}) "
                            f"[{rate:.1f} URLs/sec, {elapsed:.0f}s elapsed]"
                        )

                # Save CDX cache periodically
                if i % 200 == 0:
                    save_cdx_cache(cdx_cache)
    else:
        for i, (url, ctype) in enumerate(all_entries, 1):
            try:
                result = scrape_single(
                    url, ctype, cdx_cache, cdx_cache_lock,
                    ARTICLES_DIR, RAW_DIR,
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

            if i % 25 == 0 or i == len(all_entries):
                elapsed = time.time() - start_time
                print(
                    f"Progress: {i}/{len(all_entries)} "
                    f"(success={stats['success']}, skipped={stats['skipped']}, "
                    f"failed={stats['failed']}) [{elapsed:.0f}s elapsed]"
                )

    # Save CDX cache
    save_cdx_cache(cdx_cache)
    print(f"Saved CDX cache ({len(cdx_cache)} entries)")

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

    # Breakdown by content type
    successes = [r for r in results if r.get("status") == "success"]
    if successes:
        type_counts = Counter(r.get("content_type", "unknown") for r in successes)
        print(f"\n=== SUCCESS BY TYPE ===")
        for ctype, count in type_counts.most_common():
            print(f"  {ctype}: {count}")

        show = successes[:5]
        print(f"\n=== SAMPLE SUCCESSFUL SCRAPES ({len(show)} of {len(successes)}) ===")
        for r in show:
            print(
                f"  [{r.get('content_type', '?')}] {r['title'][:60]} "
                f"({r['word_count']} words, by {r.get('author', 'unknown')[:30]})"
            )

    # Failure breakdown
    failures = [r for r in results if r.get("status") not in ("success", "skipped")]
    if failures:
        fail_types = Counter(r["status"] for r in failures)
        print(f"\n=== FAILURE BREAKDOWN ===")
        for ftype, count in fail_types.most_common():
            print(f"  {ftype}: {count}")


if __name__ == "__main__":
    main()
