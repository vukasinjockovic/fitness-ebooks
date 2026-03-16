#!/usr/bin/env python3
"""
Scrape mayoclinic.org nutrition content: recipes, FAQs, and in-depth articles.

Mayo Clinic uses Next.js SSR. No anti-bot. Full content in HTML response.
source_tier: "tier1" (highest medical authority)

Sitemaps:
  - patient_consumer_recipe.xml: 574 recipes with full nutrition data
  - patient_consumer_faq.xml: 461 FAQs (filter ~151 nutrition-related)
  - patient_consumer_web.xml: 829 pages (filter nutrition-related)

Recipes have: ingredients, directions, nutrition per serving (cal, protein, fat,
carbs, fiber), serving size, diet tags (diabetes, heart-healthy, plant-based, etc.)

Body selector: .cmp-health-information-article with .cmp-text divs
Date: .cmp-health-information-article__date
JSON-LD: WebPage + BreadcrumbList only (no Article schema)

Resume-safe.

Usage:
    python3 scrape_all.py
    python3 scrape_all.py --limit 100
    python3 scrape_all.py --recipes-only
    python3 scrape_all.py --articles-only
"""

import argparse
import json
import os
import re
import time
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

SITEMAP_BASE = "https://www.mayoclinic.org"
SOURCE_DOMAIN = "mayoclinic.org"
SOURCE_TIER = "tier1"
SOURCE_CATEGORY = "3_nutrition_meal_planning"

# Sitemaps to fetch (from robots.txt)
RECIPE_SITEMAP = f"{SITEMAP_BASE}/patient_consumer_recipe.xml"
FAQ_SITEMAP = f"{SITEMAP_BASE}/patient_consumer_faq.xml"
WEB_SITEMAP = f"{SITEMAP_BASE}/patient_consumer_web.xml"

# Keywords for filtering non-recipe content
NUTRITION_KEYWORDS = [
    "nutrition", "diet", "food", "weight", "eating", "calorie",
    "vitamin", "protein", "fiber", "healthy-lifestyle",
    "recipes", "meal", "supplement", "cholesterol", "sodium",
    "fat", "carb", "sugar", "diabetes", "heart-healthy",
    "mediterranean", "vegetarian", "vegan", "plant-based",
    "weight-loss", "obesity", "bmi",
]

# Mayo blocks browser UAs on sitemaps AND content pages!
# Only bare requests (default Python urllib UA) get 200.
USER_AGENT = None  # Use Python default (no custom UA)
REQUEST_TIMEOUT = 30
CRAWL_DELAY = 5.0  # Mayo rate limits aggressively, be polite
PROGRESS_EVERY = 25

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def make_session() -> requests.Session:
    s = requests.Session()
    # Mayo blocks browser-like UAs but allows python-requests default.
    # Just keep the default headers from requests library.
    return s


# ---------------------------------------------------------------------------
# Sitemap parsing
# ---------------------------------------------------------------------------

def fetch_sitemap_urls(session: requests.Session, sitemap_url: str) -> list[str]:
    resp = session.get(sitemap_url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.content)
    urls = []
    for url_elem in root.findall("sm:url", SITEMAP_NS):
        loc = url_elem.find("sm:loc", SITEMAP_NS)
        if loc is not None and loc.text:
            urls.append(loc.text.strip())
    return urls


def is_nutrition_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(kw in path for kw in NUTRITION_KEYWORDS)


def get_all_urls(session: requests.Session, include_recipes: bool, include_articles: bool) -> tuple[list[str], list[str]]:
    """Return (recipe_urls, article_urls)."""
    recipe_urls = []
    article_urls = []

    if include_recipes:
        print(f"  Fetching recipe sitemap...")
        recipe_urls = fetch_sitemap_urls(session, RECIPE_SITEMAP)
        print(f"    -> {len(recipe_urls)} recipes")

    if include_articles:
        # FAQs
        print(f"  Fetching FAQ sitemap...")
        faq_urls = fetch_sitemap_urls(session, FAQ_SITEMAP)
        nutrition_faqs = [u for u in faq_urls if is_nutrition_url(u)]
        article_urls.extend(nutrition_faqs)
        print(f"    -> {len(faq_urls)} total, {len(nutrition_faqs)} nutrition FAQs")

        # Web pages
        print(f"  Fetching web pages sitemap...")
        web_urls = fetch_sitemap_urls(session, WEB_SITEMAP)
        nutrition_web = [u for u in web_urls if is_nutrition_url(u)]
        article_urls.extend(nutrition_web)
        print(f"    -> {len(web_urls)} total, {len(nutrition_web)} nutrition pages")

    return recipe_urls, article_urls


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------

def url_to_slug(url: str) -> str:
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    slug = parts[-1] if parts else "unknown"
    slug = re.sub(r"[^\w\-]", "-", slug).strip("-")
    return slug or "unknown"


