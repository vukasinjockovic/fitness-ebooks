#!/usr/bin/env python3
"""
Mob.co.uk Recipe Scraper
========================
Scrapes recipe pages from mob.co.uk using __NEXT_DATA__ JSON embedded in HTML.
Organizes output by recipe category folders.

Usage:
    python3 scrape_recipes.py                  # scrape all recipes
    python3 scrape_recipes.py --limit 10       # scrape first 10 only
    python3 scrape_recipes.py --resume         # resume from last checkpoint
"""

import json
import os
import re
import sys
import time
import random
import argparse
from pathlib import Path
from urllib.parse import unquote

import requests

BASE_DIR = Path(__file__).parent
URLS_FILE = BASE_DIR / "urls" / "recipes.txt"
DATA_DIR = BASE_DIR / "data" / "recipes"
PROGRESS_FILE = BASE_DIR / "data" / "recipes" / "_progress.json"

COOKIES = (
    "__mobsat=meBGTDsS9vN%2BEm8j1lQRCY0TKM%2BMYO5%2FIcndWJXiIDXKMfer86xy3juNo0WkvqRpmbh697WuJ7TMdZpHRs"
    "%2F%2BbKlRavkFzqHLhTwqvmJxj1Uf1iytCMsqs8F8gy6z2Ht2dhnAMdpBPxFNW0wCwt4Vk%2Be%2BDUoVljpmwJgc%2F7mCYwf1"
    "Kjt3ePlhTl0HSjLBQl%2Bmg9kezY1UvOPyNOTavWmusCWgCtXX9gF0DM47WJrKbnZc0aJoI9SrQsh1F2Q1dFo1P9Qz%2BHd5noss"
    "YZP1eUGl7rcIuWdn5Cz5J5Ts4nJcUmHQCdb0kAWpa7LyOvkexExHFTMXjz3CLjjjV2fmICd0QJI4liYIrKH41IDNJGR8AXZnYew"
    "QBeQ6%2Blm2b%2FqMjPr3WePSS0AyyP%2FX7GN1ottG8BddHBSRLqCF487tW3CpI9YD1gy9qhCI1dxVj2qrQQxeyG5P3k0y7DCnG"
    "9EwuEtP9D2h61jtNnxTMrDjwfNHVHMMYiibsr7IkL3mfdf855mrFr%2FWEv2hqjVs15AS7cul3uaKRtoJBmpMzMErTtfHatUOrV7I"
    "DsXOwSb%2FIvBuT1FEOvwhtayOWk2SFosD0eZJdG%2F5G%2BAr7MDiZCjmSF5cooLsKCyibSAmDI6PWjXJRyPbwYuiNQlWSLAhBV"
    "2EIyWiJqM3CGHB6pphJyNlr7YwPfnNVnBsVbsnQ34M2y66kCykXv6ZkflqVHe6qU1qh%2FzNlw7y3VwpgmoW%2BaM1nP0DySJdvX"
    "RhyGcgbWCEX3%2F50sokImSzFlxj%2BGJNgSTp1H4ejZG6WNdP0fMZgNWtdT7iZxfEnnibOAwSmyDKJP6yVa8OGLjqIeKZZNcLL6"
    "YnpyeRH9ZWCjLFYRYOcNoTvL83aSFCr7X8CzK0fUmAjA1Djil3JbmZ3SQ8OPhrhXdtjUbr1F9HTcHtf7d47Mq8t9qAKiM46j1to"
    "25gQT1Y9HRMPBdYM8rNoNFDicQQBXG28IKx8yfgjRsz7RqJcBjPdwvRunG4yT4j2Ovf4wJZ93cXuiJwBQS7xgfJLfTSspn0ttmH"
    "diAyGZvEX2p6ZXFG6Hdt%2FYFyGfQcSw7knb0tRU3jiswyPCa5LTc1QSSGJKpwcZ2o9D%2FH%2B3NgZCKSkAmlZsuBZaz%2FHFbli"
    "l5mCDoUD0St5CDt8H8bgLgqNb4EV1DgeX1cBQpflQPke%2FJ1sqI8tkeoVqfCG1sZZizqV6wj1eeFwgDuzzvTpTX2PAd2aYQzN7i"
    "apoMEsBAjKfDqpYsHCLIg%2FcMg2dM9ee39w2tdrn0VGav%2Fh019wrIQ8dw5FelT0evlH7OUyvIgV4Gzt2ReuQuGN7FReqQsT0g"
    "%2B2ANU3YCTMEXjz26hpzLZcM9qSaQtlbuUtprmMpOZ; "
    "__mobsatexp=1772926780; "
    "__mobrt=lin2c4jv76mf; "
    "cf_clearance=XMyTG5b7_mpVyxL.Amid_78vZKcgor6LHYQfQxLtcoA-1770334073-1.2.1.1-OV7LMjn4TbAU57CUHYMW6PrOy7qu"
    "vNV8lhexE_sFrqKkjs5XQCuS2r3Psx3yhUuPG3.4Mk2W.v5ztpj.QlPFS812s0bBj7LhznIb_gOSlvAwzkl3113mVJFnePkNTAIAN"
    "3k79SGK16lpcbIlLhEH_.FOcnLLMlceJUxED7Y.CmrFL5joHCHPKcqJeLLKDDLR7iSkh22e4y0A2HwG.pJn5DwSvpPQzsQwOO6qB3"
    "5n0Ac; "
    "guestId=07a6f988-1a5c-4122-b84b-2c666871be75"
)

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


