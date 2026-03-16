"""Phase 2: Swap enrichment - find and attach swap alternatives to cookbook recipes.

For each recipe in the cookbook, queries the full lake.recipes database to find
the top N most similar recipes that can serve as swap alternatives.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Optional

from models import Cookbook, Recipe, SwapRecipe
from recipe_vectorizer import (
    recipe_to_vector, compute_vectors_batch, find_similar,
)


# ---------------------------------------------------------------------------
# SQL query for swap candidates
# ---------------------------------------------------------------------------

SWAP_CANDIDATE_COLUMNS = [
    "id", "slug", "title", "image",
    "calories", "protein", "fat", "carbohydrates",
    "total_time", "primary_protein",
    "meal_types", "diet_tags", "normalized_cuisines",
    "quality_score",
]


def _query_swap_candidates(
    conn,
    meal_type: str,
    exclude_ids: set[int],
    dietary: list[str] | None = None,
    limit: int = 2000,
) -> list[Recipe]:
    """Query lake.recipes for swap candidates of a given meal_type.

    Returns lightweight Recipe objects (without ingredients/method for speed).

    Args:
        conn: psycopg2 connection.
        meal_type: Target meal type (e.g., "Breakfast").
        exclude_ids: Recipe IDs to exclude from results.
        dietary: Dietary constraint tags (e.g., ["Vegan", "Keto"]).
            Swap candidates must have ALL specified tags.
        limit: Max candidates to fetch.

    Returns:
        List of Recipe objects sorted by quality_score DESC.
    """
    conditions = [
        "nutrition_basis = 'per_serving'",
        "calories IS NOT NULL",
        "protein IS NOT NULL",
        "fat IS NOT NULL",
        "carbohydrates IS NOT NULL",
        "%s = ANY(meal_types)",
        "quality_score >= 40",
    ]
    params: list = [meal_type]

    # Dietary filters (Bug 1 fix): swap candidates must match
    # the cookbook's dietary constraints
    if dietary:
        for tag in dietary:
            conditions.append("%s = ANY(diet_tags)")
            params.append(tag)

    where_clause = " AND ".join(conditions)
    col_list = ", ".join(SWAP_CANDIDATE_COLUMNS)
    query = f"""
        SELECT {col_list}
        FROM lake.recipes
        WHERE {where_clause}
        ORDER BY quality_score DESC
        LIMIT %s
    """
    params.append(limit)

    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()

    recipes = []
    for row in rows:
        data = dict(zip(SWAP_CANDIDATE_COLUMNS, row))

        # Convert Decimal to float
        for key in ("calories", "protein", "fat", "carbohydrates"):
            val = data.get(key)
            if isinstance(val, Decimal):
                data[key] = float(val)
            elif val is None:
                data[key] = 0.0

        rid = data["id"]
        if rid in exclude_ids:
            continue

        # Build a lightweight Recipe (no ingredients/method needed for vectorization)
        recipes.append(Recipe(
            id=rid,
            source_id="",
            slug=data.get("slug", ""),
            title=data.get("title", ""),
            url="",
            image=data.get("image", "") or "",
            calories=float(data["calories"]),
            protein=float(data["protein"]),
            fat=float(data["fat"]),
            carbohydrates=float(data["carbohydrates"]),
            total_time=data.get("total_time") or 0,
            serving_size=1,
            ingredients=[],
            method=[],
            meal_types=list(data.get("meal_types") or []),
            diet_tags=list(data.get("diet_tags") or []),
            normalized_cuisines=list(data.get("normalized_cuisines") or []),
            primary_protein=data.get("primary_protein") or "",
            quality_score=data.get("quality_score") or 0,
        ))

    return recipes


# ---------------------------------------------------------------------------
# Main enrichment function
# ---------------------------------------------------------------------------

def enrich_cookbook_with_swaps(
    cookbook: Cookbook,
    conn,
    swaps_per_recipe: int = 5,
    macro_tolerance_pct: float = 0.15,
    dietary: list[str] | None = None,
) -> Cookbook:
    """For each recipe in the cookbook, find top N swap alternatives from the lake.

    Algorithm:
    1. Collect all recipe IDs already in the cookbook.
    2. For each cookbook group (by meal_type):
       a. Query a large candidate pool from lake.recipes for that meal_type.
       b. Exclude cookbook IDs from candidates.
       c. Vectorize all candidates + group recipes.
       d. For each recipe in the group, find top N similar swaps.
    3. Return the enriched cookbook (mutated in-place).

    Args:
        cookbook: Cookbook with groups and recipes.
        conn: psycopg2 database connection.
        swaps_per_recipe: Number of swap alternatives per recipe.
        macro_tolerance_pct: Macro tolerance as fraction (0.15 = 15%).
        dietary: Dietary constraint tags (e.g., ["Vegan", "Keto"]).
            Passed through to swap candidate queries so swaps respect
            the cookbook's dietary constraints (Bug 1 fix).

    Returns:
        The same Cookbook object, enriched with swaps on each recipe.
    """
    start_time = time.time()

    # Collect all recipe IDs in the cookbook
    cookbook_ids: set[int] = set()
    for group in cookbook.groups:
        for recipe in group.recipes:
            cookbook_ids.add(recipe.id)

    total_recipes = 0
    total_swaps_found = 0

    for group in cookbook.groups:
        if not group.recipes:
            continue

        meal_type = group.meal_type
        print(f"\n  Enriching {group.name} ({meal_type}): "
              f"{len(group.recipes)} recipes, finding {swaps_per_recipe} swaps each")

        # Query candidate pool from DB (with dietary constraints)
        candidates = _query_swap_candidates(
            conn=conn,
            meal_type=meal_type,
            exclude_ids=cookbook_ids,
            dietary=dietary,
            limit=2000,
        )
        print(f"    Candidate pool: {len(candidates)} recipes from lake")

        if not candidates:
            print(f"    WARNING: No swap candidates found for {meal_type}")
            continue

        # Vectorize candidates
        cand_vectors = compute_vectors_batch(candidates)

        # Track used swap IDs for info (but swaps CAN be shared across recipes)
        used_swap_ids: set[int] = set()

        for recipe in group.recipes:
            target_vec = recipe_to_vector(recipe)

            swaps = find_similar(
                target_recipe=recipe,
                target_vector=target_vec,
                candidate_vectors=cand_vectors,
                candidate_recipes=candidates,
                n=swaps_per_recipe,
                macro_tolerance_pct=macro_tolerance_pct,
                exclude_ids=cookbook_ids,
            )

            recipe.swaps = swaps
            total_recipes += 1
            total_swaps_found += len(swaps)

            for s in swaps:
                used_swap_ids.add(s.recipe_id)

        print(f"    Swaps found: {sum(len(r.swaps) for r in group.recipes)} "
              f"across {len(group.recipes)} recipes "
              f"({len(used_swap_ids)} unique swap recipes)")

    elapsed = time.time() - start_time
    avg_swaps = total_swaps_found / total_recipes if total_recipes > 0 else 0
    print(f"\n  Enrichment complete: {total_recipes} recipes enriched, "
          f"{total_swaps_found} total swaps ({avg_swaps:.1f} avg), "
          f"{elapsed:.1f}s")

    return cookbook
