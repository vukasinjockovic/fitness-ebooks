#!/usr/bin/env python3
"""
Parser for The Bodybuilder's Kitchen by Erin Stern
Extracts recipes, meal plans, and nutrition concepts from epub.
"""

import re
import os
from pathlib import Path
from html import unescape

EPUB_XHTML_DIR = "/tmp/claude-1001/-home-vuk/0cf3e2a3-adcb-4863-bf74-d8d61978a0fe/scratchpad/bodybuilders-kitchen/OEBPS/xhtml"
OUTPUT_DIR = "/home/vuk/fitness-books/knowledge/erin-stern/bodybuilders-kitchen"

def slugify(text):
    """Convert text to slug for filenames."""
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    return text.strip('-')

def clean_text(text):
    """Clean HTML entities and extra whitespace."""
    text = unescape(text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def parse_fractions(text):
    """Convert HTML fraction spans to Unicode fractions."""
    # Pattern: <span class="sup">1</span>⁄<span class="sub">2</span> -> ½
    fractions = {
        ('1', '2'): '½',
        ('1', '3'): '⅓',
        ('2', '3'): '⅔',
        ('1', '4'): '¼',
        ('3', '4'): '¾',
        ('1', '8'): '⅛',
    }
    pattern = r'<span class="sup">(\d+)</span>⁄<span class="sub">(\d+)</span>'

    def replace_fraction(match):
        num, denom = match.groups()
        return fractions.get((num, denom), f'{num}/{denom}')

    return re.sub(pattern, replace_fraction, text)

def extract_recipe(filepath):
    """Extract a single recipe from xhtml file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Check if this is a recipe file (recipe_head or recipe_head1)
    if 'recipe_head' not in content:
        return None

    content = parse_fractions(content)

    recipe = {}

    # Extract title (handles both recipe_head and recipe_head1)
    title_match = re.search(r'<h2 class="recipe_head1?"[^>]*>(?:<a[^>]*/>)?([^<]+)</h2>', content)
    if title_match:
        recipe['title'] = clean_text(title_match.group(1))
    else:
        return None

    # Extract intro
    intro_match = re.search(r'<p class="recipe_intro"[^>]*>([^<]+)</p>', content)
    if intro_match:
        recipe['intro'] = clean_text(intro_match.group(1))

    # Extract yield info
    yield_match = re.search(r'<strong>Makes</strong>\s*(\d+)\s*servings?', content)
    if yield_match:
        recipe['servings'] = int(yield_match.group(1))

    serving_size_match = re.search(r'<strong>Serving size</strong>\s*([^<]+)', content)
    if serving_size_match:
        recipe['serving_size'] = clean_text(serving_size_match.group(1))

    # Extract prep/cook time
    prep_match = re.search(r'<strong>Prep time</strong>\s*([^<]+)', content)
    if prep_match:
        recipe['prep_time'] = clean_text(prep_match.group(1))

    cook_match = re.search(r'<strong>Cook time</strong>\s*([^<]+)', content)
    if cook_match:
        recipe['cook_time'] = clean_text(cook_match.group(1))

    # Extract ingredients
    ingredients = re.findall(r'<p class="ingredients"[^>]*>([^<]+(?:<[^>]+>[^<]*</[^>]+>)*[^<]*)</p>', content)
    recipe['ingredients'] = [clean_text(re.sub(r'<[^>]+>', '', ing)) for ing in ingredients]

    # Extract directions
    directions = re.findall(r'<p class="recipe_steps(?:_last)?"[^>]*><strong>\d+</strong>\s*([^<]+(?:<[^>]+>[^<]*</[^>]+>)*[^<]*)</p>', content)
    recipe['directions'] = [clean_text(re.sub(r'<[^>]+>', '', d)) for d in directions]

    # Extract prep tips
    prep_tips = re.findall(r'<p class="PrepTipText"[^>]*>([^<]+)</p>', content)
    if prep_tips:
        recipe['prep_tips'] = [clean_text(tip) for tip in prep_tips]

    # Extract change it up
    change_match = re.search(r'<p class="Sidebar"[^>]*>([^<]+(?:<[^>]+>[^<]*</[^>]+>)*[^<]*)</p>', content)
    if change_match:
        recipe['variations'] = clean_text(re.sub(r'<[^>]+>', '', change_match.group(1)))

    return recipe

def recipe_to_markdown(recipe, category):
    """Convert recipe dict to markdown."""
    md = f"# {recipe['title']}\n\n"
    md += f"**Category:** {category}\n"
    md += f"**Source:** The Bodybuilder's Kitchen by Erin Stern (2018)\n\n"

    if 'intro' in recipe:
        md += f"> {recipe['intro']}\n\n"

    md += "## Quick Info\n\n"
    if 'servings' in recipe:
        md += f"- **Servings:** {recipe['servings']}\n"
    if 'serving_size' in recipe:
        md += f"- **Serving Size:** {recipe['serving_size']}\n"
    if 'prep_time' in recipe:
        md += f"- **Prep Time:** {recipe['prep_time']}\n"
    if 'cook_time' in recipe:
        md += f"- **Cook Time:** {recipe['cook_time']}\n"
    md += "\n"

    md += "## Ingredients\n\n"
    for ing in recipe.get('ingredients', []):
        md += f"- {ing}\n"
    md += "\n"

    md += "## Directions\n\n"
    for i, step in enumerate(recipe.get('directions', []), 1):
        md += f"{i}. {step}\n\n"

    if 'prep_tips' in recipe:
        md += "## Prep Tips\n\n"
        for tip in recipe['prep_tips']:
            md += f"- {tip}\n"
        md += "\n"

    if 'variations' in recipe:
        md += "## Variations\n\n"
        md += f"{recipe['variations']}\n"

    return md

def extract_meal_plan(filepath):
    """Extract a meal plan from xhtml file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    content = parse_fractions(content)

    plan = {}

    # Extract title
    title_match = re.search(r'<h2 class="a_head"[^>]*>([^<]+)</h2>', content)
    if title_match:
        plan['title'] = clean_text(title_match.group(1))
    else:
        return None

    # Extract macro formulas
    formulas = re.findall(r'<p class="formula_items\d?"[^>]*><strong>([^<]+)</strong></p>', content)
    plan['macro_formula'] = [clean_text(f) for f in formulas]

    # Extract shopping list
    shopping = re.findall(r'<p class="shoppinglist"[^>]*>([^<]+(?:<[^>]+>[^<]*</[^>]+>)*[^<]*)</p>', content)
    plan['shopping_list'] = []
    for item in shopping:
        item = re.sub(r'<[^>]+>', '', item)
        item = clean_text(item)
        # Parse category
        if '•' in item:
            item = item.replace('•', '').strip()
        if ':' in item:
            category, items = item.split(':', 1)
            category = category.replace('STARCHES', 'Starches').replace('PROTEINS', 'Proteins')
            category = category.replace('FRUITS AND VEGETABLES', 'Fruits & Vegetables').replace('OTHER', 'Other')
            plan['shopping_list'].append({'category': clean_text(category), 'items': clean_text(items)})

    return plan

def meal_plan_to_markdown(plan):
    """Convert meal plan dict to markdown."""
    md = f"# {plan['title']}\n\n"
    md += "**Source:** The Bodybuilder's Kitchen by Erin Stern (2018)\n\n"

    md += "## Macro Formula\n\n"
    for formula in plan.get('macro_formula', []):
        md += f"- **{formula}**\n"
    md += "\n"

    md += "## Shopping List\n\n"
    for item in plan.get('shopping_list', []):
        md += f"### {item['category']}\n\n"
        md += f"{item['items']}\n\n"

    return md

def get_category_from_filename(filename):
    """Determine recipe category from filename page number."""
    # Extract page number
    match = re.search(r'p(\d+)', filename)
    if not match:
        return "Uncategorized"

    page = int(match.group(1))

    if 34 <= page <= 57:
        return "Breakfasts"
    elif 60 <= page <= 97:
        return "Entrées"
    elif 100 <= page <= 125:
        return "Salads & Sides"
    elif 128 <= page <= 143:
        return "Snacks & Power Bars"
    elif 146 <= page <= 155:
        return "Shakes & Desserts"
    else:
        return "Uncategorized"

def main():
    # Create output directories
    recipes_dir = Path(OUTPUT_DIR) / "recipes"
    meal_plans_dir = Path(OUTPUT_DIR) / "meal-plans"
    recipes_dir.mkdir(parents=True, exist_ok=True)
    meal_plans_dir.mkdir(parents=True, exist_ok=True)

    # Track recipes by category
    recipes_by_category = {}

    # Process all xhtml files
    xhtml_dir = Path(EPUB_XHTML_DIR)
    recipe_count = 0

    for filepath in sorted(xhtml_dir.glob("*.xhtml")):
        filename = filepath.name

        # Check for meal plans
        if "MealPlan" in filename:
            plan = extract_meal_plan(filepath)
            if plan:
                slug = slugify(plan['title'])
                output_path = meal_plans_dir / f"{slug}.md"
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(meal_plan_to_markdown(plan))
                print(f"Extracted meal plan: {plan['title']}")
        else:
            # Try to extract recipe
            recipe = extract_recipe(filepath)
            if recipe:
                category = get_category_from_filename(filename)
                slug = slugify(recipe['title'])
                output_path = recipes_dir / f"{slug}.md"
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(recipe_to_markdown(recipe, category))
                recipe_count += 1

                if category not in recipes_by_category:
                    recipes_by_category[category] = []
                recipes_by_category[category].append(recipe['title'])
                print(f"Extracted recipe: {recipe['title']} ({category})")

    # Create index files
    with open(recipes_dir / "_index.md", 'w', encoding='utf-8') as f:
        f.write("# Recipes Index\n\n")
        f.write("**Source:** The Bodybuilder's Kitchen by Erin Stern (2018)\n\n")
        f.write(f"**Total Recipes:** {recipe_count}\n\n")

        for category in ["Breakfasts", "Entrées", "Salads & Sides", "Snacks & Power Bars", "Shakes & Desserts"]:
            if category in recipes_by_category:
                f.write(f"## {category}\n\n")
                for title in sorted(recipes_by_category[category]):
                    slug = slugify(title)
                    f.write(f"- [{title}]({slug}.md)\n")
                f.write("\n")

    with open(meal_plans_dir / "_index.md", 'w', encoding='utf-8') as f:
        f.write("# Meal Plans Index\n\n")
        f.write("**Source:** The Bodybuilder's Kitchen by Erin Stern (2018)\n\n")
        f.write("This book includes 5 comprehensive 7-day meal plans:\n\n")
        for plan_file in sorted(meal_plans_dir.glob("*.md")):
            if plan_file.name != "_index.md":
                name = plan_file.stem.replace('-', ' ').title()
                f.write(f"- [{name}]({plan_file.name})\n")

    print(f"\n=== Extraction Complete ===")
    print(f"Recipes: {recipe_count}")
    print(f"Meal Plans: 5")
    print(f"Output: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
