#!/usr/bin/env python3
"""
Parser for The Bodybuilding Meal Prep Cookbook by Michelle Vodrazka
Extracts recipes, meal prep plans, and nutrition concepts from epub.
"""

import re
import os
from pathlib import Path
from html import unescape

EPUB_TEXT_DIR = "/tmp/claude-1001/-home-vuk/0cf3e2a3-adcb-4863-bf74-d8d61978a0fe/scratchpad/meal-prep-check/text"
OUTPUT_DIR = "/home/vuk/fitness-books/knowledge/michelle-vodrazka/bodybuilding-meal-prep-cookbook"

def slugify(text):
    """Convert text to slug for filenames."""
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    return text.strip('-')

def clean_text(text):
    """Clean HTML entities and extra whitespace."""
    text = unescape(text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def extract_recipe(filepath):
    """Extract a recipe from html file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    recipe = {}

    # Look for recipe title patterns
    title_match = re.search(r'<p class="c71"><strong[^>]*>(?:<a[^>]*>)?([^<]+)', content)
    if not title_match:
        # Try alternate pattern
        title_match = re.search(r'<strong class="calibre2">([A-Z][A-Z\s\-&]+(?:WITH|AND)?[A-Z\s\-&]*)</strong>', content)

    if not title_match:
        return None

    recipe['title'] = clean_text(title_match.group(1)).strip()

    # Skip if title looks like a section header
    if recipe['title'] in ['TO MAKE THE', 'FOR THE', 'SHOPPING LIST', 'EQUIPMENT LIST']:
        return None

    # Extract macro percentages
    fat_match = re.search(r'<strong class="calibre4">(\d+)%</strong>\s*</span>\s*<strong class="calibre4">Fat</strong>', content)
    protein_match = re.search(r'<strong class="calibre4">(\d+)%</strong>\s*</span>\s*<strong class="calibre4">Protein</strong>', content)
    carbs_match = re.search(r'<strong class="calibre4">(\d+)%</strong>\s*</span>\s*<strong class="calibre4">Carbs</strong>', content)

    if fat_match:
        recipe['fat_pct'] = fat_match.group(1)
    if protein_match:
        recipe['protein_pct'] = protein_match.group(1)
    if carbs_match:
        recipe['carbs_pct'] = carbs_match.group(1)

    # Extract servings and times
    servings_match = re.search(r'MAKES\s*(\d+)\s*SERVINGS?', content, re.IGNORECASE)
    if servings_match:
        recipe['servings'] = servings_match.group(1)

    prep_match = re.search(r'PREP TIME:?\s*</strong>\s*<strong[^>]*>([^<]+)', content)
    if prep_match:
        recipe['prep_time'] = clean_text(prep_match.group(1))

    cook_match = re.search(r'COOK TIME:?\s*</strong>\s*<strong[^>]*>([^<]+)', content)
    if cook_match:
        recipe['cook_time'] = clean_text(cook_match.group(1))

    # Extract description
    desc_match = re.search(r'<p class="c81"><strong class="calibre2">([^<]+)</strong></p>', content)
    if desc_match:
        recipe['description'] = clean_text(desc_match.group(1))

    # Extract dietary tags
    tags = []
    if 'DAIRY-FREE' in content or 'DAIRY FREE' in content:
        tags.append('Dairy-Free')
    if 'GLUTEN-FREE' in content or 'GLUTEN FREE' in content:
        tags.append('Gluten-Free')
    if 'NUT-FREE' in content or 'NUT FREE' in content:
        tags.append('Nut-Free')
    if 'VEGAN' in content:
        tags.append('Vegan')
    if 'VEGETARIAN' in content:
        tags.append('Vegetarian')
    recipe['tags'] = tags

    # Extract ingredients (lines with class c83)
    ingredients = re.findall(r'<p class="c83">([^<]+)</p>', content)
    recipe['ingredients'] = [clean_text(i) for i in ingredients if clean_text(i)]

    # Extract directions (numbered steps)
    directions = re.findall(r'<strong class="calibre2">(\d+)\.</strong></span>\s*([^<]+(?:<[^>]+>[^<]*</[^>]+>)*[^<]*)</p>', content)
    recipe['directions'] = [clean_text(d[1]) for d in directions]

    # Extract nutrition info
    nutrition_match = re.search(r'Per serving[^:]*:\s*</span>\s*([^<]+)', content)
    if nutrition_match:
        recipe['nutrition'] = clean_text(nutrition_match.group(1))

    # Extract tips
    tip_match = re.search(r'(?:SUBSTITUTION TIP|TIP|PREP TIP):\s*</span>\s*([^<]+)', content)
    if tip_match:
        recipe['tip'] = clean_text(tip_match.group(1))

    # Extract storage info
    storage_match = re.search(r'(Refrigerate|Freeze|Store)[^<]*</span>\s*([^<]+)', content)
    if storage_match:
        recipe['storage'] = clean_text(storage_match.group(0))

    return recipe

def extract_prep_plan(filepath, prep_num):
    """Extract a weekly prep plan."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    plan = {'prep_num': prep_num}

    # Extract intro text
    intro_match = re.search(r'<div class="c57">\s*<p class="c20">([^<]+)</p>', content)
    if intro_match:
        plan['intro'] = clean_text(intro_match.group(1))

    # Extract shopping list items
    shopping = {}
    current_category = None

    for match in re.finditer(r'<p class="c59"><strong class="calibre4">([^<]+)</strong></p>|<p class="c60"><span class="c61">•</span>([^<]+)</p>', content):
        if match.group(1):
            current_category = clean_text(match.group(1))
            shopping[current_category] = []
        elif match.group(2) and current_category:
            shopping[current_category].append(clean_text(match.group(2)))

    plan['shopping'] = shopping

    # Extract meal schedule from table
    meals = []
    rows = re.findall(r'<tr class="calibre10">(.*?)</tr>', content, re.DOTALL)
    for row in rows:
        cells = re.findall(r'<td[^>]*>([^<]*(?:<[^>]+>[^<]*)*)</td>', row)
        if cells:
            cleaned = [clean_text(c) for c in cells]
            if cleaned and any(cleaned):
                meals.append(cleaned)
    plan['schedule'] = meals

    # Extract equipment
    equipment = []
    equip_section = re.search(r'EQUIPMENT LIST.*?(?=STEP-BY-STEP|$)', content, re.DOTALL)
    if equip_section:
        equipment = re.findall(r'<span class="c61">•</span>([^<]+)', equip_section.group(0))
        equipment = [clean_text(e) for e in equipment]
    plan['equipment'] = equipment

    # Extract prep steps
    prep_steps = re.findall(r'<span class="c68"><strong class="calibre2">(\d+)\.</strong></span>\s*([^<]+(?:<a[^>]*>[^<]*</a>)?[^<]*)', content)
    plan['prep_steps'] = [(s[0], clean_text(s[1])) for s in prep_steps]

    return plan

def recipe_to_markdown(recipe, category):
    """Convert recipe dict to markdown."""
    md = f"# {recipe['title']}\n\n"
    md += f"**Category:** {category}\n"
    md += "**Source:** The Bodybuilding Meal Prep Cookbook by Michelle Vodrazka (2019)\n"

    if recipe.get('tags'):
        md += f"**Tags:** {', '.join(recipe['tags'])}\n"
    md += "\n"

    if recipe.get('description'):
        md += f"> {recipe['description']}\n\n"

    md += "## Quick Info\n\n"
    if recipe.get('servings'):
        md += f"- **Servings:** {recipe['servings']}\n"
    if recipe.get('prep_time'):
        md += f"- **Prep Time:** {recipe['prep_time']}\n"
    if recipe.get('cook_time'):
        md += f"- **Cook Time:** {recipe['cook_time']}\n"

    if recipe.get('fat_pct') or recipe.get('protein_pct') or recipe.get('carbs_pct'):
        md += f"- **Macros:** {recipe.get('protein_pct', '?')}% Protein / {recipe.get('carbs_pct', '?')}% Carbs / {recipe.get('fat_pct', '?')}% Fat\n"
    md += "\n"

    if recipe.get('ingredients'):
        md += "## Ingredients\n\n"
        for ing in recipe['ingredients']:
            md += f"- {ing}\n"
        md += "\n"

    if recipe.get('directions'):
        md += "## Directions\n\n"
        for i, step in enumerate(recipe['directions'], 1):
            md += f"{i}. {step}\n\n"

    if recipe.get('nutrition'):
        md += "## Nutrition (per serving)\n\n"
        md += f"{recipe['nutrition']}\n\n"

    if recipe.get('storage'):
        md += "## Storage\n\n"
        md += f"{recipe['storage']}\n\n"

    if recipe.get('tip'):
        md += "## Tips\n\n"
        md += f"{recipe['tip']}\n"

    return md

def prep_plan_to_markdown(plan):
    """Convert prep plan to markdown."""
    md = f"# Prep {plan['prep_num']}: Week {plan['prep_num']} Meal Prep\n\n"
    md += "**Source:** The Bodybuilding Meal Prep Cookbook by Michelle Vodrazka (2019)\n\n"

    if plan.get('intro'):
        md += f"> {plan['intro']}\n\n"

    if plan.get('schedule'):
        md += "## 5-Day Meal Schedule\n\n"
        md += "| | Day 1 | Day 2 | Day 3 | Day 4 | Day 5 |\n"
        md += "|---|---|---|---|---|---|\n"
        for row in plan['schedule']:
            if len(row) >= 6 and row[0]:
                md += f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]} | {row[5]} |\n"
        md += "\n"

    if plan.get('shopping'):
        md += "## Shopping List\n\n"
        for category, items in plan['shopping'].items():
            md += f"### {category}\n\n"
            for item in items:
                md += f"- {item}\n"
            md += "\n"

    if plan.get('equipment'):
        md += "## Equipment\n\n"
        for item in plan['equipment']:
            md += f"- {item}\n"
        md += "\n"

    if plan.get('prep_steps'):
        md += "## Step-by-Step Prep\n\n"
        for num, step in plan['prep_steps']:
            md += f"{num}. {step}\n\n"

    return md