def slugify(text: str) -> str:
    """Convert text to filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


def extract_recipe(html: str) -> dict | None:
    """Extract recipe data from __NEXT_DATA__ in HTML."""
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html)
    if not match:
        return None

    try:
        next_data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

    recipe = next_data.get("props", {}).get("pageProps", {}).get("recipe")
    if not recipe:
        return None

    # Extract ingredients
    ingredients = []
    for item in recipe.get("recipeIngredients", []):
        for ing in item.get("ingredient", []):
            ingredients.append({
                "label": item.get("label", ""),
                "quantity": item.get("quantity"),
                "unit": ing.get("unit", [{}])[0].get("shorthand") if ing.get("unit") else None,
                "name": ing.get("title", ""),
                "optional": item.get("optional", False),
                "to_taste": item.get("toTaste", False),
                "to_serve": item.get("toServe", False),
            })

    # Extract method steps
    steps = []
    for item in recipe.get("method", []):
        if item.get("typeHandle") == "step":
            # Strip HTML tags from description
            desc = item.get("description", "") or ""
            desc = re.sub(r'<[^>]+>', '', desc).strip()
            if desc:
                steps.append({
                    "heading": item.get("heading"),
                    "description": desc,
                })

    # Extract tips from notes (HTML)
    tips_html = recipe.get("notes", "") or ""
    tips = []
    for li in re.findall(r'<li[^>]*>(.*?)</li>', tips_html, re.DOTALL):
        tip_text = re.sub(r'<[^>]+>', '', li).strip()
        if tip_text:
            tips.append(tip_text)

    # Extract categories
    categories = [cat.get("title", "") for cat in recipe.get("recipeCategories", [])]
    dietary = [d.get("title", "") for d in recipe.get("dietaryRequirements", [])]
    meals = [m.get("title", "") for m in recipe.get("meals", [])]
    cuisines = [c.get("title", "") for c in recipe.get("cuisines", [])]

    # Extract chef info
    chefs = []
    for chef in recipe.get("chefs", []):
        chefs.append({
            "name": chef.get("title", ""),
            "uri": chef.get("uri", ""),
            "summary": chef.get("summary", ""),
        })

    # Build structured output
    return {
        "id": recipe.get("id"),
        "slug": recipe.get("slug"),
        "title": recipe.get("title"),
        "url": f"https://www.mob.co.uk/{recipe.get('uri', '')}",
        "summary": recipe.get("summary", ""),
        "image": recipe["image"][0]["url"] if recipe.get("image") else None,
        "cook_time": recipe.get("cookTime"),
        "prep_time": recipe.get("prepTime"),
        "total_time": recipe.get("time"),
        "serving_size": recipe.get("servingSize"),
        "nutrition": {
            "calories": recipe.get("calories"),
            "protein": recipe.get("protein"),
            "fat": recipe.get("fat"),
            "saturated_fat": recipe.get("saturatedFat"),
            "carbohydrates": recipe.get("carbohydrates"),
            "dietary_fibre": recipe.get("dietaryFibre"),
            "sugars": recipe.get("sugars"),
            "sodium": recipe.get("sodium"),
        },
        "ingredients": ingredients,
        "method": steps,
        "tips": tips,
        "categories": categories,
        "dietary_requirements": dietary,
        "meals": meals,
        "cuisines": cuisines,
        "chefs": chefs,
        "access_level": recipe.get("accessLevel"),
        "post_date": recipe.get("postDate"),
    }


def save_recipe(recipe: dict, data_dir: Path):
    """Save recipe JSON organized by primary category."""
    # Determine category folder
    if recipe["categories"]:
        category = slugify(recipe["categories"][0])
    elif recipe["meals"]:
        category = slugify(recipe["meals"][0])
    else:
        category = "uncategorized"

    cat_dir = data_dir / category
    cat_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{recipe['slug']}.json"
    filepath = cat_dir / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(recipe, f, indent=2, ensure_ascii=False)

    return filepath


def load_progress() -> set:
    """Load set of already-scraped URLs."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return set(json.load(f))
    return set()


