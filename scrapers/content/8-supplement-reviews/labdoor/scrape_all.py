#!/usr/bin/env python3
"""
Scrape labdoor.com product reviews.

1,164 product reviews in sitemap. Server-rendered HTML with quality scores
as data attributes on ranking pages. Individual review pages have lab test data.
source_tier: tier2, source_category: 8_supplement_reviews
"""

import json
import os
import re
import time
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error

from bs4 import BeautifulSoup
from markdownify import markdownify as md

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

DOMAIN = "labdoor.com"
SITEMAP_URL = "https://labdoor.com/sitemap_index.xml"
SOURCE_TIER = "tier2"
USER_AGENT = "Mozilla/5.0 (compatible; GymZilla/1.0)"
DELAY = 1.0


def _escape_yaml(s: str) -> str:
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    return s


def build_frontmatter(article: dict) -> str:
    tags_str = json.dumps(article.get("tags", []))
    img = article.get("image_url")
    img_str = f'"{_escape_yaml(img)}"' if img else "null"
    lines = [
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
        f"image_url: {img_str}",
        "---",
    ]
    return "\n".join(lines) + "\n"


def fetch(url: str, retries: int = 2) -> bytes | None:
    for attempt in range(1 + retries):
        req = urllib.request.Request(url)
        req.add_header("User-Agent", USER_AGENT)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            return None
        except (urllib.error.URLError, OSError):
            if attempt < retries:
                time.sleep(3)
                continue
            return None
    return None


def get_review_urls() -> list[str]:
    """Get all review URLs from sitemap."""
    # Fetch sitemap index
    data = fetch(SITEMAP_URL)
    if not data:
        print("ERROR: Could not fetch sitemap index")
        return []

    root = ET.fromstring(data)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    # Find product_reviews sitemap
    review_sitemap_url = None
    for sitemap in root.findall(".//sm:sitemap", ns):
        loc = sitemap.find("sm:loc", ns)
        if loc is not None and "product_reviews" in (loc.text or ""):
            review_sitemap_url = loc.text.strip()
            break

    if not review_sitemap_url:
        print("ERROR: Could not find product_reviews sitemap")
        return []

    print(f"  Found reviews sitemap: {review_sitemap_url}")
    data = fetch(review_sitemap_url)
    if not data:
        print("ERROR: Could not fetch reviews sitemap")
        return []

    root = ET.fromstring(data)
    urls = []
    for loc in root.findall(".//sm:loc", ns):
        u = loc.text.strip() if loc.text else ""
        if "/review/" in u:
            urls.append(u)
    return urls


def scrape_review(url: str) -> dict | None:
    """Scrape a single product review page."""
    data = fetch(url)
    if not data:
        return None

    html_str = data.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html_str, "lxml")

    # Title from h1 or og:title
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        og = soup.find("meta", property="og:title")
        if og:
            title = og.get("content", "")
    if not title:
        return None

    # Description from og:description
    description = ""
    og_desc = soup.find("meta", property="og:description")
    if og_desc:
        description = og_desc.get("content", "")

    # Image
    image_url = None
    og_img = soup.find("meta", property="og:image")
    if og_img:
        image_url = og_img.get("content", "")

    # Extract lab test data from KEY DATA widgets
    body_parts = []
    body_parts.append(f"# {title}\n")
    if description:
        body_parts.append(f"{description}\n")

    # Look for score/grade
    score_el = soup.find(class_="labdoorScoreLetterWrap")
    if score_el:
        grade = score_el.get_text(strip=True)
        body_parts.append(f"## Labdoor Grade: {grade}\n")

    # Look for widget data (lab test results)
    widgets = soup.find_all(class_="widget-percentage-header")
    for widget in widgets:
        section_name = widget.get_text(strip=True)
        body_parts.append(f"## {section_name}\n")

        # Find sibling items
        parent = widget.parent
        if parent:
            items = parent.find_all(class_="widget-percentage-item")
            for item in items:
                label = item.find(class_="widget-percentage-item-label")
                value = item.find(class_="widget-percentage-item-value")
                if label and value:
                    body_parts.append(f"- **{label.get_text(strip=True)}**: {value.get_text(strip=True)}")

    # Also get any general content sections
    content_sections = soup.find_all(["section", "div"], class_=re.compile(r"review|content|details", re.I))
    for section in content_sections:
        for tag in section.find_all(["script", "style", "nav"]):
            tag.decompose()
        text = section.get_text(strip=True)
        if len(text) > 50 and text not in "\n".join(body_parts):
            section_md = md(str(section), heading_style="ATX", strip=["img"]).strip()
            if section_md and len(section_md) > 50:
                body_parts.append(section_md)

    body_md = "\n\n".join(body_parts)
    if not body_md or len(body_md) < 50:
        # Fallback: just grab the whole main content
        main = soup.find("main") or soup.find("body")
        if main:
            for tag in main.find_all(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            body_md = md(str(main), heading_style="ATX", strip=["img"]).strip()

    if not body_md:
        return None

    wc = len(re.sub(r"[*_#\[\]()>~`|]", " ", body_md).split())
    slug = url.rstrip("/").split("/")[-1]

    # Tags from category
    tags = ["supplement review", "lab tested"]
    cat_link = soup.find("a", href=re.compile(r"/rankings/"))
    if cat_link:
        cat_name = cat_link.get_text(strip=True)
        if cat_name:
            tags.append(cat_name.lower())

    return {
        "source_id": slug,
        "source_domain": DOMAIN,
        "source_url": url,
        "title": title,
        "author": "Labdoor",
        "date_published": "",
        "tags": tags,
        "content_type": "supplement_review",
        "source_tier": SOURCE_TIER,
        "word_count": wc,
        "image_url": image_url,
        "body_md": body_md,
    }


def main():
    os.makedirs(ARTICLES_DIR, exist_ok=True)

    print(f"Scraping {DOMAIN}...")
    print(f"  Fetching review URLs from sitemap...")

    urls = get_review_urls()
    print(f"  Found {len(urls)} review URLs")
    print()

    scraped = 0
    skipped = 0
    failed = 0

    for i, url in enumerate(urls, 1):
        slug = url.rstrip("/").split("/")[-1]
        filepath = os.path.join(ARTICLES_DIR, f"{slug}.md")

        if os.path.isfile(filepath):
            skipped += 1
            continue

        article = scrape_review(url)
        if article is None:
            failed += 1
            if i % 50 == 0 or failed <= 5:
                print(f"  [{i}/{len(urls)}] FAILED: {slug}")
            continue

        content = build_frontmatter(article) + "\n" + article["body_md"] + "\n"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        scraped += 1

        if i % 50 == 0:
            print(f"  [{i}/{len(urls)}] scraped={scraped} skipped={skipped} failed={failed}")

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