# Recipe file ranges by category (based on TOC)
RECIPE_RANGES = {
    'Prep Week Recipes': list(range(21, 58)),
    'Staples and Sauces': list(range(62, 74)),
    'Breakfast': list(range(76, 93)),
    'Lunch and Dinner': list(range(95, 113)),
    'Vegetables and Grains': list(range(115, 125)),
    'Sweet and Savory Snacks': list(range(127, 139)),
}

def get_category(part_num):
    """Determine category from part number."""
    for category, ranges in RECIPE_RANGES.items():
        if part_num in ranges:
            return category
    return "Uncategorized"

def main():
    # Create output directories
    recipes_dir = Path(OUTPUT_DIR) / "recipes"
    prep_plans_dir = Path(OUTPUT_DIR) / "meal-prep-plans"
    concepts_dir = Path(OUTPUT_DIR) / "concepts"

    recipes_dir.mkdir(parents=True, exist_ok=True)
    prep_plans_dir.mkdir(parents=True, exist_ok=True)
    concepts_dir.mkdir(parents=True, exist_ok=True)

    recipes_by_category = {}
    recipe_count = 0

    text_dir = Path(EPUB_TEXT_DIR)

    # Extract prep plans (parts 20, 24, 29, 36, 43, 50)
    prep_parts = [20, 24, 29, 36, 43, 50]
    for i, part in enumerate(prep_parts, 1):
        filepath = text_dir / f"part{part:04d}.html"
        if filepath.exists():
            plan = extract_prep_plan(filepath, i)
            slug = f"prep-{i:02d}-week-{i}"
            output_path = prep_plans_dir / f"{slug}.md"
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(prep_plan_to_markdown(plan))
            print(f"Extracted prep plan: Prep {i}")

    # Extract recipes
    for filepath in sorted(text_dir.glob("part*.html")):
        filename = filepath.name
        part_match = re.search(r'part(\d+)', filename)
        if not part_match:
            continue

        part_num = int(part_match.group(1))
        category = get_category(part_num)

        if category == "Uncategorized":
            continue

        recipe = extract_recipe(filepath)
        if recipe and recipe.get('title') and len(recipe.get('ingredients', [])) > 0:
            slug = slugify(recipe['title'])
            if not slug:
                continue

            output_path = recipes_dir / f"{slug}.md"
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(recipe_to_markdown(recipe, category))

            recipe_count += 1
            if category not in recipes_by_category:
                recipes_by_category[category] = []
            recipes_by_category[category].append(recipe['title'])
            print(f"Extracted recipe: {recipe['title']} ({category})")

    # Create recipe index
    with open(recipes_dir / "_index.md", 'w', encoding='utf-8') as f:
        f.write("# Recipes Index\n\n")
        f.write("**Source:** The Bodybuilding Meal Prep Cookbook by Michelle Vodrazka (2019)\n\n")
        f.write(f"**Total Recipes:** {recipe_count}\n\n")

        for category in ['Prep Week Recipes', 'Breakfast', 'Lunch and Dinner',
                        'Vegetables and Grains', 'Sweet and Savory Snacks', 'Staples and Sauces']:
            if category in recipes_by_category:
                f.write(f"## {category}\n\n")
                for title in sorted(recipes_by_category[category]):
                    slug = slugify(title)
                    f.write(f"- [{title}]({slug}.md)\n")
                f.write("\n")

    # Create prep plans index
    with open(prep_plans_dir / "_index.md", 'w', encoding='utf-8') as f:
        f.write("# Meal Prep Plans Index\n\n")
        f.write("**Source:** The Bodybuilding Meal Prep Cookbook by Michelle Vodrazka (2019)\n\n")
        f.write("This book features a 6-week progressive meal prep program:\n\n")
        f.write("| Week | Focus | Meals/Day |\n")
        f.write("|------|-------|----------|\n")
        f.write("| [Prep 1](prep-01-week-1.md) | Getting Started | 2 |\n")
        f.write("| [Prep 2](prep-02-week-2.md) | Building Habits | 3 |\n")
        f.write("| [Prep 3](prep-03-week-3.md) | Expanding | 4 |\n")
        f.write("| [Prep 4](prep-04-week-4.md) | Full Program | 4 |\n")
        f.write("| [Prep 5](prep-05-week-5.md) | Variety | 4 |\n")
        f.write("| [Prep 6](prep-06-week-6.md) | Mastery | 4+ |\n")

    print(f"\n=== Extraction Complete ===")
    print(f"Recipes: {recipe_count}")
    print(f"Prep Plans: 6")
    print(f"Output: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