def extract_metadata(soup: BeautifulSoup) -> dict:
    meta = {
        "title": "", "author": "",
        "date_published": "", "date_modified": "",
        "tags": [], "image_url": "", "description": "",
    }

    # Title
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        meta["title"] = og["content"]
        # Clean suffix
        meta["title"] = re.sub(r"\s*[-|]\s*Mayo Clinic\s*$", "", meta["title"])
    else:
        t = soup.find("title")
        if t:
            meta["title"] = re.sub(r"\s*[-|]\s*Mayo Clinic\s*$", "", t.get_text(strip=True))

    # Author/Byline
    byline = soup.select_one(".cmp-health-information-article__byline")
    if byline:
        meta["author"] = byline.get_text(strip=True)
    if not meta["author"]:
        meta["author"] = "Mayo Clinic Staff"

    # Date
    date_el = soup.select_one(".cmp-health-information-article__date")
    if date_el:
        date_text = date_el.get_text(strip=True)
        meta["date_published"] = _parse_mayo_date(date_text)

    # Image
    oi = soup.find("meta", property="og:image")
    if oi and oi.get("content"):
        meta["image_url"] = oi["content"]

    # Description
    desc = soup.find("meta", attrs={"name": "description"})
    if desc and desc.get("content"):
        meta["description"] = desc["content"]

    return meta


def _parse_mayo_date(text: str) -> str:
    """Parse Mayo date format like 'Dec. 24, 2025' to YYYY-MM-DD."""
    import calendar
    # Try ISO first
    m = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)

    # Month abbreviation mapping
    months = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "may": "05", "jun": "06", "jul": "07", "aug": "08",
        "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }

    m = re.search(r"(\w+)\.?\s+(\d{1,2}),?\s+(\d{4})", text)
    if m:
        month_str = m.group(1).lower()[:3]
        day = m.group(2).zfill(2)
        year = m.group(3)
        month = months.get(month_str, "01")
        return f"{year}-{month}-{day}"

    return text


def _extract_date(raw: str) -> str:
    if not raw:
        return ""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return match.group(1) if match else raw


