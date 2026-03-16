#!/usr/bin/env python3
"""Bug 2 fix: Audit and repair mistagged diet tags in lake.recipes.

Problem: 32,571+ recipes are tagged both "Vegan" AND "Keto" but contain
meat/fish/dairy. Root cause: when primary_protein is NULL, all diet tags
were assigned as defaults by the upstream normalizer.

Strategy (conservative):
1. If title or ingredients contain meat/fish/poultry keywords AND recipe
   is tagged Vegan/Vegetarian, remove those tags.
2. If title or ingredients contain dairy keywords AND recipe is tagged Vegan,
   remove the Vegan tag (keep Vegetarian).
3. Only remove tags when there is clear evidence of mistagging.
4. Run in dry-run mode by default; use --apply to actually update.

Usage:
    python3 fix_diet_tags.py --dry-run    # Preview changes (default)
    python3 fix_diet_tags.py --apply      # Apply fixes to database
"""

from __future__ import annotations

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import get_connection

# Keywords that indicate meat/fish/poultry (NOT vegan or vegetarian)
# NOTE: Be conservative -- only include unambiguous meat terms.
# Avoid: "oyster" (oyster mushroom/sauce), "buffalo" (buffalo mozzarella),
#         "ribs" (celery ribs), "sushi" (sushi rice), "cod" (Cape Cod),
#         "bass" (ambiguous), "duck" (duck sauce/fat in vegetarian context)
MEAT_KEYWORDS = [
    # Poultry (unambiguous)
    "chicken", "turkey breast", "turkey thigh", "turkey leg", "ground turkey",
    "roast turkey", "turkey mince",
    "roast goose", "goose breast", "goose leg", "goose fat",
    "cornish hen", "cornish game hen",
    "poultry", "quail", "pheasant",
    # Red meat (unambiguous)
    "beef", "steak", "lamb chop", "lamb shank", "lamb shoulder", "leg of lamb",
    "rack of lamb", "ground lamb", "lamb mince",
    "pork chop", "pork loin", "pork belly", "pork shoulder", "pulled pork",
    "ground pork", "pork tenderloin", "pork mince",
    "veal", "venison", "bison",
    "bacon", "prosciutto", "pancetta", "salami",
    "chorizo", "pepperoni", "meatball", "meatloaf", "ground meat",
    "bratwurst",
    # Fish / seafood (unambiguous)
    "salmon", "tuna", "halibut", "tilapia", "trout", "mackerel",
    "sardine", "anchovy", "anchovies", "swordfish", "mahi mahi", "branzino",
    "snapper", "catfish", "haddock",
    "shrimp", "prawn", "lobster", "crab cake", "crab meat", "crab leg",
    "scallop", "clam", "mussel",
    "calamari", "squid", "octopus", "crawfish", "crayfish",
    "camaron", "fish fillet", "fish cake", "fish stick", "fish taco",
    "ceviche", "sashimi",
]

# Keywords that indicate dairy (NOT vegan, but OK for vegetarian)
# NOTE: "butter" alone is too ambiguous (peanut butter, almond butter, etc.)
# Use "unsalted butter", "melted butter", etc. or rely on exceptions list.
# "cream" alone is too ambiguous (cream of tartar, ice cream base, etc.)
# "milk" alone is too ambiguous (coconut milk, oat milk, etc.)
DAIRY_KEYWORDS = [
    "feta", "cheddar", "mozzarella", "parmesan", "brie",
    "camembert", "gouda", "gruyere", "ricotta", "cream cheese",
    "mascarpone", "cottage cheese", "queso", "paneer",
    "ghee", "heavy cream", "whipping cream", "double cream",
    "sour cream", "creme fraiche", "clotted cream",
    "yogurt", "yoghurt", "kefir",
    "whole milk", "skimmed milk", "semi-skimmed milk", "buttermilk",
]

