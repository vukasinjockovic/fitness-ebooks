"""Tests for Phase 2: Swappable Recipe Enrichment Layer."""

import sys
import os
import json

import pytest
import numpy as np

# Allow imports from parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import (
    Recipe, CookbookGroup, Cookbook, CookbookStats,
    SwapRecipe, MealSlot, DayPlan, MealPlanInput,
)
from recipe_vectorizer import (
    recipe_to_vector, compute_vectors_batch, find_similar,
    DEFAULT_WEIGHTS, VECTOR_DIM,
    TOP_CUISINES_VEC, PROTEINS_VEC, DIET_FLAGS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_recipe(
    id=1,
    title="Test Recipe",
    calories=500.0,
    protein=30.0,
    fat=20.0,
    carbohydrates=50.0,
    meal_types=None,
    diet_tags=None,
    normalized_cuisines=None,
    primary_protein="Chicken",
    total_time=30,
    quality_score=80,
    **kwargs,
) -> Recipe:
    return Recipe(
        id=id,
        source_id=f"src-{id}",
        slug=f"test-recipe-{id}",
        title=title,
        url=f"https://example.com/recipe/{id}",
        image=f"https://img.example.com/{id}.jpg",
        calories=calories,
        protein=protein,
        fat=fat,
        carbohydrates=carbohydrates,
        total_time=total_time,
        serving_size=1,
        ingredients=[],
        method=[],
        meal_types=meal_types or ["Lunch"],
        diet_tags=diet_tags or [],
        normalized_cuisines=normalized_cuisines or ["American"],
        primary_protein=primary_protein,
        quality_score=quality_score,
    )


# ---------------------------------------------------------------------------
# 1. test_recipe_to_vector_dimensions
# ---------------------------------------------------------------------------

class TestRecipeToVector:
    def test_recipe_to_vector_dimensions(self):
        """Vector has the correct number of dimensions."""
        r = _make_recipe()
        v = recipe_to_vector(r)
        assert isinstance(v, np.ndarray)
        assert v.dtype == np.float32
        # Expected dimensions: 4 macros + 5 meal_types + len(TOP_CUISINES_VEC) +
        # len(PROTEINS_VEC) + 1 time + len(DIET_FLAGS)
        expected_dim = (
            4  # calories, protein, fat, carbs
            + 5  # meal types
            + len(TOP_CUISINES_VEC)
            + len(PROTEINS_VEC)
            + 1  # time
            + len(DIET_FLAGS)
        )
        assert v.shape == (expected_dim,), f"Expected {expected_dim} dims, got {v.shape[0]}"
        assert v.shape[0] == VECTOR_DIM

    def test_recipe_to_vector_weights(self):
        """Weights change the vector values."""
        r = _make_recipe(calories=500, protein=30)
        v_default = recipe_to_vector(r)
        custom_weights = dict(DEFAULT_WEIGHTS)
        custom_weights['calories'] = 10.0  # much higher weight
        v_custom = recipe_to_vector(r, weights=custom_weights)
        # The calorie dimension (index 0) should differ
        assert v_custom[0] != v_default[0]
        # Other dimensions with same weights should be equal
        # Protein (index 1) has same weight in both
        assert v_custom[1] == v_default[1]

    def test_vector_null_safe(self):
        """Handles recipes with None meal_types, diet_tags, etc."""
        r = _make_recipe(
            meal_types=None,
            diet_tags=None,
            normalized_cuisines=None,
            primary_protein=None,
            total_time=None,
        )
        # meal_types is defaulted to ["Lunch"] in _make_recipe, but let's force None
        r.meal_types = None
        r.diet_tags = None
        r.normalized_cuisines = None
        r.primary_protein = None
        r.total_time = None
        v = recipe_to_vector(r)
        assert v.shape == (VECTOR_DIM,)
        assert not np.any(np.isnan(v))


# ---------------------------------------------------------------------------
# 2. test_compute_vectors_batch
# ---------------------------------------------------------------------------

class TestComputeVectorsBatch:
    def test_batch_shape(self):
        """Batch vectorization produces correct shape."""
        recipes = [_make_recipe(id=i) for i in range(10)]
        mat = compute_vectors_batch(recipes)
        assert mat.shape == (10, VECTOR_DIM)

    def test_batch_matches_individual(self):
        """Batch vectors match individual computation."""
        recipes = [_make_recipe(id=i, calories=400 + i * 10) for i in range(5)]
        mat = compute_vectors_batch(recipes)
        for idx, r in enumerate(recipes):
            v = recipe_to_vector(r)
            np.testing.assert_array_almost_equal(mat[idx], v)


# ---------------------------------------------------------------------------
# 3. test_find_similar_respects_meal_type
# ---------------------------------------------------------------------------

class TestFindSimilar:
    def test_find_similar_respects_meal_type(self):
        """Swaps must share at least one meal_type with the target."""
        target = _make_recipe(id=100, meal_types=["Breakfast"], calories=400, protein=30)
        # Candidates: some breakfast, some lunch-only
        candidates = [
            _make_recipe(id=1, meal_types=["Breakfast"], calories=390, protein=29),
            _make_recipe(id=2, meal_types=["Lunch"], calories=400, protein=30),  # wrong meal type
            _make_recipe(id=3, meal_types=["Breakfast", "Lunch"], calories=410, protein=31),
            _make_recipe(id=4, meal_types=["Dinner"], calories=395, protein=28),  # wrong meal type
        ]
        target_vec = recipe_to_vector(target)
        cand_vecs = compute_vectors_batch(candidates)
        results = find_similar(target, target_vec, cand_vecs, candidates, n=5)
        # Only candidates with Breakfast should appear
        result_ids = {r.recipe_id for r in results}
        assert 1 in result_ids  # Breakfast
        assert 3 in result_ids  # Breakfast + Lunch
        assert 2 not in result_ids  # Lunch only
        assert 4 not in result_ids  # Dinner only

    def test_find_similar_respects_macro_tolerance(self):
        """Swaps must have calories and protein within tolerance."""
        target = _make_recipe(id=100, meal_types=["Lunch"], calories=500, protein=40)
        # ±15% of 500 = 425-575 cal; ±15% of 40 = 34-46 protein
        candidates = [
            _make_recipe(id=1, meal_types=["Lunch"], calories=510, protein=42),   # within
            _make_recipe(id=2, meal_types=["Lunch"], calories=700, protein=40),   # cal too high
            _make_recipe(id=3, meal_types=["Lunch"], calories=500, protein=10),   # pro too low
            _make_recipe(id=4, meal_types=["Lunch"], calories=430, protein=35),   # within
            _make_recipe(id=5, meal_types=["Lunch"], calories=300, protein=38),   # cal too low
        ]
        target_vec = recipe_to_vector(target)
        cand_vecs = compute_vectors_batch(candidates)
        results = find_similar(
            target, target_vec, cand_vecs, candidates,
            n=10, macro_tolerance_pct=0.15,
        )
        result_ids = {r.recipe_id for r in results}
        assert 1 in result_ids
        assert 4 in result_ids
        assert 2 not in result_ids  # 700 cal >> 575
        assert 3 not in result_ids  # 10g protein << 34
        assert 5 not in result_ids  # 300 cal << 425

    def test_find_similar_excludes_ids(self):
        """Excluded recipe IDs don't appear in results."""
        target = _make_recipe(id=100, meal_types=["Lunch"], calories=500, protein=30)
        candidates = [
            _make_recipe(id=1, meal_types=["Lunch"], calories=505, protein=31),
            _make_recipe(id=2, meal_types=["Lunch"], calories=510, protein=29),
            _make_recipe(id=3, meal_types=["Lunch"], calories=495, protein=30),
        ]
        target_vec = recipe_to_vector(target)
        cand_vecs = compute_vectors_batch(candidates)
        results = find_similar(
            target, target_vec, cand_vecs, candidates,
            n=5, exclude_ids={2},
        )
        result_ids = {r.recipe_id for r in results}
        assert 2 not in result_ids
        assert 1 in result_ids
        assert 3 in result_ids

    def test_find_similar_returns_n_or_fewer(self):
        """Returns at most N results (or fewer if not enough candidates)."""
        target = _make_recipe(id=100, meal_types=["Lunch"], calories=500, protein=30)
        # Only 2 valid candidates
        candidates = [
            _make_recipe(id=1, meal_types=["Lunch"], calories=510, protein=31),
            _make_recipe(id=2, meal_types=["Lunch"], calories=490, protein=29),
        ]
        target_vec = recipe_to_vector(target)
        cand_vecs = compute_vectors_batch(candidates)

        # Ask for 5 but only 2 exist
        results = find_similar(target, target_vec, cand_vecs, candidates, n=5)
        assert len(results) <= 5
        assert len(results) == 2

        # Ask for 1
        results = find_similar(target, target_vec, cand_vecs, candidates, n=1)
        assert len(results) == 1

    def test_find_similar_sorted_by_distance(self):
        """Results are sorted by similarity_score ascending (closer = better)."""
        target = _make_recipe(id=100, meal_types=["Lunch"], calories=500, protein=30,
                              fat=20, carbohydrates=50)
        candidates = [
            # Very similar
            _make_recipe(id=1, meal_types=["Lunch"], calories=502, protein=30,
                         fat=20, carbohydrates=50),
            # Somewhat different
            _make_recipe(id=2, meal_types=["Lunch"], calories=520, protein=33,
                         fat=25, carbohydrates=55),
            # More different
            _make_recipe(id=3, meal_types=["Lunch"], calories=550, protein=35,
                         fat=28, carbohydrates=60),
        ]
        target_vec = recipe_to_vector(target)
        cand_vecs = compute_vectors_batch(candidates)
        results = find_similar(target, target_vec, cand_vecs, candidates, n=5)
        # Scores should be non-decreasing
        scores = [r.similarity_score for r in results]
        assert scores == sorted(scores), f"Not sorted: {scores}"

    def test_find_similar_low_protein_tolerance(self):
        """For low-protein recipes, tolerance should be at least 5g."""
        # A 10g protein recipe: 15% of 10 = 1.5g, but we want at least 5g tolerance
        target = _make_recipe(id=100, meal_types=["Snack"], calories=150, protein=10)
        candidates = [
            # 4g difference from 10g = within 5g floor, but outside 15% (1.5g)
            _make_recipe(id=1, meal_types=["Snack"], calories=150, protein=14),
            # 6g difference - outside even the 5g floor
            _make_recipe(id=2, meal_types=["Snack"], calories=150, protein=16),
        ]
        target_vec = recipe_to_vector(target)
        cand_vecs = compute_vectors_batch(candidates)
        results = find_similar(
            target, target_vec, cand_vecs, candidates,
            n=5, macro_tolerance_pct=0.15,
        )
        result_ids = {r.recipe_id for r in results}
        assert 1 in result_ids, "14g protein should be within 5g floor of 10g"
        # 16g is 6g away from 10g, outside the max(15%, 5g) = 5g floor
        assert 2 not in result_ids, "16g protein should be outside tolerance"


# ---------------------------------------------------------------------------
# 4. test_swap_recipe_model
# ---------------------------------------------------------------------------

class TestSwapRecipeModel:
    def test_swap_json_serialization(self):
        """SwapRecipe serializes to dict correctly."""
        swap = SwapRecipe(
            recipe_id=42,
            title="Swap Recipe",
            slug="swap-recipe",
            image="https://img.example.com/42.jpg",
            calories=500.0,
            protein=35.0,
            fat=20.0,
            carbohydrates=45.0,
            total_time=25,
            primary_protein="Chicken",
            similarity_score=0.123,
        )
        d = swap.to_dict()
        assert d["recipe_id"] == 42
        assert d["title"] == "Swap Recipe"
        assert d["slug"] == "swap-recipe"
        assert d["calories"] == 500.0
        assert d["protein"] == 35.0
        assert d["fat"] == 20.0
        assert d["carbohydrates"] == 45.0
        assert d["total_time"] == 25
        assert d["primary_protein"] == "Chicken"
        assert d["similarity_score"] == 0.123
        assert d["image"] == "https://img.example.com/42.jpg"

    def test_swap_recipe_json_roundtrip(self):
        """SwapRecipe survives JSON serialization."""
        swap = SwapRecipe(
            recipe_id=1, title="A", slug="a", image="", calories=100,
            protein=10, fat=5, carbohydrates=15, total_time=10,
            primary_protein="Eggs", similarity_score=0.05,
        )
        serialized = json.dumps(swap.to_dict())
        deserialized = json.loads(serialized)
        assert deserialized["recipe_id"] == 1
        assert deserialized["similarity_score"] == 0.05

    def test_recipe_has_swaps_field(self):
        """Recipe model has a swaps field (list of SwapRecipe)."""
        r = _make_recipe()
        assert hasattr(r, 'swaps')
        assert r.swaps == []  # default empty

    def test_recipe_to_dict_includes_swaps(self):
        """Recipe.to_dict() includes swaps."""
        r = _make_recipe()
        swap = SwapRecipe(
            recipe_id=99, title="Swap", slug="swap", image="",
            calories=490, protein=29, fat=19, carbohydrates=48,
            total_time=20, primary_protein="Chicken", similarity_score=0.05,
        )
        r.swaps.append(swap)
        d = r.to_dict()
        assert "swaps" in d
        assert len(d["swaps"]) == 1
        assert d["swaps"][0]["recipe_id"] == 99


# ---------------------------------------------------------------------------
# 5. test_meal_slot_swaps
# ---------------------------------------------------------------------------

class TestMealSlotSwaps:
    def test_swaps_appear_in_mealslot(self):
        """MealSlot includes swaps in its to_dict output."""
        r = _make_recipe()
        swap = SwapRecipe(
            recipe_id=50, title="Alt", slug="alt", image="",
            calories=480, protein=28, fat=18, carbohydrates=52,
            total_time=35, primary_protein="Beef", similarity_score=0.1,
        )
        slot = MealSlot(
            meal_type="Lunch",
            recipe=r,
            serving_multiplier=1.0,
            adjusted_calories=500,
            adjusted_protein=30,
            adjusted_fat=20,
            adjusted_carbs=50,
            swaps=[swap.to_dict()],
        )
        d = slot.to_dict()
        assert "swaps" in d
        assert len(d["swaps"]) == 1
        assert d["swaps"][0]["recipe_id"] == 50


# ---------------------------------------------------------------------------
# 6. test_enrich_cookbook_adds_swaps (integration)
# ---------------------------------------------------------------------------

class TestEnrichCookbook:
    """Integration test requiring a database connection.

    These tests verify the enrichment works end-to-end with real DB data.
    Skipped if DB is not available.
    """

    @pytest.fixture
    def conn(self):
        """Get a database connection, skip if unavailable."""
        try:
            from config import get_connection
            with get_connection() as c:
                # Quick health check
                cur = c.cursor()
                cur.execute("SELECT COUNT(*) FROM lake.recipes WHERE nutrition_basis = 'per_serving'")
                count = cur.fetchone()[0]
                cur.close()
                if count < 100:
                    pytest.skip("Not enough recipes in DB")
                yield c
        except Exception as e:
            pytest.skip(f"DB not available: {e}")

    def _make_small_cookbook(self, conn) -> Cookbook:
        """Build a small cookbook with 2 groups from real DB data."""
        from candidate_pool import get_candidates
        from cookbook_generator import _solve_group

        # Get some breakfast recipes
        breakfast_candidates = get_candidates(
            conn, meal_type="Breakfast",
            calorie_range=(300, 600), protein_min=20,
            min_quality_score=50, limit=50,
        )
        # Get some lunch recipes
        lunch_candidates = get_candidates(
            conn, meal_type="Lunch",
            calorie_range=(400, 700), protein_min=25,
            min_quality_score=50, limit=50,
        )

        b_selected, _, _ = _solve_group(breakfast_candidates, 5, (300, 600), 20, True, [])
        l_selected, _, _ = _solve_group(lunch_candidates, 5, (400, 700), 25, True, [])

        cookbook = Cookbook(name="Test Cookbook")
        cookbook.groups = [
            CookbookGroup(name="Breakfast", meal_type="Breakfast", recipes=b_selected),
            CookbookGroup(name="Lunch", meal_type="Lunch", recipes=l_selected),
        ]
        cookbook.compute_stats()
        return cookbook

    def test_enrich_cookbook_adds_swaps(self, conn):
        """After enrichment, recipes in the cookbook have swap alternatives."""
        from swap_enricher import enrich_cookbook_with_swaps

        cookbook = self._make_small_cookbook(conn)
        enriched = enrich_cookbook_with_swaps(
            cookbook, conn, swaps_per_recipe=3, macro_tolerance_pct=0.15,
        )

        found_any_swaps = False
        for group in enriched.groups:
            for recipe in group.recipes:
                if recipe.swaps:
                    found_any_swaps = True
                    # Each swap should NOT be a recipe in the cookbook
                    cookbook_ids = set()
                    for g in enriched.groups:
                        for r in g.recipes:
                            cookbook_ids.add(r.id)
                    for swap in recipe.swaps:
                        assert swap.recipe_id not in cookbook_ids, (
                            f"Swap {swap.recipe_id} should not be in cookbook"
                        )
        assert found_any_swaps, "Enrichment should add at least some swaps"

    def test_swap_macros_are_similar(self, conn):
        """Every swap's macros are within tolerance of its parent recipe."""
        from swap_enricher import enrich_cookbook_with_swaps

        cookbook = self._make_small_cookbook(conn)
        enriched = enrich_cookbook_with_swaps(
            cookbook, conn, swaps_per_recipe=5, macro_tolerance_pct=0.15,
        )

        tolerance = 0.15
        for group in enriched.groups:
            for recipe in group.recipes:
                for swap in recipe.swaps:
                    # Calorie tolerance
                    cal_diff = abs(swap.calories - recipe.calories)
                    cal_limit = recipe.calories * tolerance
                    assert cal_diff <= cal_limit + 1, (
                        f"Swap '{swap.title}' cal {swap.calories} vs recipe "
                        f"'{recipe.title}' cal {recipe.calories}: diff={cal_diff:.0f} > limit={cal_limit:.0f}"
                    )

                    # Protein tolerance (use max(tolerance, 5g floor))
                    pro_diff = abs(swap.protein - recipe.protein)
                    pro_limit = max(recipe.protein * tolerance, 5)
                    assert pro_diff <= pro_limit + 1, (
                        f"Swap '{swap.title}' pro {swap.protein} vs recipe "
                        f"'{recipe.title}' pro {recipe.protein}: diff={pro_diff:.1f} > limit={pro_limit:.1f}"
                    )

    def test_swaps_appear_in_mealplan(self, conn):
        """End-to-end: MealSlot.swaps are populated when cookbook has swaps."""
        from swap_enricher import enrich_cookbook_with_swaps
        from mealplan_generator import generate_mealplan

        cookbook = self._make_small_cookbook(conn)
        enriched = enrich_cookbook_with_swaps(
            cookbook, conn, swaps_per_recipe=3, macro_tolerance_pct=0.15,
        )

        mp_input = MealPlanInput(
            weeks=1,
            daily_calories=1800,
            daily_calories_tolerance=300,
            daily_protein=120,
            daily_protein_tolerance=30,
            daily_carbs=200,
            daily_carbs_tolerance=40,
            daily_fat=70,
            daily_fat_tolerance=20,
            serving_multipliers=[1.0],
        )
        plan = generate_mealplan(mp_input, enriched)

        found_swaps_in_plan = False
        for week in plan.weeks:
            for day in week.days:
                for meal in day.meals:
                    if meal.swaps:
                        found_swaps_in_plan = True
                        break
        assert found_swaps_in_plan, "Meal plan should carry over swaps from enriched cookbook"