def extract_recipe_data(soup: BeautifulSoup) -> dict:
    """Extract recipe-specific data: ingredients, directions, nutrition."""
    recipe = {
        "ingredients": [],
        "directions": [],
        "nutrition": {},
        "servings": "",
        "diet_tags": [],
    }

    # Ingredients - look for ingredient list items
    for li in soup.select("ul.cmp-recipe__ingredients li, .cmp-recipe-ingredients li, .recipe-ingredients li"):
        text = li.get_text(strip=True)
        if text:
            recipe["ingredients"].append(text)

    # If not found with specific selectors, try broader approach
    if not recipe["ingredients"]:
        ing_header = None
        for h in soup.find_all(["h2", "h3", "h4"]):
            if "ingredient" in h.get_text(strip=True).lower():
                ing_header = h
                break
        if ing_header:
            ul = ing_header.find_next("ul")
            if ul:
                for li in ul.find_all("li"):
                    text = li.get_text(strip=True)
                    if text:
                        recipe["ingredients"].append(text)

    # Directions
    for li in soup.select("ol.cmp-recipe__directions li, .cmp-recipe-directions li, .recipe-directions li"):
        text = li.get_text(strip=True)
        if text:
            recipe["directions"].append(text)

    if not recipe["directions"]:
        dir_header = None
        for h in soup.find_all(["h2", "h3", "h4"]):
            if "direction" in h.get_text(strip=True).lower() or "instruction" in h.get_text(strip=True).lower():
                dir_header = h
                break
        if dir_header:
            ol = dir_header.find_next("ol")
            if ol:
                for li in ol.find_all("li"):
                    text = li.get_text(strip=True)
                    if text:
                        recipe["directions"].append(text)

    # Nutrition facts - look for nutrition table/list
    nutrition_keywords = {
        "calories": "calories", "total fat": "total_fat", "fat": "total_fat",
        "cholesterol": "cholesterol", "sodium": "sodium",
        "carbohydrate": "carbohydrate", "total carbohydrate": "carbohydrate",
        "fiber": "fiber", "dietary fiber": "fiber",
        "protein": "protein",
        "saturated fat": "saturated_fat",
        "trans fat": "trans_fat",
        "sugar": "sugar", "total sugars": "sugar",
    }

    # Try structured nutrition elements
    for row in soup.select(".cmp-nutrition-facts tr, .nutrition-facts tr, .recipe-nutrition tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) >= 2:
            key = cells[0].get_text(strip=True).lower()
            val = cells[1].get_text(strip=True)
            for kw, field in nutrition_keywords.items():
                if kw in key:
                    recipe["nutrition"][field] = val
                    break

    # Also try dt/dd pairs
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            key = dt.get_text(strip=True).lower()
            val = dd.get_text(strip=True)
            for kw, field in nutrition_keywords.items():
                if kw in key:
                    recipe["nutrition"][field] = val
                    break

    # Broader text-based nutrition extraction
    if not recipe["nutrition"]:
        nut_section = None
        for h in soup.find_all(["h2", "h3", "h4"]):
            if "nutrition" in h.get_text(strip=True).lower():
                nut_section = h
                break
        if nut_section:
            next_el = nut_section.find_next_sibling()
            if next_el:
                text = next_el.get_text()
                for kw, field in nutrition_keywords.items():
                    pattern = rf"{kw}[:\s]+(\d+[\d.,]*\s*\w*)"
                    m = re.search(pattern, text, re.IGNORECASE)
                    if m:
                        recipe["nutrition"][field] = m.group(1).strip()

    # Servings
    for el in soup.find_all(string=re.compile(r"serv", re.IGNORECASE)):
        text = el.strip() if isinstance(el, str) else el.get_text(strip=True)
        m = re.search(r"(\d+)\s*serv", text, re.IGNORECASE)
        if m:
            recipe["servings"] = m.group(1)
            break

    # Diet tags
    for tag_el in soup.select(".cmp-recipe__tags a, .recipe-tags a, .diet-tags a"):
        tag = tag_el.get_text(strip=True)
        if tag:
            recipe["diet_tags"].append(tag)

    # Also check for tag-like content in specific areas
    for badge in soup.select(".badge, .tag, .label"):
        text = badge.get_text(strip=True).lower()
        diet_terms = ["diabetes", "heart-healthy", "low-sodium", "plant-based",
                       "healthy-carb", "weight management", "gluten-free"]
        for term in diet_terms:
            if term in text and term not in [t.lower() for t in recipe["diet_tags"]]:
                recipe["diet_tags"].append(text.title())

    return recipe


def extract_body(soup: BeautifulSoup, is_recipe: bool = False) -> str:
    """Extract article body."""
    # Mayo uses specific article classes
    content = soup.select_one(".cmp-health-information-article")
    if not content:
        content = soup.find("article")
    if not content:
        content = soup.select_one("main")
    if not content:
        return ""

    for tag in content.find_all(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()
    for cls in ["ad-container", "sidebar", "related-articles", "social-share", "navigation"]:
        for el in content.find_all(class_=lambda c: c and cls in c):
            el.decompose()
    for nav in content.find_all("nav"):
        nav.decompose()

    body_html = str(content)
    body_md = md(body_html, heading_style="ATX", strip=["img"])
    body_md = re.sub(r"\n{3,}", "\n\n", body_md)
    return body_md.strip()


def count_words(text: str) -> int:
    return len(text.split()) if text else 0


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------

def _escape_yaml(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_frontmatter(slug: str, url: str, meta: dict, is_recipe: bool = False,
                       recipe_data: dict | None = None) -> str:
    lines = ["---"]
    lines.append(f'source_id: "{slug}"')
    lines.append(f'source_domain: "{SOURCE_DOMAIN}"')
    lines.append(f'source_url: "{_escape_yaml(url)}"')
    lines.append(f'title: "{_escape_yaml(meta["title"])}"')
    if meta["author"]:
        lines.append(f'author: "{_escape_yaml(meta["author"])}"')
    if meta["date_published"]:
        lines.append(f'date_published: "{meta["date_published"]}"')
    if meta["description"]:
        lines.append(f'description: "{_escape_yaml(meta["description"])}"')
    if meta["tags"]:
        tag_list = ", ".join(f'"{_escape_yaml(t)}"' for t in meta["tags"][:20])
        lines.append(f"tags: [{tag_list}]")

    content_type = "recipe" if is_recipe else "article"
    lines.append(f'content_type: "{content_type}"')
    lines.append(f'source_tier: "{SOURCE_TIER}"')
    lines.append(f'source_category: "{SOURCE_CATEGORY}"')

    if meta.get("word_count"):
        lines.append(f'word_count: {meta["word_count"]}')
    if meta["image_url"]:
        lines.append(f'image_url: "{meta["image_url"]}"')

    # Recipe-specific fields
    if recipe_data:
        if recipe_data.get("servings"):
            lines.append(f'servings: {recipe_data["servings"]}')
        if recipe_data.get("nutrition"):
            lines.append("nutrition:")
            for key, val in recipe_data["nutrition"].items():
                lines.append(f'  {key}: "{_escape_yaml(str(val))}"')
        if recipe_data.get("diet_tags"):
            dt_list = ", ".join(f'"{_escape_yaml(t)}"' for t in recipe_data["diet_tags"])
            lines.append(f"diet_tags: [{dt_list}]")

    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fetch + scrape
# ---------------------------------------------------------------------------

def fetch_page(session: requests.Session, url: str) -> str | None:
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if resp.status_code == 404:
                return None
            if resp.status_code == 403:
                # Mayo sometimes 403s intermittently; retry with backoff
                if attempt < 2:
                    wait = 5 * (attempt + 1)
                    print(f"    HTTP 403, retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                return None
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = 5 * (attempt + 1)
                print(f"    HTTP {resp.status_code}, retrying in {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.text
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                requests.exceptions.HTTPError):
            if attempt < 2:
                time.sleep(5)
                continue
            return None
    return None


def scrape_one(session: requests.Session, url: str, is_recipe: bool,
               existing: set, stats: dict) -> None:
    slug = url_to_slug(url)
    prefix = "recipe-" if is_recipe else ""
    filename = f"{prefix}{slug}.md"

    if filename in existing:
        stats["skipped"] += 1
        return

    html = fetch_page(session, url)
    if html is None:
        stats["failed"] += 1
        return

    soup = BeautifulSoup(html, "lxml")
    meta = extract_metadata(soup)
    body_md = extract_body(soup, is_recipe)

    if not body_md or len(body_md) < 50:
        stats["failed"] += 1
        return

    recipe_data = None
    if is_recipe:
        recipe_data = extract_recipe_data(soup)

    meta["word_count"] = count_words(body_md)
    frontmatter = build_frontmatter(slug, url, meta, is_recipe, recipe_data)

    # For recipes, append structured ingredient/direction sections
    if is_recipe and recipe_data:
        extras = []
        if recipe_data["ingredients"]:
            extras.append("\n## Ingredients\n")
            for ing in recipe_data["ingredients"]:
                extras.append(f"- {ing}")
        if recipe_data["directions"]:
            extras.append("\n## Directions\n")
            for i, step in enumerate(recipe_data["directions"], 1):
                extras.append(f"{i}. {step}")
        if extras:
            body_md = body_md + "\n" + "\n".join(extras)

    content = f"{frontmatter}\n\n{body_md}\n"

    filepath = os.path.join(ARTICLES_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    existing.add(filename)
    stats["scraped"] += 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scrape Mayo Clinic nutrition content")
    parser.add_argument("--limit", type=int, default=0, help="Max per category (0=all)")
    parser.add_argument("--recipes-only", action="store_true")
    parser.add_argument("--articles-only", action="store_true")
    args = parser.parse_args()

    os.makedirs(ARTICLES_DIR, exist_ok=True)
    existing = set(os.listdir(ARTICLES_DIR))
    stats = {"scraped": 0, "skipped": 0, "failed": 0}

    print("Mayo Clinic Nutrition Scraper")
    print(f"Articles directory: {ARTICLES_DIR}")
    print(f"Existing files: {len(existing)}")

    session = make_session()

    include_recipes = not args.articles_only
    include_articles = not args.recipes_only

    try:
        recipe_urls, article_urls = get_all_urls(session, include_recipes, include_articles)
    except Exception as e:
        print(f"ERROR fetching sitemaps: {e}")
        return

    if args.limit > 0:
        recipe_urls = recipe_urls[:args.limit]
        article_urls = article_urls[:args.limit]

    total = len(recipe_urls) + len(article_urls)
    print(f"\nScraping {len(recipe_urls)} recipes + {len(article_urls)} articles = {total} total")

    # Recipes first
    if recipe_urls:
        print(f"\n--- Scraping {len(recipe_urls)} recipes ---")
        for i, url in enumerate(recipe_urls, 1):
            if i % PROGRESS_EVERY == 0:
                print(f"  Recipes: {i}/{len(recipe_urls)} | scraped={stats['scraped']} skipped={stats['skipped']}")
            scrape_one(session, url, True, existing, stats)
            time.sleep(CRAWL_DELAY)

    # Articles/FAQs
    if article_urls:
        print(f"\n--- Scraping {len(article_urls)} articles/FAQs ---")
        for i, url in enumerate(article_urls, 1):
            if i % PROGRESS_EVERY == 0:
                print(f"  Articles: {i}/{len(article_urls)} | scraped={stats['scraped']} skipped={stats['skipped']}")
            scrape_one(session, url, False, existing, stats)
            time.sleep(CRAWL_DELAY)

    session.close()

    print(f"\n{'='*60}")
    print(f"MAYO CLINIC SCRAPER - SUMMARY")
    print(f"{'='*60}")
    print(f"  Scraped:  {stats['scraped']}")
    print(f"  Skipped:  {stats['skipped']} (already existed)")
    print(f"  Failed:   {stats['failed']}")
    print(f"  Files:    {len(os.listdir(ARTICLES_DIR))}")


if __name__ == "__main__":
    main()