# Exceptions: terms that look like dairy/meat but are actually vegan
VEGAN_EXCEPTIONS = [
    "coconut milk", "coconut cream", "almond milk", "oat milk", "soy milk",
    "rice milk", "cashew milk", "hemp milk", "plant milk",
    "vegan cheese", "vegan butter", "vegan cream", "dairy-free",
    "plant-based", "beyond meat", "impossible", "mock meat",
    "jackfruit", "seitan", "nutritional yeast",
    "coconut yogurt", "soy yogurt", "vegan yogurt",
    "mushroom bacon", "coconut bacon", "tempeh bacon",
    "vegan sausage", "vegan hot dog",
    "fish sauce alternative", "vegan fish",
    "oyster mushroom", "oyster sauce", "king oyster",
    "buffalo mozzarella", "buffalo sauce", "buffalo cauliflower",
    "buffalo wing sauce",
    "duck sauce",
    "sushi rice", "sushi vinegar",
    "celery ribs",
    "almond butter", "peanut butter", "cashew butter", "sunflower butter",
    "apple butter", "nut butter", "cocoa butter", "shea butter",
    "butternut", "buttercup", "butterfly", "buttermilk squash",
    "cream of tartar", "ice cream",
    "coconut cream cheese", "vegan cream cheese",
    "soy cream", "cashew cream",
    "vegan parmesan", "vegan mozzarella", "vegan cheddar", "vegan feta",
    "vegan ricotta", "vegan queso",
    "nutritional yeast parmesan",
    # Seitan/plant-based descriptions that mention meats for comparison
    "like chicken", "like beef", "like pork",
    "chicken-style", "beef-style", "pork-style",
    "chicken substitute", "meat substitute",
]


def _text_contains_keyword(text: str, keywords: list[str], exceptions: list[str]) -> list[str]:
    """Check if text contains any keyword, excluding exceptions.

    Returns list of matched keywords.
    """
    text_lower = text.lower()

    # First check if any exception applies (if so, mask those regions)
    exception_ranges = []
    for exc in exceptions:
        start = 0
        while True:
            pos = text_lower.find(exc, start)
            if pos == -1:
                break
            exception_ranges.append((pos, pos + len(exc)))
            start = pos + 1

    matches = []
    for kw in keywords:
        start = 0
        while True:
            pos = text_lower.find(kw, start)
            if pos == -1:
                break
            # Check if this match is within an exception range
            in_exception = False
            for exc_start, exc_end in exception_ranges:
                if exc_start <= pos < exc_end or exc_start < pos + len(kw) <= exc_end:
                    in_exception = True
                    break
            if not in_exception:
                matches.append(kw)
                break  # One match per keyword is enough
            start = pos + 1

    return matches


