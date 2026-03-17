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
    exclude_ids: Optional[set] = None,
    db_source: str = "lake",
) -> list[Recipe]:
    """Query recipes for candidates matching the given constraints.

    Returns up to `limit` Recipe objects sorted by quality_score DESC,
    with soft preference for preferred_cuisines (sort boost, not hard filter).
    Title-level deduplication: keeps only the highest quality_score version
    of each title to prevent the MIP solver from selecting duplicates.

    Args:
        exclude_ids: Recipe IDs to exclude (for cross-group deduplication).
        db_source: 'lake' (default) or 'production' (queries public.bp_cpts).
    """
    if db_source == "production":
        return _get_candidates_production(
            conn=conn,
            meal_type=meal_type,
            calorie_range=calorie_range,
            protein_min=protein_min,
            dietary=dietary,
            excluded_ingredients=excluded_ingredients,
            preferred_cuisines=preferred_cuisines,
            max_prep_time=max_prep_time,
            limit=limit,
            exclude_ids=exclude_ids,
        )

    return _get_candidates_lake(
        conn=conn,
        meal_type=meal_type,
        calorie_range=calorie_range,
        protein_min=protein_min,
        dietary=dietary,
        excluded_ingredients=excluded_ingredients,
        preferred_cuisines=preferred_cuisines,
        max_prep_time=max_prep_time,
        min_quality_score=min_quality_score,
        require_image=require_image,
        limit=limit,
        exclude_ids=exclude_ids,
    )


def _get_candidates_lake(
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
    exclude_ids: Optional[set] = None,
) -> list[Recipe]:
    """Query lake.recipes for candidates (original implementation)."""
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


# ---------------------------------------------------------------------------
# Production (public.bp_cpts) query
# ---------------------------------------------------------------------------

PRODUCTION_COLUMNS = [
    "id", "title", "slug",
    "calories", "protein", "fat", "carbohydrates",
    "total_time", "serving_size",
    "ingredients", "method",
    "meal_types", "diet_tags", "cuisines",
    "quality_score",
]


def _row_to_recipe_production(row: tuple, columns: list[str]) -> Recipe:
    """Convert a production query row to a Recipe object."""
    data = dict(zip(columns, row))

    # Convert numeric fields
    for key in ("calories", "protein", "fat", "carbohydrates"):
        val = data.get(key)
        if isinstance(val, Decimal):
            data[key] = float(val)
        elif val is None:
            data[key] = 0.0
        else:
            data[key] = float(val)

    # JSONB fields from meta come as Python objects (list/dict) via psycopg2
    for key in ("ingredients", "method"):
        val = data.get(key)
        if isinstance(val, str):
            data[key] = json.loads(val)
        elif val is None:
            data[key] = []

    # Array/list fields
    for key in ("meal_types", "diet_tags", "cuisines"):
        val = data.get(key)
        if isinstance(val, str):
            data[key] = json.loads(val)
        elif val is None:
            data[key] = []

    return Recipe(
        id=data["id"],  # UUID string
        source_id="",
        slug=data.get("slug", "") or "",
        title=data.get("title", ""),
        url="",
        image="",
        calories=float(data["calories"]),
        protein=float(data["protein"]),
        fat=float(data["fat"]),
        carbohydrates=float(data["carbohydrates"]),
        total_time=int(data.get("total_time") or 0),
        serving_size=int(data.get("serving_size") or 1),
        ingredients=data.get("ingredients", []),
        method=data.get("method", []),
        meal_types=list(data.get("meal_types") or []),
        diet_tags=list(data.get("diet_tags") or []),
        normalized_cuisines=list(data.get("cuisines") or []),
        primary_protein="",
        quality_score=int(data.get("quality_score") or 0),
    )


def _get_candidates_production(
    conn,
    meal_type: str,
    calorie_range: tuple[int, int],
    protein_min: int,
    dietary: Optional[list[str]] = None,
    excluded_ingredients: Optional[list[str]] = None,
    preferred_cuisines: Optional[list[str]] = None,
    max_prep_time: int = 60,
    limit: int = 200,
    exclude_ids: Optional[set] = None,
) -> list[Recipe]:
    """Query public.bp_cpts for production recipe candidates.

    Production recipes store all nutritional data in a JSONB ``meta`` column.
    The ``id`` is a UUID (text), and there are no ``nutrition_basis``,
    ``quality_score``, or ``primary_protein`` columns -- those are lake-only.
    """
    conditions = [
        "cpt_name = 'recipes'",
        "status = 'published'",
        "deleted_at IS NULL",
        "(meta->>'calories') IS NOT NULL",
        "(meta->>'calories')::float > 0",
        "(meta->>'protein') IS NOT NULL",
        "(meta->>'fat') IS NOT NULL",
        "(meta->>'carbohydrates') IS NOT NULL",
    ]
    params: list = []

    # Meal type filter (meta->'meals' is a JSONB array)
    conditions.append("meta->'meals' @> %s::jsonb")
    params.append(json.dumps([meal_type]))

    # Calorie range
    conditions.append("(meta->>'calories')::float >= %s")
    params.append(calorie_range[0])
    conditions.append("(meta->>'calories')::float <= %s")
    params.append(calorie_range[1])

    # Protein minimum
    conditions.append("(meta->>'protein')::float >= %s")
    params.append(protein_min)

    # Max prep time
    if max_prep_time > 0:
        conditions.append(
            "((meta->>'total_time') IS NULL OR (meta->>'total_time')::int <= %s)"
        )
        params.append(max_prep_time)

    # Dietary filters (meta->'dietary_requirements' is a JSONB array)
    if dietary:
        for tag in dietary:
            conditions.append("meta->'dietary_requirements' @> %s::jsonb")
            params.append(json.dumps([tag]))

    # Excluded ingredients
    if excluded_ingredients:
        for ingredient in excluded_ingredients:
            conditions.append("NOT (meta->'ingredients'::text ILIKE %s)")
            params.append(f"%{ingredient}%")

    # Cross-group deduplication
    if exclude_ids:
        conditions.append("id::text != ALL(%s)")
        params.append([str(eid) for eid in exclude_ids])

    where_clause = " AND ".join(conditions)

    # Cuisine preference (soft sort boost)
    cuisine_order = ""
    if preferred_cuisines:
        cuisine_cases = []
        for cuisine in preferred_cuisines:
            cuisine_cases.append(
                "CASE WHEN meta->'cuisines' @> %s::jsonb THEN 1 ELSE 0 END"
            )
            params.append(json.dumps([cuisine]))
        cuisine_order = f"({' + '.join(cuisine_cases)}) DESC, "

    # Build the SELECT with meta extraction
    query = f"""
        SELECT
            id::text,
            name as title,
            slug,
            (meta->>'calories')::float as calories,
            (meta->>'protein')::float as protein,
            (meta->>'fat')::float as fat,
            (meta->>'carbohydrates')::float as carbohydrates,
            (meta->>'total_time')::int as total_time,
            (meta->>'serving_size')::int as serving_size,
            meta->'ingredients' as ingredients,
            meta->'method' as method,
            meta->'meals' as meal_types,
            meta->'dietary_requirements' as diet_tags,
            meta->'cuisines' as cuisines,
            COALESCE((meta->>'lake_quality_score')::int, 50) as quality_score
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY name ORDER BY created_at DESC) AS rn
            FROM public.bp_cpts
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

    return [_row_to_recipe_production(row, PRODUCTION_COLUMNS) for row in rows]
