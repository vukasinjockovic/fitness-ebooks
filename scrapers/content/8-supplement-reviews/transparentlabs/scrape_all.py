#!/usr/bin/env python3
"""
Scrape transparentlabs.com blog articles.

Shopify platform, ~386 articles. HTML scrape required (Shopify JSON API disabled).
Custom theme with sb-blog-post__* CSS classes.
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

DOMAIN = "transparentlabs.com"
SITEMAP_URL = "https://www.transparentlabs.com/sitemap.xml"
SOURCE_TIER = "tier2"
USER_AGENT = "Mozilla/5.0 (compatible; GymZilla/1.0)"
DELAY = 1.5  # Polite for Shopify + Cloudflare


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


def get_article_urls() -> list[str]:
    """Get blog article URLs from sitemap."""
    # Fetch sitemap index
    data = fetch(SITEMAP_URL)
    if not data:
        print("ERROR: Could not fetch sitemap index")
        return []

    root = ET.fromstring(data)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    # Find blogs sitemap
    blog_sitemap_url = None
    for sitemap in root.findall(".//sm:sitemap", ns):
        loc = sitemap.find("sm:loc", ns)
        if loc is not None and "blogs" in (loc.text or ""):
            blog_sitemap_url = loc.text.strip()
            break

    if not blog_sitemap_url:
        print("ERROR: Could not find blogs sitemap")
        return []

    print(f"  Found blogs sitemap: {blog_sitemap_url}")
    data = fetch(blog_sitemap_url)
    if not data:
        print("ERROR: Could not fetch blogs sitemap")
        return []

    root = ET.fromstring(data)
    urls = []
    for loc in root.findall(".//sm:loc", ns):
        u = loc.text.strip() if loc.text else ""
        # Only actual articles, not index/listing pages
        if "/blogs/" in u and u.count("/") > 4:
            urls.append(u)
    return urls


def scrape_article(url: str) -> dict | None:
    """Scrape a single Transparent Labs article."""
    data = fetch(url)
    if not data:
        return None

    html_str = data.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html_str, "lxml")

    # Title from sb-blog-post__title or og:title
    title = ""
    title_el = soup.find(class_="sb-blog-post__title")
    if title_el:
        title = title_el.get_text(strip=True)
    if not title:
        og = soup.find("meta", property="og:title")
        if og:
            title = og.get("content", "")
    if not title:
        return None

    # Author
    author = "Transparent Labs"
    author_el = soup.find(class_="sb-blog-post__author-name")
    if author_el:
        author = author_el.get_text(strip=True)

    # Date
    date_pub = ""
    date_el = soup.find(class_="sb-blog-post__date-value")
    if date_el:
        date_pub = date_el.get_text(strip=True)

    # Category
    tags = []
    cat_el = soup.find(class_="sb-blog-post__category")
    if cat_el:
        tags.append(cat_el.get_text(strip=True).lower())
    label_el = soup.find(class_="sb-blog-post__label")
    if label_el:
        label = label_el.get_text(strip=True).lower()
        if label and label not in tags:
            tags.append(label)

    # Image
    image_url = None
    og_img = soup.find("meta", property="og:image")
    if og_img:
        image_url = og_img.get("content", "")

    # Body content
    body_el = soup.find(class_="sb-blog-post__content")
    if not body_el:
        body_el = soup.find("article") or soup.find("div", class_="article-content")
    if not body_el:
        return None

    for tag in body_el.find_all(["script", "style", "nav", "footer"]):
        tag.decompose()
    # Remove product embed sections
    for tag in body_el.find_all(class_=re.compile(r"sb-blog-featured-product")):
        tag.decompose()

    body_md = md(str(body_el), heading_style="ATX", strip=["img"]).strip()
    if not body_md or len(body_md) < 100:
        return None

    wc = len(re.sub(r"[*_#\[\]()>~`|]", " ", body_md).split())
    slug = url.rstrip("/").split("/")[-1]

    return {
        "source_id": slug,
        "source_domain": DOMAIN,
        "source_url": url,
        "title": title,
        "author": author,
        "date_published": date_pub,
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
    print(f"  Fetching article URLs from sitemap...")

    urls = get_article_urls()
    print(f"  Found {len(urls)} article URLs")
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

        article = scrape_article(url)
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
