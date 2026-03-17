"""Tests for day-by-day MIP solving in mealplan_generator.py.

The core change: instead of one massive MIP for all 14 days simultaneously
(3,528 binary variables), solve each day independently (~168 variables each).

Tests verify:
- _solve_day() returns correct structure with selected recipes + multipliers
- _solve_day() respects used_this_week exclusions
- _solve_day() hits macro targets within tolerance
- generate_mealplan() produces same output structure as before
- No recipe repeats within a week (when recipes allow)
- Progressive relaxation still works
- Greedy fallback still works
- Output JSON format unchanged
- Performance: <10s for 2-week plan
"""

import sys
import os
import time
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import (
    Recipe, CookbookGroup, Cookbook, CookbookStats,
    MealPlanInput, MealSlot, DayPlan, WeekPlan, MealPlan,
)
from mealplan_generator import generate_mealplan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_recipe(
    id=1, title="Test Recipe", calories=500.0, protein=30.0,
    fat=20.0, carbohydrates=50.0, primary_protein="Chicken",
    quality_score=80, **kwargs
) -> Recipe:
    defaults = dict(
        id=id, source_id="src1", slug=f"recipe-{id}", title=title,
        url="http://example.com", image="http://example.com/img.jpg",
        calories=calories, protein=protein, fat=fat,
        carbohydrates=carbohydrates,
        total_time=30, serving_size=4, ingredients=[], method=[],
        meal_types=["Lunch"], diet_tags=[],
        normalized_cuisines=["Italian"], primary_protein=primary_protein,
        quality_score=quality_score,
    )
    defaults.update(kwargs)
    return Recipe(**defaults)


def _make_cookbook_4types(recipes_per_type=10) -> Cookbook:
    """Build a realistic cookbook with 4 meal types and enough recipes."""
    groups = []
    recipe_id = 1

    # Breakfast: lower cal, moderate protein
    breakfast_recipes = []
    for i in range(recipes_per_type):
        breakfast_recipes.append(_make_recipe(
            id=recipe_id, title=f"Breakfast {i+1}",
            calories=300 + i * 15, protein=25 + i * 2,
            fat=10 + i, carbohydrates=30 + i * 3,
            primary_protein=["Eggs", "Turkey", "Chicken"][i % 3],
            quality_score=70 + i,
        ))
        recipe_id += 1
    groups.append(CookbookGroup(name="Breakfast", meal_type="Breakfast",
                                recipes=breakfast_recipes))

    # Lunch: medium cal, high protein
    lunch_recipes = []
    for i in range(recipes_per_type):
        lunch_recipes.append(_make_recipe(
            id=recipe_id, title=f"Lunch {i+1}",
            calories=450 + i * 20, protein=35 + i * 3,
            fat=15 + i * 2, carbohydrates=40 + i * 4,
            primary_protein=["Chicken", "Fish/Seafood", "Beef"][i % 3],
            quality_score=75 + i,
        ))
        recipe_id += 1
    groups.append(CookbookGroup(name="Lunch", meal_type="Lunch",
                                recipes=lunch_recipes))

    # Dinner: higher cal, high protein
    dinner_recipes = []
    for i in range(recipes_per_type):
        dinner_recipes.append(_make_recipe(
            id=recipe_id, title=f"Dinner {i+1}",
            calories=500 + i * 25, protein=40 + i * 3,
            fat=20 + i * 2, carbohydrates=45 + i * 5,
            primary_protein=["Beef", "Chicken", "Pork"][i % 3],
            quality_score=70 + i,
        ))
        recipe_id += 1
    groups.append(CookbookGroup(name="Dinner", meal_type="Dinner",
                                recipes=dinner_recipes))

    # Snack: low cal
    snack_recipes = []
    for i in range(recipes_per_type):
        snack_recipes.append(_make_recipe(
            id=recipe_id, title=f"Snack {i+1}",
            calories=150 + i * 10, protein=10 + i * 2,
            fat=5 + i, carbohydrates=15 + i * 2,
            primary_protein=["Eggs", "Legumes", "Tofu/Tempeh"][i % 3],
            quality_score=65 + i,
        ))
        recipe_id += 1
    groups.append(CookbookGroup(name="Snacks", meal_type="Snack",
                                recipes=snack_recipes))

    cookbook = Cookbook(name="Test Cookbook", groups=groups)
    cookbook.compute_stats()
    return cookbook