def audit_mistagged_recipes(conn, dry_run: bool = True) -> dict:
    """Audit and optionally fix mistagged diet tags.

    Returns stats dict with counts of fixes applied.
    """
    stats = {
        "total_checked": 0,
        "vegan_removed": 0,
        "vegetarian_removed": 0,
        "skipped_exceptions": 0,
        "recipes_fixed": 0,
        "sample_fixes": [],
    }

    updates = []  # List of (recipe_id, tags_to_remove)

    # Pass 1: Recipes with explicit meat/fish primary_protein that are
    # tagged Vegan/Vegetarian -- these are clear mistagging.
    MEAT_PROTEINS = {"Chicken", "Beef", "Pork", "Fish/Seafood", "Turkey", "Lamb", "Game"}

    cur = conn.cursor()
    cur.execute("""
        SELECT id, title, diet_tags, primary_protein
        FROM lake.recipes
        WHERE ('Vegan' = ANY(diet_tags) OR 'Vegetarian' = ANY(diet_tags))
          AND primary_protein = ANY(%s)
        ORDER BY quality_score DESC
    """, [list(MEAT_PROTEINS)])

    protein_rows = cur.fetchall()
    cur.close()

    print(f"Pass 1: Checking {len(protein_rows)} recipes with meat primary_protein + Vegan/Vegetarian tags...")

    for rid, title, diet_tags, primary_protein in protein_rows:
        stats["total_checked"] += 1
        tags_to_remove = set()
        if "Vegan" in diet_tags:
            tags_to_remove.add("Vegan")
        if "Vegetarian" in diet_tags:
            tags_to_remove.add("Vegetarian")

        if tags_to_remove:
            updates.append((rid, tags_to_remove))
            stats["recipes_fixed"] += 1
            if "Vegan" in tags_to_remove:
                stats["vegan_removed"] += 1
            if "Vegetarian" in tags_to_remove:
                stats["vegetarian_removed"] += 1
            if len(stats["sample_fixes"]) < 10:
                stats["sample_fixes"].append({
                    "id": rid,
                    "title": title,
                    "removed": list(tags_to_remove),
                    "reason": f"primary_protein={primary_protein}",
                })

    seen_ids = {rid for rid, _ in updates}

    # Pass 2: Recipes with NULL/empty primary_protein -- check title + ingredients
    cur = conn.cursor()
    cur.execute("""
        SELECT id, title, diet_tags, ingredients::text, primary_protein
        FROM lake.recipes
        WHERE ('Vegan' = ANY(diet_tags) OR 'Vegetarian' = ANY(diet_tags))
          AND (primary_protein IS NULL OR primary_protein = '')
        ORDER BY quality_score DESC
    """)

    rows = cur.fetchall()
    cur.close()

    print(f"Pass 2: Checking {len(rows)} recipes with Vegan/Vegetarian tags and no primary_protein...")

    for rid, title, diet_tags, ingredients_text, primary_protein in rows:
        if rid in seen_ids:
            continue
        stats["total_checked"] += 1

        combined_text = f"{title} {ingredients_text}"

        # Check for meat keywords
        meat_matches = _text_contains_keyword(combined_text, MEAT_KEYWORDS, VEGAN_EXCEPTIONS)

        # Check for dairy keywords
        dairy_matches = _text_contains_keyword(combined_text, DAIRY_KEYWORDS, VEGAN_EXCEPTIONS)

        tags_to_remove = set()

        if meat_matches:
            # Meat found: remove both Vegan and Vegetarian
            if "Vegan" in diet_tags:
                tags_to_remove.add("Vegan")
            if "Vegetarian" in diet_tags:
                tags_to_remove.add("Vegetarian")

        if dairy_matches and not meat_matches:
            # Dairy found but no meat: remove Vegan only (still vegetarian)
            if "Vegan" in diet_tags:
                tags_to_remove.add("Vegan")

        if tags_to_remove:
            updates.append((rid, tags_to_remove))
            stats["recipes_fixed"] += 1
            if "Vegan" in tags_to_remove:
                stats["vegan_removed"] += 1
            if "Vegetarian" in tags_to_remove:
                stats["vegetarian_removed"] += 1

            if len(stats["sample_fixes"]) < 30:
                reason = []
                if meat_matches:
                    reason.append(f"meat: {meat_matches[:3]}")
                if dairy_matches:
                    reason.append(f"dairy: {dairy_matches[:3]}")
                stats["sample_fixes"].append({
                    "id": rid,
                    "title": title,
                    "removed": list(tags_to_remove),
                    "reason": ", ".join(reason),
                })

    print(f"\nAudit results:")
    print(f"  Total checked: {stats['total_checked']}")
    print(f"  Recipes to fix: {stats['recipes_fixed']}")
    print(f"  Vegan tag removals: {stats['vegan_removed']}")
    print(f"  Vegetarian tag removals: {stats['vegetarian_removed']}")

    if stats["sample_fixes"]:
        print(f"\nSample fixes (first {len(stats['sample_fixes'])}):")
        for fix in stats["sample_fixes"]:
            print(f"  [{fix['id']}] {fix['title']}")
            print(f"    Remove: {fix['removed']}, Reason: {fix['reason']}")

    if dry_run:
        print(f"\nDRY RUN: No changes applied. Use --apply to apply fixes.")
    else:
        print(f"\nApplying {len(updates)} fixes...")
        cur = conn.cursor()
        batch_size = 500
        for i in range(0, len(updates), batch_size):
            batch = updates[i:i + batch_size]
            for rid, tags_to_remove in batch:
                # Use array_remove to remove specific tags
                for tag in tags_to_remove:
                    cur.execute(
                        "UPDATE lake.recipes SET diet_tags = array_remove(diet_tags, %s) WHERE id = %s",
                        [tag, rid],
                    )
            conn.commit()
            print(f"  Applied batch {i // batch_size + 1} ({min(i + batch_size, len(updates))}/{len(updates)})")

        print(f"\nAll {len(updates)} fixes applied successfully.")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Audit and fix mistagged diet tags in lake.recipes"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually apply fixes (default is dry-run)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Preview changes without applying (default)"
    )
    args = parser.parse_args()

    dry_run = not args.apply

    with get_connection() as conn:
        stats = audit_mistagged_recipes(conn, dry_run=dry_run)

    return 0 if stats["recipes_fixed"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
