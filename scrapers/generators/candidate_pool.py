"""Query lake.recipes for filtered candidate pools."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Optional

from models import Recipe


def _row_to_recipe(row: tuple, columns: list[str]) -> Recipe:
    """Convert a database row to a Recipe object."""
    data = dict(zip(columns, row))

    # Convert Decimal fields to float
    for key in ("calories", "protein", "fat", "carbohydrates"):
        val = data.get(key)
        if isinstance(val, Decimal):
            data[key] = float(val)
        elif val is None:
            data[key] = 0.0

    # Parse JSONB fields that may come as strings
    for key in ("ingredients", "method"):
        val = data.get(key)
        if isinstance(val, str):
            data[key] = json.loads(val)
        elif val is None:
            data[key] = []

    # Array fields default to empty list
    for key in ("meal_types", "diet_tags", "normalized_cuisines"):
        if data.get(key) is None:
            data[key] = []

    return Recipe(
        id=data["id"],
        source_id=data.get("source_id", ""),
        slug=data.get("slug", ""),
        title=data.get("title", ""),
        url=data.get("url", "") or "",
        image=data.get("image", "") or "",
        calories=float(data["calories"]),
        protein=float(data["protein"]),
        fat=float(data["fat"]),
        carbohydrates=float(data["carbohydrates"]),
        total_time=data.get("total_time") or 0,
        serving_size=data.get("serving_size") or 1,
        ingredients=data.get("ingredients", []),
        method=data.get("method", []),
        meal_types=list(data.get("meal_types") or []),
        diet_tags=list(data.get("diet_tags") or []),
        normalized_cuisines=list(data.get("normalized_cuisines") or []),
        primary_protein=data.get("primary_protein") or "",
        quality_score=data.get("quality_score") or 0,
    )


COLUMNS = [
    "id", "source_id", "slug", "title", "url", "image",
    "calories", "protein", "fat", "carbohydrates",
    "total_time", "serving_size", "ingredients", "method",
    "meal_types", "diet_tags", "normalized_cuisines",
    "primary_protein", "quality_score",
]


def get_candidates(
    conn,
    meal_type: str,
    calorie_range: tuple[int, int],
    protein_min: int,
    dietary: Optional[list[str]] = None,
    excluded_ingredients: Optional[list[str]] = None,
    preferred_cuisines: Optional[list[str]] = None,
    max_prep_time: int = 60,
    min_quality_score: int = 50,
    require_image: bool = False,
    limit: int = 200,
    exclude_ids: Optional[set[int]] = None,
) -> list[Recipe]:
    """Query lake.recipes for candidates matching the given constraints.

    Returns up to `limit` Recipe objects sorted by quality_score DESC,
    with soft preference for preferred_cuisines (sort boost, not hard filter).
    Title-level deduplication: keeps only the highest quality_score version
    of each title to prevent the MIP solver from selecting duplicates.

    Args:
        exclude_ids: Recipe IDs to exclude (for cross-group deduplication).
    """
    conditions = [
        "nutrition_basis = 'per_serving'",
        "calories IS NOT NULL",
        "protein IS NOT NULL",
        "fat IS NOT NULL",
        "carbohydrates IS NOT NULL",
    ]
    params: list = []

    # Meal type filter
    conditions.append("%s = ANY(meal_types)")
    params.append(meal_type)

    # Calorie range
    conditions.append("calories >= %s")
    params.append(calorie_range[0])
    conditions.append("calories <= %s")
    params.append(calorie_range[1])

    # Protein minimum
    conditions.append("protein >= %s")
    params.append(protein_min)

    # Quality score
    conditions.append("quality_score >= %s")
    params.append(min_quality_score)

    # Max prep time
    if max_prep_time > 0:
        conditions.append("(total_time IS NULL OR total_time <= %s)")
        params.append(max_prep_time)

    # Require image
    if require_image:
        conditions.append("image IS NOT NULL AND image != ''")

    # Dietary filters
    if dietary:
        for tag in dietary:
            conditions.append("%s = ANY(diet_tags)")
            params.append(tag)

    # Excluded ingredients (ILIKE check on ingredients JSONB text)
    if excluded_ingredients:
        for ingredient in excluded_ingredients:
            conditions.append("NOT (ingredients::text ILIKE %s)")
            params.append(f"%{ingredient}%")

    # Cross-group deduplication: exclude recipe IDs already selected
    if exclude_ids:
        conditions.append("id != ALL(%s)")
        params.append(list(exclude_ids))

    where_clause = " AND ".join(conditions)

    # Cuisine preference: soft sort boost (not hard filter)
    cuisine_order = ""
    if preferred_cuisines:
        # Build a CASE expression that gives a bonus for preferred cuisines
        cuisine_cases = []
        for cuisine in preferred_cuisines:
            cuisine_cases.append(
                "CASE WHEN %s = ANY(normalized_cuisines) THEN 1 ELSE 0 END"
            )
            params.append(cuisine)
        cuisine_order = f"({' + '.join(cuisine_cases)}) DESC, "

    col_list = ", ".join(COLUMNS)

    # Title-level deduplication: use a subquery with ROW_NUMBER() to keep
    # only the highest quality_score version of each title (Bug 4 fix).
    query = f"""
        SELECT {col_list}
        FROM (
            SELECT {col_list},
                   ROW_NUMBER() OVER (PARTITION BY title ORDER BY quality_score DESC) AS rn
            FROM lake.recipes
            WHERE {where_clause}
        ) deduped
        WHERE rn = 1
        ORDER BY {cuisine_order}quality_score DESC
        LIMIT %s
    """
    params.append(limit)

    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()

    return [_row_to_recipe(row, COLUMNS) for row in rows]