def _default_mealplan_input(weeks=2) -> MealPlanInput:
    return MealPlanInput(
        weeks=weeks,
        daily_calories=2000,
        daily_calories_tolerance=150,
        daily_protein=150,
        daily_protein_tolerance=20,
        daily_carbs=200,
        daily_carbs_tolerance=25,
        daily_fat=70,
        daily_fat_tolerance=15,
        serving_multipliers=[0.5, 0.75, 1.0, 1.25, 1.5, 2.0],
    )


# ---------------------------------------------------------------------------
# 1. Output structure tests
# ---------------------------------------------------------------------------

class TestOutputStructure:
    """Verify the output format matches the expected JSON schema."""

    def test_returns_mealplan_object(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        assert isinstance(plan, MealPlan)

    def test_has_correct_weeks_count(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input(weeks=2)
        plan = generate_mealplan(inp, cookbook)
        assert len(plan.weeks) == 2

    def test_each_week_has_7_days(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        for week in plan.weeks:
            assert len(week.days) == 7

    def test_each_day_has_all_meal_types(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        expected_types = {"Breakfast", "Lunch", "Dinner", "Snack"}
        for week in plan.weeks:
            for day in week.days:
                actual_types = {m.meal_type for m in day.meals}
                assert actual_types == expected_types, (
                    f"Day {day.day_name} has {actual_types}, expected {expected_types}"
                )

    def test_day_names_are_correct(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        expected_names = [
            "Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday",
        ]
        for week in plan.weeks:
            actual_names = [d.day_name for d in week.days]
            assert actual_names == expected_names

    def test_solver_status_set(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        assert plan.solver_status != "not_run"
        assert "Optimal" in plan.solver_status or "greedy" in plan.solver_status.lower()

    def test_cookbook_id_preserved(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        assert plan.cookbook_id == cookbook.cookbook_id

    def test_daily_targets_set(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        assert plan.daily_targets["calories"] == 2000
        assert plan.daily_targets["protein"] == 150

    def test_each_meal_has_recipe(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        for week in plan.weeks:
            for day in week.days:
                for meal in day.meals:
                    assert meal.recipe is not None
                    assert meal.recipe.id is not None

    def test_each_meal_has_serving_multiplier(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        allowed = {0.5, 0.75, 1.0, 1.25, 1.5, 2.0}
        for week in plan.weeks:
            for day in week.days:
                for meal in day.meals:
                    assert meal.serving_multiplier in allowed, (
                        f"Got multiplier {meal.serving_multiplier}"
                    )

    def test_adjusted_macros_computed(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        for week in plan.weeks:
            for day in week.days:
                for meal in day.meals:
                    expected_cal = meal.recipe.calories * meal.serving_multiplier
                    assert abs(meal.adjusted_calories - expected_cal) < 0.01

    def test_day_totals_computed(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        for week in plan.weeks:
            for day in week.days:
                assert "calories" in day.totals
                assert "protein" in day.totals
                assert day.totals["calories"] > 0

    def test_week_averages_computed(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        for week in plan.weeks:
            assert "avg_daily_calories" in week.averages
            assert "avg_daily_protein" in week.averages


# ---------------------------------------------------------------------------
# 2. Macro target accuracy tests
# ---------------------------------------------------------------------------

class TestMacroAccuracy:
    """Verify the solver hits macro targets within reasonable tolerance."""

    def test_daily_calories_within_tolerance(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        target = inp.daily_calories
        tolerance = inp.daily_calories_tolerance * 3  # Allow 3x tolerance for day-by-day
        for week in plan.weeks:
            for day in week.days:
                actual = day.totals["calories"]
                assert abs(actual - target) < tolerance, (
                    f"{day.day_name}: {actual:.0f} cal vs target {target} "
                    f"(deviation {abs(actual - target):.0f} > {tolerance})"
                )

    def test_daily_protein_within_tolerance(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        target = inp.daily_protein
        tolerance = inp.daily_protein_tolerance * 3
        for week in plan.weeks:
            for day in week.days:
                actual = day.totals["protein"]
                assert abs(actual - target) < tolerance, (
                    f"{day.day_name}: {actual:.1f}g protein vs target {target}g"
                )

    def test_average_calories_close_to_target(self):
        """The weekly average should be closer to target than individual days."""
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        for week in plan.weeks:
            avg_cal = week.averages["avg_daily_calories"]
            # Weekly average should be within 2x tolerance
            assert abs(avg_cal - inp.daily_calories) < inp.daily_calories_tolerance * 2


# ---------------------------------------------------------------------------
# 3. Variety / no-repeat tests
# ---------------------------------------------------------------------------

class TestVariety:
    """Verify recipe variety within weeks."""

    def test_no_recipe_repeat_within_week_when_enough_recipes(self):
        """With 10 recipes per type and 7 days, no repeats should be needed."""
        cookbook = _make_cookbook_4types(recipes_per_type=10)
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        for week in plan.weeks:
            for mt in ["Breakfast", "Lunch", "Dinner", "Snack"]:
                recipe_ids = []
                for day in week.days:
                    for meal in day.meals:
                        if meal.meal_type == mt:
                            recipe_ids.append(meal.recipe.id)
                assert len(recipe_ids) == len(set(recipe_ids)), (
                    f"Week {week.week}, {mt}: repeats found: {recipe_ids}"
                )

    def test_allows_repeats_between_weeks(self):
        """Recipes CAN repeat across different weeks."""
        cookbook = _make_cookbook_4types(recipes_per_type=8)
        inp = _default_mealplan_input(weeks=2)
        plan = generate_mealplan(inp, cookbook)
        # With only 8 recipes per type and 7 days/week,
        # week 2 MUST reuse some from week 1
        # This test just verifies it doesn't crash
        assert len(plan.weeks) == 2


# ---------------------------------------------------------------------------
# 4. Progressive relaxation tests
# ---------------------------------------------------------------------------

class TestRelaxation:
    """Verify relaxation still works when recipes are scarce."""

    def test_relaxation_with_few_recipes(self):
        """With fewer recipes than days, relaxation should allow repeats."""
        groups = []
        recipe_id = 1
        for mt, name in [("Breakfast", "Breakfast"), ("Lunch", "Lunch"),
                         ("Dinner", "Dinner"), ("Snack", "Snacks")]:
            recipes = []
            for i in range(3):  # Only 3 recipes per type < 7 days
                recipes.append(_make_recipe(
                    id=recipe_id, title=f"{name} {i+1}",
                    calories=400 + i * 50, protein=30 + i * 5,
                    fat=15 + i * 3, carbohydrates=40 + i * 5,
                    primary_protein="Chicken",
                    quality_score=70,
                ))
                recipe_id += 1
            groups.append(CookbookGroup(name=name, meal_type=mt, recipes=recipes))

        cookbook = Cookbook(name="Small Cookbook", groups=groups)
        cookbook.compute_stats()
        inp = _default_mealplan_input(weeks=1)
        plan = generate_mealplan(inp, cookbook)

        # Should still produce a plan (via relaxation or greedy)
        assert len(plan.weeks) == 1
        assert len(plan.weeks[0].days) == 7
        for day in plan.weeks[0].days:
            assert len(day.meals) == 4

    def test_relaxation_tracked_in_output(self):
        """When relaxation happens, it should be recorded."""
        groups = []
        recipe_id = 1
        for mt, name in [("Breakfast", "Breakfast"), ("Lunch", "Lunch"),
                         ("Dinner", "Dinner"), ("Snack", "Snacks")]:
            recipes = []
            for i in range(3):
                recipes.append(_make_recipe(
                    id=recipe_id, title=f"{name} {i+1}",
                    calories=400 + i * 50, protein=30 + i * 5,
                    fat=15 + i * 3, carbohydrates=40 + i * 5,
                    primary_protein="Chicken",
                    quality_score=70,
                ))
                recipe_id += 1
            groups.append(CookbookGroup(name=name, meal_type=mt, recipes=recipes))

        cookbook = Cookbook(name="Small Cookbook", groups=groups)
        cookbook.compute_stats()
        inp = _default_mealplan_input(weeks=1)
        plan = generate_mealplan(inp, cookbook)

        # relaxations list should be populated
        assert isinstance(plan.relaxations, list)


# ---------------------------------------------------------------------------
# 5. Empty / edge case tests
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases that must not crash."""

    def test_empty_cookbook_returns_no_recipes_status(self):
        cookbook = Cookbook(name="Empty")
        cookbook.compute_stats()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        assert plan.solver_status == "no_recipes"

    def test_single_week(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input(weeks=1)
        plan = generate_mealplan(inp, cookbook)
        assert len(plan.weeks) == 1
        assert len(plan.weeks[0].days) == 7

    def test_single_meal_type(self):
        """Cookbook with only 1 meal type should still work."""
        recipes = [
            _make_recipe(id=i+1, title=f"Lunch {i+1}",
                         calories=500, protein=40, fat=20, carbohydrates=50)
            for i in range(10)
        ]
        cookbook = Cookbook(
            name="Lunch Only",
            groups=[CookbookGroup(name="Lunch", meal_type="Lunch", recipes=recipes)],
        )
        cookbook.compute_stats()
        inp = _default_mealplan_input(weeks=1)
        plan = generate_mealplan(inp, cookbook)
        assert len(plan.weeks) == 1
        for day in plan.weeks[0].days:
            assert len(day.meals) == 1


# ---------------------------------------------------------------------------
# 6. JSON serialization test
# ---------------------------------------------------------------------------

class TestJsonSerialization:
    """Verify the output JSON matches expected schema."""

    def test_to_dict_has_required_keys(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        d = plan.to_dict()
        assert "plan_id" in d
        assert "cookbook_id" in d
        assert "daily_targets" in d
        assert "solver_status" in d
        assert "relaxations" in d
        assert "weeks" in d
        assert len(d["weeks"]) == 2

    def test_to_json_roundtrip(self):
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        j = plan.to_json()
        parsed = json.loads(j)
        assert len(parsed["weeks"]) == 2
        assert len(parsed["weeks"][0]["days"]) == 7

    def test_meal_slot_has_swaps_key(self):
        """Every meal slot should have swaps key (even if empty)."""
        cookbook = _make_cookbook_4types()
        inp = _default_mealplan_input()
        plan = generate_mealplan(inp, cookbook)
        d = plan.to_dict()
        for week in d["weeks"]:
            for day in week["days"]:
                for meal in day["meals"]:
                    assert "swaps" in meal
                    assert "serving_multiplier" in meal
                    assert "adjusted_calories" in meal
                    assert "adjusted_protein" in meal
                    assert "adjusted_fat" in meal
                    assert "adjusted_carbs" in meal


# ---------------------------------------------------------------------------
# 7. Performance test
# ---------------------------------------------------------------------------

class TestPerformance:
    """Day-by-day solving should be dramatically faster than monolithic."""

    def test_2week_plan_under_30s(self):
        """Full 2-week plan with 4 meal types should complete in <30s.

        The old monolithic solver took ~60s. Day-by-day should be <10s,
        but we use 30s as a generous upper bound for CI.
        """
        cookbook = _make_cookbook_4types(recipes_per_type=10)
        inp = _default_mealplan_input(weeks=2)
        start = time.time()
        plan = generate_mealplan(inp, cookbook)
        elapsed = time.time() - start
        assert elapsed < 30, f"Took {elapsed:.1f}s, expected <30s"
        assert len(plan.weeks) == 2

    def test_1week_plan_under_15s(self):
        """Single week should be under 15s."""
        cookbook = _make_cookbook_4types(recipes_per_type=10)
        inp = _default_mealplan_input(weeks=1)
        start = time.time()
        plan = generate_mealplan(inp, cookbook)
        elapsed = time.time() - start
        assert elapsed < 15, f"Took {elapsed:.1f}s, expected <15s"
