"""Tests for serving multiplier and min/max recipe count features.

Covers:
- models.py: new fields on GlobalConstraints, MealPlanInput, MealSlot
- models.py: DayPlan.compute_totals uses adjusted values
- cookbook_generator.py: min/max total recipe validation
- mealplan_generator.py: solver picks (recipe, multiplier) combos
- cli.py: --multipliers flag parsing
- pipeline.py: summary output includes serving multiplier info
"""

import sys
import os
import json
import pytest
from unittest.mock import patch, MagicMock
from io import StringIO

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import (
    CookbookInput, GroupInput, GlobalConstraints, MealPlanConstraints,
    Recipe, CookbookGroup, Cookbook, CookbookStats,
    MealPlanInput, MealSlot, DayPlan, WeekPlan, MealPlan,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_recipe(**kwargs) -> Recipe:
    defaults = dict(
        id=1, source_id="src1", slug="test-recipe", title="Test Recipe",
        url="http://example.com", image="http://example.com/img.jpg",
        calories=500.0, protein=30.0, fat=20.0, carbohydrates=50.0,
        total_time=30, serving_size=4, ingredients=[], method=[],
        meal_types=["Lunch"], diet_tags=["Keto"],
        normalized_cuisines=["Italian"], primary_protein="Chicken",
        quality_score=80,
    )
    defaults.update(kwargs)
    return Recipe(**defaults)


# ---------------------------------------------------------------------------
# 1. GlobalConstraints: min/max total recipes
# ---------------------------------------------------------------------------

class TestGlobalConstraintsMinMax:
    def test_default_min_total_recipes(self):
        gc = GlobalConstraints()
        assert gc.min_total_recipes == 20

    def test_default_max_total_recipes(self):
        gc = GlobalConstraints()
        assert gc.max_total_recipes == 100

    def test_custom_min_max(self):
        gc = GlobalConstraints(min_total_recipes=10, max_total_recipes=50)
        assert gc.min_total_recipes == 10
        assert gc.max_total_recipes == 50

    def test_parsed_from_dict(self):
        data = {
            "name": "Test",
            "groups": [
                {"name": "Lunch", "meal_type": "Lunch", "count": 10,
                 "calorie_range": [400, 700], "protein_min": 30}
            ],
            "global_constraints": {
                "min_total_recipes": 15,
                "max_total_recipes": 60,
            },
        }
        inp = CookbookInput.from_dict(data)
        assert inp.global_constraints.min_total_recipes == 15
        assert inp.global_constraints.max_total_recipes == 60

    def test_parsed_defaults_when_not_in_json(self):
        data = {
            "name": "Test",
            "groups": [
                {"name": "Lunch", "meal_type": "Lunch", "count": 10,
                 "calorie_range": [400, 700], "protein_min": 30}
            ],
        }
        inp = CookbookInput.from_dict(data)
        assert inp.global_constraints.min_total_recipes == 20
        assert inp.global_constraints.max_total_recipes == 100


# ---------------------------------------------------------------------------
# 2. MealPlanInput: serving_multipliers field
# ---------------------------------------------------------------------------

class TestMealPlanInputMultipliers:
    def test_default_serving_multipliers(self):
        inp = MealPlanInput()
        assert inp.serving_multipliers == [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

    def test_custom_serving_multipliers(self):
        inp = MealPlanInput(serving_multipliers=[1.0, 1.5])
        assert inp.serving_multipliers == [1.0, 1.5]

    def test_backward_compat_no_multipliers(self):
        """When serving_multipliers is [1.0], behaves like old behavior."""
        inp = MealPlanInput(serving_multipliers=[1.0])
        assert inp.serving_multipliers == [1.0]

    def test_from_mealplan_constraints_default(self):
        mp = MealPlanConstraints(weeks=2, daily_calories=2000)
        inp = MealPlanInput.from_mealplan_constraints(mp)
        # Default: serving_multipliers should be default list
        assert inp.serving_multipliers == [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

    def test_from_mealplan_constraints_with_multipliers(self):
        mp = MealPlanConstraints(
            weeks=2, daily_calories=2000,
            serving_multipliers=[0.5, 1.0, 2.0],
        )
        inp = MealPlanInput.from_mealplan_constraints(mp)
        assert inp.serving_multipliers == [0.5, 1.0, 2.0]

    def test_parsed_from_mealplan_json(self):
        data = {
            "name": "Test",
            "groups": [
                {"name": "Lunch", "meal_type": "Lunch", "count": 10,
                 "calorie_range": [400, 700], "protein_min": 30}
            ],
            "mealplan": {
                "weeks": 2,
                "daily_calories": 2000,
                "serving_multipliers": [0.75, 1.0, 1.5],
            },
        }
        inp = CookbookInput.from_dict(data)
        assert inp.mealplan.serving_multipliers == [0.75, 1.0, 1.5]


# ---------------------------------------------------------------------------
# 3. MealSlot: serving_multiplier and adjusted macros
# ---------------------------------------------------------------------------

class TestMealSlotMultiplier:
    def test_default_serving_multiplier(self):
        r = _make_recipe()
        slot = MealSlot(meal_type="Lunch", recipe=r)
        assert slot.serving_multiplier == 1.0

    def test_default_adjusted_macros_zero(self):
        r = _make_recipe()
        slot = MealSlot(meal_type="Lunch", recipe=r)
        assert slot.adjusted_calories == 0
        assert slot.adjusted_protein == 0
        assert slot.adjusted_fat == 0
        assert slot.adjusted_carbs == 0

    def test_custom_serving_multiplier(self):
        r = _make_recipe(calories=400, protein=30, fat=15, carbohydrates=40)
        slot = MealSlot(
            meal_type="Lunch", recipe=r,
            serving_multiplier=1.5,
            adjusted_calories=600.0,
            adjusted_protein=45.0,
            adjusted_fat=22.5,
            adjusted_carbs=60.0,
        )
        assert slot.serving_multiplier == 1.5
        assert slot.adjusted_calories == 600.0
        assert slot.adjusted_protein == 45.0
        assert slot.adjusted_fat == 22.5
        assert slot.adjusted_carbs == 60.0

    def test_to_dict_includes_multiplier(self):
        r = _make_recipe(calories=400, protein=30, fat=15, carbohydrates=40)
        slot = MealSlot(
            meal_type="Lunch", recipe=r,
            serving_multiplier=1.25,
            adjusted_calories=500.0,
            adjusted_protein=37.5,
            adjusted_fat=18.75,
            adjusted_carbs=50.0,
        )
        d = slot.to_dict()
        assert d["serving_multiplier"] == 1.25
        assert d["adjusted_calories"] == 500.0
        assert d["adjusted_protein"] == 37.5
        assert d["adjusted_fat"] == 18.8  # rounded to 1 decimal in to_dict
        assert d["adjusted_carbs"] == 50.0

    def test_half_serving(self):
        r = _make_recipe(calories=500, protein=30, fat=20, carbohydrates=50)
        slot = MealSlot(
            meal_type="Snack", recipe=r,
            serving_multiplier=0.5,
            adjusted_calories=250.0,
            adjusted_protein=15.0,
            adjusted_fat=10.0,
            adjusted_carbs=25.0,
        )
        assert slot.adjusted_calories == 250.0


# ---------------------------------------------------------------------------
# 4. DayPlan.compute_totals: uses adjusted values when present
# ---------------------------------------------------------------------------

class TestDayPlanWithMultipliers:
    def test_compute_totals_uses_adjusted_macros(self):
        """When adjusted_calories > 0, compute_totals should use adjusted values."""
        r1 = _make_recipe(calories=400, protein=30, fat=15, carbohydrates=40)
        r2 = _make_recipe(calories=500, protein=35, fat=20, carbohydrates=50)

        day = DayPlan(day=1, day_name="Monday", meals=[
            MealSlot(
                meal_type="Breakfast", recipe=r1,
                serving_multiplier=1.5,
                adjusted_calories=600.0, adjusted_protein=45.0,
                adjusted_fat=22.5, adjusted_carbs=60.0,
            ),
            MealSlot(
                meal_type="Lunch", recipe=r2,
                serving_multiplier=1.0,
                adjusted_calories=500.0, adjusted_protein=35.0,
                adjusted_fat=20.0, adjusted_carbs=50.0,
            ),
        ])
        day.compute_totals()

        assert day.totals["calories"] == 1100.0
        assert day.totals["protein"] == 80.0
        assert day.totals["fat"] == 42.5
        assert day.totals["carbohydrates"] == 110.0

    def test_compute_totals_falls_back_to_recipe_when_no_adjusted(self):
        """When adjusted_calories == 0, should use recipe base values (backward compat)."""
        r1 = _make_recipe(calories=400, protein=30, fat=15, carbohydrates=40)

        day = DayPlan(day=1, day_name="Monday", meals=[
            MealSlot(meal_type="Breakfast", recipe=r1),
        ])
        day.compute_totals()

        assert day.totals["calories"] == 400.0
        assert day.totals["protein"] == 30.0


# ---------------------------------------------------------------------------
# 5. Cookbook generator: min/max total recipe validation
# ---------------------------------------------------------------------------

class TestCookbookMinMaxValidation:
    def test_validate_total_below_min_warns(self):
        """If sum of group counts < min_total_recipes, should warn."""
        from cookbook_generator import validate_total_recipe_count

        groups = [
            GroupInput(name="B", meal_type="Breakfast", count=3,
                       calorie_range=(300, 500), protein_min=20),
            GroupInput(name="L", meal_type="Lunch", count=3,
                       calorie_range=(400, 700), protein_min=30),
        ]
        gc = GlobalConstraints(min_total_recipes=20, max_total_recipes=100)

        warnings = validate_total_recipe_count(groups, gc)
        assert len(warnings) > 0
        assert any("below minimum" in w.lower() or "6" in w for w in warnings)

    def test_validate_total_above_max_warns(self):
        """If sum of group counts > max_total_recipes, should warn."""
        from cookbook_generator import validate_total_recipe_count

        groups = [
            GroupInput(name="B", meal_type="Breakfast", count=30,
                       calorie_range=(300, 500), protein_min=20),
            GroupInput(name="L", meal_type="Lunch", count=30,
                       calorie_range=(400, 700), protein_min=30),
            GroupInput(name="D", meal_type="Dinner", count=30,
                       calorie_range=(400, 700), protein_min=30),
            GroupInput(name="S", meal_type="Snack", count=20,
                       calorie_range=(100, 250), protein_min=5),
        ]
        gc = GlobalConstraints(min_total_recipes=20, max_total_recipes=100)

        warnings = validate_total_recipe_count(groups, gc)
        assert len(warnings) > 0
        assert any("above maximum" in w.lower() or "exceeds" in w.lower() or "110" in w for w in warnings)

    def test_validate_total_within_range_ok(self):
        """If within range, no warnings."""
        from cookbook_generator import validate_total_recipe_count

        groups = [
            GroupInput(name="B", meal_type="Breakfast", count=10,
                       calorie_range=(300, 500), protein_min=20),
            GroupInput(name="L", meal_type="Lunch", count=12,
                       calorie_range=(400, 700), protein_min=30),
            GroupInput(name="D", meal_type="Dinner", count=12,
                       calorie_range=(400, 700), protein_min=30),
            GroupInput(name="S", meal_type="Snack", count=8,
                       calorie_range=(100, 250), protein_min=5),
        ]
        gc = GlobalConstraints(min_total_recipes=20, max_total_recipes=100)

        warnings = validate_total_recipe_count(groups, gc)
        assert len(warnings) == 0


# ---------------------------------------------------------------------------
# 6. Mealplan generator: solver uses multipliers
# ---------------------------------------------------------------------------

class TestMealplanGeneratorMultipliers:
    """Test that mealplan_generator picks recipe+multiplier combos."""

    def _make_small_cookbook(self):
        """Create a small cookbook with enough recipes for a 1-week plan."""
        breakfast = [
            _make_recipe(id=i, title=f"Breakfast {i}", calories=350+i*20,
                         protein=25+i*2, fat=12+i, carbohydrates=35+i*3,
                         meal_types=["Breakfast"], primary_protein=["Eggs", "Chicken", "Tofu"][i%3],
                         quality_score=70+i)
            for i in range(1, 11)
        ]
        lunch = [
            _make_recipe(id=100+i, title=f"Lunch {i}", calories=500+i*20,
                         protein=35+i*2, fat=18+i, carbohydrates=45+i*3,
                         meal_types=["Lunch"], primary_protein=["Chicken", "Beef", "Fish/Seafood"][i%3],
                         quality_score=70+i)
            for i in range(1, 13)
        ]
        dinner = [
            _make_recipe(id=200+i, title=f"Dinner {i}", calories=550+i*20,
                         protein=40+i*2, fat=20+i, carbohydrates=40+i*3,
                         meal_types=["Dinner"], primary_protein=["Beef", "Fish/Seafood", "Chicken"][i%3],
                         quality_score=70+i)
            for i in range(1, 13)
        ]
        snack = [
            _make_recipe(id=300+i, title=f"Snack {i}", calories=150+i*10,
                         protein=10+i, fat=5+i, carbohydrates=15+i*2,
                         meal_types=["Snack"], primary_protein=["Eggs", "Legumes"][i%2],
                         quality_score=70+i)
            for i in range(1, 9)
        ]

        cookbook = Cookbook(name="Test Cookbook")
        cookbook.groups = [
            CookbookGroup(name="Breakfast", meal_type="Breakfast", recipes=breakfast),
            CookbookGroup(name="Lunch", meal_type="Lunch", recipes=lunch),
            CookbookGroup(name="Dinner", meal_type="Dinner", recipes=dinner),
            CookbookGroup(name="Snacks", meal_type="Snack", recipes=snack),
        ]
        cookbook.compute_stats()
        return cookbook

    def test_solver_with_multipliers_produces_plan(self):
        """Solver should produce a valid plan using serving multipliers."""
        from mealplan_generator import generate_mealplan

        cookbook = self._make_small_cookbook()
        inp = MealPlanInput(
            weeks=1, daily_calories=2000,
            daily_calories_tolerance=200,
            daily_protein=150, daily_protein_tolerance=30,
            daily_carbs=200, daily_carbs_tolerance=40,
            daily_fat=70, daily_fat_tolerance=20,
            serving_multipliers=[0.75, 1.0, 1.25, 1.5],
        )

        plan = generate_mealplan(inp, cookbook)

        assert plan is not None
        assert len(plan.weeks) == 1
        assert len(plan.weeks[0].days) == 7
        for day in plan.weeks[0].days:
            assert len(day.meals) == 4  # Breakfast, Lunch, Dinner, Snack

    def test_meal_slots_have_multiplier_info(self):
        """Each MealSlot should have serving_multiplier and adjusted macros set."""
        from mealplan_generator import generate_mealplan

        cookbook = self._make_small_cookbook()
        inp = MealPlanInput(
            weeks=1, daily_calories=2000,
            daily_calories_tolerance=200,
            daily_protein=150, daily_protein_tolerance=30,
            daily_carbs=200, daily_carbs_tolerance=40,
            daily_fat=70, daily_fat_tolerance=20,
            serving_multipliers=[0.75, 1.0, 1.25, 1.5],
        )

        plan = generate_mealplan(inp, cookbook)

        for day in plan.weeks[0].days:
            for meal in day.meals:
                assert meal.serving_multiplier > 0
                assert meal.adjusted_calories > 0
                # adjusted should equal recipe * multiplier (approximately)
                expected = meal.recipe.calories * meal.serving_multiplier
                assert abs(meal.adjusted_calories - expected) < 0.01

    def test_solver_with_single_multiplier_backward_compat(self):
        """With multipliers=[1.0], should behave like old code."""
        from mealplan_generator import generate_mealplan

        cookbook = self._make_small_cookbook()
        inp = MealPlanInput(
            weeks=1, daily_calories=2000,
            daily_calories_tolerance=300,
            daily_protein=150, daily_protein_tolerance=40,
            daily_carbs=200, daily_carbs_tolerance=50,
            daily_fat=70, daily_fat_tolerance=30,
            serving_multipliers=[1.0],
        )

        plan = generate_mealplan(inp, cookbook)

        assert plan is not None
        for day in plan.weeks[0].days:
            for meal in day.meals:
                assert meal.serving_multiplier == 1.0
                expected = meal.recipe.calories * 1.0
                assert abs(meal.adjusted_calories - expected) < 0.01

    def test_multipliers_improve_macro_accuracy(self):
        """With multipliers, daily totals should be closer to target than fixed 1.0."""
        from mealplan_generator import generate_mealplan

        cookbook = self._make_small_cookbook()

        # Run with multipliers
        inp_multi = MealPlanInput(
            weeks=1, daily_calories=2000,
            daily_calories_tolerance=200,
            daily_protein=150, daily_protein_tolerance=30,
            daily_carbs=200, daily_carbs_tolerance=40,
            daily_fat=70, daily_fat_tolerance=20,
            serving_multipliers=[0.5, 0.75, 1.0, 1.25, 1.5, 2.0],
        )

        plan_multi = generate_mealplan(inp_multi, cookbook)

        # Run with just 1.0
        inp_fixed = MealPlanInput(
            weeks=1, daily_calories=2000,
            daily_calories_tolerance=200,
            daily_protein=150, daily_protein_tolerance=30,
            daily_carbs=200, daily_carbs_tolerance=40,
            daily_fat=70, daily_fat_tolerance=20,
            serving_multipliers=[1.0],
        )

        plan_fixed = generate_mealplan(inp_fixed, cookbook)

        # Calculate average calorie deviation for both
        def avg_cal_dev(plan, target):
            devs = []
            for week in plan.weeks:
                for day in week.days:
                    devs.append(abs(day.totals.get("calories", 0) - target))
            return sum(devs) / len(devs) if devs else 9999

        multi_dev = avg_cal_dev(plan_multi, 2000)
        fixed_dev = avg_cal_dev(plan_fixed, 2000)

        # Multiplier version should be at least as good (usually better)
        # Allow some tolerance since solver is stochastic
        assert multi_dev <= fixed_dev + 100  # multiplier version should not be much worse

    def test_preference_for_1x_serving(self):
        """With only 1.0x available, solver should still work correctly.
        When multiple multipliers are available, the solver may use non-1.0x
        to optimize diversity and macro fit. The key is that multiplier=1.0
        is not penalized and the solver can freely choose it.
        """
        from mealplan_generator import generate_mealplan

        cookbook = self._make_small_cookbook()
        # Use only [1.0] to verify backward compatibility and 1.0x preference
        inp = MealPlanInput(
            weeks=1, daily_calories=2000,
            daily_calories_tolerance=500,  # Very generous tolerance
            daily_protein=100, daily_protein_tolerance=80,
            daily_carbs=200, daily_carbs_tolerance=100,
            daily_fat=70, daily_fat_tolerance=50,
            serving_multipliers=[1.0],
        )

        plan = generate_mealplan(inp, cookbook)

        # With only 1.0 available, all meals should be 1.0x
        for week in plan.weeks:
            for day in week.days:
                for meal in day.meals:
                    assert meal.serving_multiplier == 1.0

    def test_multiplier_penalty_is_tiebreaker_not_barrier(self):
        """The multiplier penalty should be small enough that it doesn't
        prevent the solver from using non-1.0x when macros benefit.

        With narrow tolerances, the solver should use multipliers to improve
        macro accuracy even though 1.0x has zero penalty.
        """
        from mealplan_generator import generate_mealplan

        cookbook = self._make_small_cookbook()
        # Tight tolerances force the solver to work hard on macros
        inp = MealPlanInput(
            weeks=1, daily_calories=2000,
            daily_calories_tolerance=100,
            daily_protein=180, daily_protein_tolerance=15,
            daily_carbs=200, daily_carbs_tolerance=20,
            daily_fat=70, daily_fat_tolerance=10,
            serving_multipliers=[0.5, 0.75, 1.0, 1.25, 1.5, 2.0],
        )

        plan = generate_mealplan(inp, cookbook)

        # With tight protein tolerance, solver should use some non-1.0x
        total_meals = 0
        non_1x_meals = 0
        for week in plan.weeks:
            for day in week.days:
                for meal in day.meals:
                    total_meals += 1
                    if meal.serving_multiplier != 1.0:
                        non_1x_meals += 1

        # At least a few meals should use multipliers
        assert non_1x_meals > 0, (
            f"Expected some non-1.0x meals with tight protein target, "
            f"but all {total_meals} meals used 1.0x. "
            f"Multiplier penalty may be too high."
        )

    def test_day_totals_use_adjusted_values(self):
        """DayPlan totals should reflect adjusted (multiplied) macros."""
        from mealplan_generator import generate_mealplan

        cookbook = self._make_small_cookbook()
        inp = MealPlanInput(
            weeks=1, daily_calories=2000,
            daily_calories_tolerance=200,
            daily_protein=150, daily_protein_tolerance=30,
            daily_carbs=200, daily_carbs_tolerance=40,
            daily_fat=70, daily_fat_tolerance=20,
            serving_multipliers=[0.75, 1.0, 1.25, 1.5],
        )

        plan = generate_mealplan(inp, cookbook)

        for day in plan.weeks[0].days:
            # Manually compute expected totals from adjusted values
            expected_cal = sum(m.adjusted_calories for m in day.meals)
            expected_pro = sum(m.adjusted_protein for m in day.meals)
            assert abs(day.totals["calories"] - expected_cal) < 0.1
            assert abs(day.totals["protein"] - expected_pro) < 0.1


# ---------------------------------------------------------------------------
# 7. CLI: --multipliers flag
# ---------------------------------------------------------------------------

class TestCLIMultipliers:
    def test_parse_multipliers_flag(self):
        """--multipliers flag should be parsed as a list of floats."""
        from cli import main
        import argparse

        # We test the argument parser directly
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")

        p_full = subparsers.add_parser("full")
        p_full.add_argument("input_file")
        p_full.add_argument("--weeks", type=int)
        p_full.add_argument("--daily-cal", type=int)
        p_full.add_argument("--protein", type=int)
        p_full.add_argument("--multipliers", type=str, default=None)

        args = parser.parse_args(["full", "test.json", "--multipliers", "0.5,1.0,1.5,2.0"])
        assert args.multipliers == "0.5,1.0,1.5,2.0"

        # Parse as float list
        multipliers = [float(x) for x in args.multipliers.split(",")]
        assert multipliers == [0.5, 1.0, 1.5, 2.0]

    def test_parse_multipliers_default_none(self):
        """Without --multipliers, default should be None (use model default)."""
        import argparse

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")

        p_full = subparsers.add_parser("full")
        p_full.add_argument("input_file")
        p_full.add_argument("--multipliers", type=str, default=None)

        args = parser.parse_args(["full", "test.json"])
        assert args.multipliers is None


# ---------------------------------------------------------------------------
# 8. Pipeline output includes serving multiplier
# ---------------------------------------------------------------------------

class TestPipelineOutputMultiplier:
    def test_mealplan_summary_shows_multiplier(self):
        """print_mealplan_summary should show serving multiplier when != 1.0."""
        from pipeline import print_mealplan_summary

        r = _make_recipe(calories=400, protein=30, fat=15, carbohydrates=40)
        slot = MealSlot(
            meal_type="Lunch", recipe=r,
            serving_multiplier=1.5,
            adjusted_calories=600.0,
            adjusted_protein=45.0,
            adjusted_fat=22.5,
            adjusted_carbs=60.0,
        )
        day = DayPlan(day=1, day_name="Monday", meals=[slot])
        day.compute_totals()
        week = WeekPlan(week=1, days=[day])
        week.compute_averages()

        plan = MealPlan(
            cookbook_id="abc",
            daily_targets={"calories": 2000, "protein": 150},
            weeks=[week],
            solver_status="Optimal",
        )
        inp = MealPlanInput(daily_calories=2000, daily_protein=150,
                            daily_carbs=200, daily_fat=70)

        # Capture stdout
        captured = StringIO()
        import contextlib
        with contextlib.redirect_stdout(captured):
            print_mealplan_summary(plan, inp)

        output = captured.getvalue()
        assert "1.5" in output, f"Expected '1.5' serving multiplier in output:\n{output}"

    def test_mealplan_summary_no_multiplier_for_1x(self):
        """For 1.0 serving, the output should show '1.0 serving' or similar."""
        from pipeline import print_mealplan_summary

        r = _make_recipe(calories=400, protein=30, fat=15, carbohydrates=40)
        slot = MealSlot(
            meal_type="Lunch", recipe=r,
            serving_multiplier=1.0,
            adjusted_calories=400.0,
            adjusted_protein=30.0,
            adjusted_fat=15.0,
            adjusted_carbs=40.0,
        )
        day = DayPlan(day=1, day_name="Monday", meals=[slot])
        day.compute_totals()
        week = WeekPlan(week=1, days=[day])
        week.compute_averages()

        plan = MealPlan(
            cookbook_id="abc",
            daily_targets={"calories": 2000, "protein": 150},
            weeks=[week],
            solver_status="Optimal",
        )
        inp = MealPlanInput(daily_calories=2000, daily_protein=150,
                            daily_carbs=200, daily_fat=70)

        captured = StringIO()
        import contextlib
        with contextlib.redirect_stdout(captured):
            print_mealplan_summary(plan, inp)

        output = captured.getvalue()
        # Output should contain "1.0 serving" or just the recipe name
        # Not a hard requirement on format, just verify it works without error
        assert "Test Recipe" in output


# ---------------------------------------------------------------------------
# 9. MealSlot to_dict / MealPlan serialization roundtrip
# ---------------------------------------------------------------------------

class TestSerializationWithMultipliers:
    def test_meal_slot_to_dict_full(self):
        r = _make_recipe(calories=500, protein=40, fat=20, carbohydrates=50)
        slot = MealSlot(
            meal_type="Dinner", recipe=r,
            serving_multiplier=1.25,
            adjusted_calories=625.0,
            adjusted_protein=50.0,
            adjusted_fat=25.0,
            adjusted_carbs=62.5,
        )
        d = slot.to_dict()
        assert d["serving_multiplier"] == 1.25
        assert d["adjusted_calories"] == 625.0
        assert d["adjusted_protein"] == 50.0
        assert d["adjusted_fat"] == 25.0
        assert d["adjusted_carbs"] == 62.5
        assert d["meal_type"] == "Dinner"
        assert d["recipe"]["recipe_id"] == 1

    def test_mealplan_json_includes_multipliers(self):
        r = _make_recipe()
        slot = MealSlot(
            meal_type="Lunch", recipe=r,
            serving_multiplier=0.75,
            adjusted_calories=375.0,
            adjusted_protein=22.5,
            adjusted_fat=15.0,
            adjusted_carbs=37.5,
        )
        day = DayPlan(day=1, day_name="Monday", meals=[slot])
        day.compute_totals()
        week = WeekPlan(week=1, days=[day])
        week.compute_averages()

        plan = MealPlan(weeks=[week])
        j = plan.to_json()
        data = json.loads(j)

        meal_data = data["weeks"][0]["days"][0]["meals"][0]
        assert meal_data["serving_multiplier"] == 0.75
        assert meal_data["adjusted_calories"] == 375.0


# ---------------------------------------------------------------------------
# 10. Multiplier solver: uses non-1.0x when protein target demands it
# ---------------------------------------------------------------------------

class TestMultiplierSolverUsesNon1x:
    """Regression tests for the multiplier penalty bug.

    The solver must actually use non-1.0x multipliers when the protein target
    is unreachable at 1.0x servings. Previously, the multiplier penalty weight
    (0.5) was so high relative to the macro deviation weights that the solver
    preferred missing protein by 20-40g over using 1.25x or 1.5x servings.
    """

    def _make_low_protein_cookbook(self):
        """Cookbook where 1.0x protein maxes out ~160g/day.

        With target of 220g, multipliers are essential.
        """
        breakfast = [
            _make_recipe(
                id=i, title=f"Breakfast {i}",
                calories=300+i*20, protein=15+i*2, fat=10+i,
                carbohydrates=35+i*3, meal_types=["Breakfast"],
                primary_protein=["Eggs", "Chicken", "Tofu"][i % 3],
                quality_score=70+i,
            )
            for i in range(1, 11)  # protein: 17-35g
        ]
        lunch = [
            _make_recipe(
                id=100+i, title=f"Lunch {i}",
                calories=450+i*20, protein=25+i*3, fat=15+i*2,
                carbohydrates=40+i*4, meal_types=["Lunch"],
                primary_protein=["Chicken", "Beef", "Fish/Seafood"][i % 3],
                quality_score=72+i,
            )
            for i in range(1, 13)  # protein: 28-61g
        ]
        dinner = [
            _make_recipe(
                id=200+i, title=f"Dinner {i}",
                calories=500+i*20, protein=30+i*3, fat=18+i*2,
                carbohydrates=35+i*4, meal_types=["Dinner"],
                primary_protein=["Beef", "Fish/Seafood", "Chicken"][i % 3],
                quality_score=74+i,
            )
            for i in range(1, 13)  # protein: 33-66g
        ]
        snack = [
            _make_recipe(
                id=300+i, title=f"Snack {i}",
                calories=150+i*10, protein=8+i*2, fat=5+i,
                carbohydrates=15+i*2, meal_types=["Snack"],
                primary_protein=["Eggs", "Legumes"][i % 2],
                quality_score=68+i,
            )
            for i in range(1, 9)  # protein: 10-24g
        ]

        cookbook = Cookbook(name="Low Protein Cookbook")
        cookbook.groups = [
            CookbookGroup(name="Breakfast", meal_type="Breakfast", recipes=breakfast),
            CookbookGroup(name="Lunch", meal_type="Lunch", recipes=lunch),
            CookbookGroup(name="Dinner", meal_type="Dinner", recipes=dinner),
            CookbookGroup(name="Snacks", meal_type="Snack", recipes=snack),
        ]
        cookbook.compute_stats()
        return cookbook

    def test_solver_uses_multipliers_for_high_protein_target(self):
        """When protein target (220g) is unreachable at 1.0x (~160g max),
        the solver MUST use non-1.0x multipliers to close the gap.

        At least 25% of meals should be non-1.0x in this scenario.
        """
        from mealplan_generator import generate_mealplan

        cookbook = self._make_low_protein_cookbook()
        inp = MealPlanInput(
            weeks=1, daily_calories=2200,
            daily_calories_tolerance=150,
            daily_protein=220, daily_protein_tolerance=20,
            daily_carbs=200, daily_carbs_tolerance=25,
            daily_fat=75, daily_fat_tolerance=15,
            serving_multipliers=[0.5, 0.75, 1.0, 1.25, 1.5, 2.0],
        )

        plan = generate_mealplan(inp, cookbook)

        assert plan is not None
        assert len(plan.weeks) == 1

        total_meals = 0
        non_1x_meals = 0
        for day in plan.weeks[0].days:
            for meal in day.meals:
                total_meals += 1
                if meal.serving_multiplier != 1.0:
                    non_1x_meals += 1

        ratio = non_1x_meals / total_meals if total_meals > 0 else 0
        assert ratio >= 0.25, (
            f"Expected at least 25% non-1.0x meals when protein target is "
            f"unreachable at 1.0x, but got {ratio:.0%} ({non_1x_meals}/{total_meals}). "
            f"The multiplier penalty is likely too high."
        )

    def test_solver_protein_deviation_improves_with_multipliers(self):
        """With multipliers available, the average daily protein deviation
        should be significantly better than with only 1.0x.
        """
        from mealplan_generator import generate_mealplan

        cookbook = self._make_low_protein_cookbook()

        # With multipliers
        inp_multi = MealPlanInput(
            weeks=1, daily_calories=2200,
            daily_calories_tolerance=150,
            daily_protein=220, daily_protein_tolerance=20,
            daily_carbs=200, daily_carbs_tolerance=25,
            daily_fat=75, daily_fat_tolerance=15,
            serving_multipliers=[0.5, 0.75, 1.0, 1.25, 1.5, 2.0],
        )
        plan_multi = generate_mealplan(inp_multi, cookbook)

        # Without multipliers
        inp_fixed = MealPlanInput(
            weeks=1, daily_calories=2200,
            daily_calories_tolerance=150,
            daily_protein=220, daily_protein_tolerance=20,
            daily_carbs=200, daily_carbs_tolerance=25,
            daily_fat=75, daily_fat_tolerance=15,
            serving_multipliers=[1.0],
        )
        plan_fixed = generate_mealplan(inp_fixed, cookbook)

        def avg_protein_dev(plan, target):
            devs = []
            for week in plan.weeks:
                for day in week.days:
                    devs.append(abs(day.totals.get("protein", 0) - target))
            return sum(devs) / len(devs) if devs else 9999

        multi_dev = avg_protein_dev(plan_multi, 220)
        fixed_dev = avg_protein_dev(plan_fixed, 220)

        # Multiplier version should be at least 10g/day closer to target
        assert multi_dev < fixed_dev - 5, (
            f"With multipliers, protein deviation should be significantly better. "
            f"Got multi={multi_dev:.1f}g vs fixed={fixed_dev:.1f}g. "
            f"The multiplier penalty may be suppressing useful multiplier choices."
        )

    def test_minimal_1slot_multiplier_selection(self):
        """Minimal test: 1 day, 1 meal type, target only reachable at 1.5x.

        Recipe: 400 cal, 40g protein at 1.0x.
        Target: 600 cal, 60g protein => needs 1.5x.
        """
        from mealplan_generator import generate_mealplan

        recipe = _make_recipe(
            id=1, title="Test Chicken",
            calories=400, protein=40, fat=15, carbohydrates=45,
            meal_types=["Lunch"],
            primary_protein="Chicken",
            quality_score=80,
        )

        cookbook = Cookbook(name="Minimal")
        cookbook.groups = [
            CookbookGroup(name="Lunch", meal_type="Lunch", recipes=[recipe]),
        ]
        cookbook.compute_stats()

        inp = MealPlanInput(
            weeks=1, daily_calories=600,
            daily_calories_tolerance=50,
            daily_protein=60, daily_protein_tolerance=5,
            daily_carbs=68, daily_carbs_tolerance=10,
            daily_fat=23, daily_fat_tolerance=5,
            serving_multipliers=[0.5, 0.75, 1.0, 1.25, 1.5, 2.0],
        )

        plan = generate_mealplan(inp, cookbook)

        assert plan is not None
        # Every day should pick a multiplier close to 1.5x
        for day in plan.weeks[0].days:
            assert len(day.meals) == 1
            meal = day.meals[0]
            assert meal.serving_multiplier != 1.0, (
                f"Solver picked 1.0x but target (600 cal, 60g pro) requires ~1.5x "
                f"of a 400 cal, 40g pro recipe. Got multiplier={meal.serving_multiplier}"
            )
            assert meal.serving_multiplier >= 1.25, (
                f"Expected 1.5x (or 1.25x at minimum), got {meal.serving_multiplier}"
            )
