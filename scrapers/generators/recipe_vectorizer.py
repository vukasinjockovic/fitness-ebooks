"""Phase 2: Recipe vectorization and similarity search for swap enrichment.

Computes feature vectors for recipes to enable fast similarity search.
Uses cosine distance with hard filters for meal_type and macro tolerance.
"""

from __future__ import annotations

import numpy as np

from models import Recipe, SwapRecipe


# ---------------------------------------------------------------------------
# Constants for vector construction
# ---------------------------------------------------------------------------

MEAL_TYPE_LABELS = ['Breakfast', 'Lunch', 'Dinner', 'Snack', 'Dessert']

TOP_CUISINES_VEC = [
    'American', 'Italian', 'Mexican', 'Indian', 'Chinese', 'French',
    'Thai', 'Japanese', 'Mediterranean', 'Greek', 'British', 'Asian',
    'Korean', 'Spanish', 'Middle Eastern',
]

PROTEINS_VEC = [
    'Chicken', 'Beef', 'Pork', 'Fish/Seafood', 'Turkey', 'Lamb', 'Game',
    'Tofu/Tempeh', 'Eggs', 'Legumes',
]

DIET_FLAGS = [
    'Keto', 'Gluten-Free', 'Vegan', 'Vegetarian', 'Dairy-Free',
    'High-Protein', 'Low-Carb',
]

# Total vector dimensionality
VECTOR_DIM = (
    4                       # macros: calories, protein, fat, carbs
    + len(MEAL_TYPE_LABELS) # meal type one-hot
    + len(TOP_CUISINES_VEC) # cuisine one-hot
    + len(PROTEINS_VEC)     # protein type one-hot
    + 1                     # prep time normalized
    + len(DIET_FLAGS)       # diet flags
)

DEFAULT_WEIGHTS = {
    'calories': 3.0,
    'protein': 3.0,
    'fat': 2.0,
    'carbs': 2.0,
    'meal_type': 5.0,   # must match for valid swap
    'cuisine': 1.0,
    'protein_type': 1.5,
    'time': 1.0,
    'diet': 2.0,
}


# ---------------------------------------------------------------------------
# Vectorization
# ---------------------------------------------------------------------------