def save_progress(scraped: set):
    """Save progress checkpoint."""
    with open(PROGRESS_FILE, "w") as f:
        json.dump(sorted(scraped), f)


def scrape_recipes(limit: int = 0, resume: bool = False):
    """Main scraping loop."""
    # Load URLs
    with open(URLS_FILE) as f:
        urls = [line.strip() for line in f if line.strip()]

    if limit > 0:
        urls = urls[:limit]

    # Load progress
    scraped = load_progress() if resume else set()
    if resume and scraped:
        print(f"Resuming: {len(scraped)} already scraped, {len(urls) - len(scraped)} remaining")

    # Setup session
    session = requests.Session()
    session.headers.update({
        "Cookie": COOKIES,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    })

    success = 0
    failed = 0
    blocked = 0

    for i, url in enumerate(urls):
        if url in scraped:
            continue

        # Rotate user agent
        session.headers["User-Agent"] = random.choice(USER_AGENTS)

        try:
            resp = session.get(url, timeout=15)

            if resp.status_code == 403:
                blocked += 1
                print(f"  [{i+1}/{len(urls)}] BLOCKED (403) - {url}")
                if blocked >= 3:
                    print("  3 consecutive blocks - stopping. Try again later or refresh cookies.")
                    break
                time.sleep(30)  # long backoff on block
                continue
            elif resp.status_code == 429:
                print(f"  [{i+1}/{len(urls)}] RATE LIMITED (429) - backing off 60s")
                time.sleep(60)
                continue
            elif resp.status_code != 200:
                failed += 1
                print(f"  [{i+1}/{len(urls)}] HTTP {resp.status_code} - {url}")
                continue

            blocked = 0  # reset consecutive block counter on success

            recipe = extract_recipe(resp.text)
            if not recipe:
                failed += 1
                print(f"  [{i+1}/{len(urls)}] NO DATA - {url}")
                continue

            filepath = save_recipe(recipe, DATA_DIR)
            scraped.add(url)
            success += 1

            print(f"  [{i+1}/{len(urls)}] OK - {recipe['title']} -> {filepath.relative_to(BASE_DIR)}")

            # Save progress every 50 recipes
            if success % 50 == 0:
                save_progress(scraped)

        except requests.RequestException as e:
            failed += 1
            print(f"  [{i+1}/{len(urls)}] ERROR - {url}: {e}")

        # Polite delay
        time.sleep(random.uniform(0.05, 0.1))

    # Final save
    save_progress(scraped)

    print(f"\nDone! Success: {success}, Failed: {failed}, Total scraped: {len(scraped)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape mob.co.uk recipes")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of recipes to scrape (0 = all)")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
    args = parser.parse_args()

    scrape_recipes(limit=args.limit, resume=args.resume)