def recipe_to_vector(recipe: Recipe, weights: dict | None = None) -> np.ndarray:
    """Convert a Recipe to a numpy feature vector for similarity search.

    The vector has ~40 dimensions. Weights control what "similar" means:
    higher weight = more important for similarity matching.

    Args:
        recipe: Recipe to vectorize.
        weights: Optional weight overrides (keys from DEFAULT_WEIGHTS).

    Returns:
        1-D float32 numpy array of shape (VECTOR_DIM,).
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS

    v = []

    # Macros normalized 0-1, then weighted
    v.append((recipe.calories / 1000.0) * weights.get('calories', 3.0))
    v.append((recipe.protein / 60.0) * weights.get('protein', 3.0))
    v.append((recipe.fat / 60.0) * weights.get('fat', 2.0))
    v.append((recipe.carbohydrates / 100.0) * weights.get('carbs', 2.0))

    # Meal types one-hot (critical for swap matching)
    meal_types = recipe.meal_types or []
    for m in MEAL_TYPE_LABELS:
        v.append((1.0 if m in meal_types else 0.0) * weights.get('meal_type', 5.0))

    # Cuisine one-hot (top 15)
    cuisines = recipe.normalized_cuisines or []
    for c in TOP_CUISINES_VEC:
        v.append((1.0 if c in cuisines else 0.0) * weights.get('cuisine', 1.0))

    # Primary protein one-hot
    primary_protein = recipe.primary_protein or ''
    for p in PROTEINS_VEC:
        v.append((1.0 if primary_protein == p else 0.0) * weights.get('protein_type', 1.5))

    # Prep time normalized to 0-1 (capped at 120 min)
    time_val = min(recipe.total_time or 30, 120) / 120.0
    v.append(time_val * weights.get('time', 1.0))

    # Diet flags
    diet_tags = recipe.diet_tags or []
    for d in DIET_FLAGS:
        v.append((1.0 if d in diet_tags else 0.0) * weights.get('diet', 2.0))

    return np.array(v, dtype=np.float32)


def compute_vectors_batch(recipes: list[Recipe], weights: dict | None = None) -> np.ndarray:
    """Vectorize a batch of recipes.

    Args:
        recipes: List of Recipe objects.
        weights: Optional weight overrides.

    Returns:
        (N, VECTOR_DIM) float32 numpy array.
    """
    if not recipes:
        return np.empty((0, VECTOR_DIM), dtype=np.float32)

    vecs = [recipe_to_vector(r, weights) for r in recipes]
    return np.stack(vecs)


# ---------------------------------------------------------------------------
# Similarity search
# ---------------------------------------------------------------------------

def find_similar(
    target_recipe: Recipe,
    target_vector: np.ndarray,
    candidate_vectors: np.ndarray,
    candidate_recipes: list[Recipe],
    n: int = 5,
    macro_tolerance_pct: float = 0.15,
    exclude_ids: set | None = None,
) -> list[SwapRecipe]:
    """Find top-N similar recipes using cosine distance + hard filters.

    Hard filters (must pass all to be a valid swap):
    - At least one shared meal_type with target
    - Calories within +/- macro_tolerance_pct of target
    - Protein within +/- max(macro_tolerance_pct, 5g / target_protein) of target
      (5g floor handles low-protein recipes)

    Args:
        target_recipe: The recipe to find swaps for.
        target_vector: Pre-computed vector for target.
        candidate_vectors: (N, D) matrix of candidate vectors.
        candidate_recipes: Corresponding Recipe objects.
        n: Maximum number of swaps to return.
        macro_tolerance_pct: Tolerance as fraction (0.15 = 15%).
        exclude_ids: Recipe IDs to exclude (e.g., already in cookbook).

    Returns:
        List of SwapRecipe objects sorted by similarity (closest first).
    """
    if exclude_ids is None:
        exclude_ids = set()

    target_meal_types = set(target_recipe.meal_types or [])
    target_cal = target_recipe.calories
    target_pro = target_recipe.protein

    # Compute cosine distances: 1 - cosine_similarity
    # cosine_sim = dot(a, b) / (norm(a) * norm(b))
    target_norm = np.linalg.norm(target_vector)
    if target_norm == 0:
        # Degenerate case: target vector is all zeros
        target_norm = 1e-10

    cand_norms = np.linalg.norm(candidate_vectors, axis=1)
    cand_norms = np.where(cand_norms == 0, 1e-10, cand_norms)

    dots = candidate_vectors @ target_vector
    cosine_sims = dots / (cand_norms * target_norm)
    cosine_dists = 1.0 - cosine_sims

    # Apply hard filters
    results: list[tuple[float, int]] = []  # (distance, index)

    for idx, cand in enumerate(candidate_recipes):
        # Exclude by ID
        if cand.id in exclude_ids:
            continue

        # Exclude target itself
        if cand.id == target_recipe.id:
            continue

        # HARD FILTER: at least one shared meal_type
        cand_meal_types = set(cand.meal_types or [])
        if not (target_meal_types & cand_meal_types):
            continue

        # HARD FILTER: calories within tolerance
        cal_limit = target_cal * macro_tolerance_pct
        if abs(cand.calories - target_cal) > cal_limit:
            continue

        # HARD FILTER: protein within tolerance (with 5g floor)
        pro_tolerance = max(target_pro * macro_tolerance_pct, 5.0)
        if abs(cand.protein - target_pro) > pro_tolerance:
            continue

        results.append((float(cosine_dists[idx]), idx))

    # Sort by distance (ascending = most similar first)
    results.sort(key=lambda x: x[0])

    # Build SwapRecipe objects for top N
    swaps = []
    for dist, idx in results[:n]:
        cand = candidate_recipes[idx]
        swaps.append(SwapRecipe(
            recipe_id=cand.id,
            title=cand.title,
            slug=cand.slug,
            image=cand.image,
            calories=cand.calories,
            protein=cand.protein,
            fat=cand.fat,
            carbohydrates=cand.carbohydrates,
            total_time=cand.total_time,
            primary_protein=cand.primary_protein,
            similarity_score=dist,
        ))

    return swaps
