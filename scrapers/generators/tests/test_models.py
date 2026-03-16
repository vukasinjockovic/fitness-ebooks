"""Tests for models.py - data model parsing and serialization."""

import sys
import os
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import (
    CookbookInput, GroupInput, GlobalConstraints, MealPlanConstraints,
    Recipe, CookbookGroup, Cookbook, CookbookStats,
    MealPlanInput, MealSlot, DayPlan, WeekPlan, MealPlan,
)


# ---------------------------------------------------------------------------
# CookbookInput parsing
# ---------------------------------------------------------------------------

class TestCookbookInputParsing:
    def test_parse_minimal(self):
        data = {
            "name": "Test",
            "groups": [
                {"name": "B", "meal_type": "Breakfast", "count": 5,
                 "calorie_range": [300, 500], "protein_min": 20}
            ],
        }
        inp = CookbookInput.from_dict(data)
        assert inp.name == "Test"
        assert len(inp.groups) == 1
        assert inp.groups[0].calorie_range == (300, 500)
        assert inp.mealplan is None

    def test_parse_full_with_mealplan(self):
        data = {
            "name": "Full",
            "groups": [
                {"name": "Lunch", "meal_type": "Lunch", "count": 10,
                 "calorie_range": [400, 700], "protein_min": 30}
            ],
            "global_constraints": {
                "dietary": ["Keto"],
                "excluded_ingredients": ["nuts"],
                "preferred_cuisines": ["Italian"],
                "max_prep_time": 45,
                "min_quality_score": 60,
                "require_image": True,
                "protein_variety": True,
            },
            "mealplan": {
                "weeks": 4,
                "daily_calories": 2200,
                "daily_protein": 180,
            },
        }
        inp = CookbookInput.from_dict(data)
        assert inp.global_constraints.dietary == ["Keto"]
        assert inp.global_constraints.excluded_ingredients == ["nuts"]
        assert inp.global_constraints.require_image is True
        assert inp.mealplan is not None
        assert inp.mealplan.weeks == 4
        assert inp.mealplan.daily_calories == 2200

    def test_parse_defaults(self):
        data = {"name": "Defaults", "groups": [
            {"name": "D", "meal_type": "Dinner", "count": 3,
             "calorie_range": [400, 600], "protein_min": 25}
        ]}
        inp = CookbookInput.from_dict(data)
        assert inp.global_constraints.max_prep_time == 60
        assert inp.global_constraints.min_quality_score == 50
        assert inp.global_constraints.require_image is False


# ---------------------------------------------------------------------------
# Recipe model
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


class TestRecipe:
    def test_to_dict(self):
        r = _make_recipe()
        d = r.to_dict()
        assert d["recipe_id"] == 1
        assert d["calories"] == 500.0
        assert d["protein"] == 30.0
        assert d["primary_protein"] == "Chicken"
        assert "ingredients" in d
        assert "method" in d


# ---------------------------------------------------------------------------
# Cookbook
# ---------------------------------------------------------------------------

class TestCookbook:
    def test_compute_stats(self):
        r1 = _make_recipe(id=1, calories=400, protein=30, fat=15,
                          carbohydrates=40, normalized_cuisines=["Italian"],
                          primary_protein="Chicken")
        r2 = _make_recipe(id=2, calories=600, protein=40, fat=25,
                          carbohydrates=60, normalized_cuisines=["Mexican"],
                          primary_protein="Beef")

        cb = Cookbook(name="Test")
        cb.groups = [
            CookbookGroup(name="Lunch", meal_type="Lunch", recipes=[r1, r2])
        ]
        cb.compute_stats()

        assert cb.stats.total_recipes == 2
        assert cb.stats.avg_calories == 500.0
        assert cb.stats.avg_protein == 35.0
        assert cb.stats.cuisines_represented == 2
        assert cb.stats.proteins_represented == 2

    def test_to_json(self):
        r = _make_recipe()
        cb = Cookbook(name="JSON Test")
        cb.groups = [CookbookGroup(name="G", meal_type="Lunch", recipes=[r])]
        cb.compute_stats()

        j = cb.to_json()
        data = json.loads(j)
        assert data["name"] == "JSON Test"
        assert data["stats"]["total_recipes"] == 1
        assert len(data["groups"]) == 1
        assert len(data["groups"][0]["recipes"]) == 1


# ---------------------------------------------------------------------------
# DayPlan / WeekPlan
# ---------------------------------------------------------------------------

class TestDayPlan:
    def test_compute_totals(self):
        r1 = _make_recipe(calories=400, protein=30, fat=15, carbohydrates=40)
        r2 = _make_recipe(calories=500, protein=35, fat=20, carbohydrates=50)

        day = DayPlan(day=1, day_name="Monday", meals=[
            MealSlot(meal_type="Breakfast", recipe=r1),
            MealSlot(meal_type="Lunch", recipe=r2),
        ])
        day.compute_totals()

        assert day.totals["calories"] == 900.0
        assert day.totals["protein"] == 65.0
        assert day.totals["fat"] == 35.0
        assert day.totals["carbohydrates"] == 90.0


class TestWeekPlan:
    def test_compute_averages(self):
        day1 = DayPlan(day=1, day_name="Mon", totals={
            "calories": 2000, "protein": 150, "fat": 70, "carbohydrates": 200
        })
        day2 = DayPlan(day=2, day_name="Tue", totals={
            "calories": 2200, "protein": 170, "fat": 80, "carbohydrates": 220
        })
        week = WeekPlan(week=1, days=[day1, day2])
        week.compute_averages()

        assert week.averages["avg_daily_calories"] == 2100.0
        assert week.averages["avg_daily_protein"] == 160.0


# ---------------------------------------------------------------------------
# MealPlanInput
# ---------------------------------------------------------------------------

class TestMealPlanInput:
    def test_from_mealplan_constraints(self):
        mp = MealPlanConstraints(weeks=4, daily_calories=2200, daily_protein=180)
        inp = MealPlanInput.from_mealplan_constraints(mp)
        assert inp.weeks == 4
        assert inp.daily_calories == 2200
        assert inp.daily_protein == 180


# ---------------------------------------------------------------------------
# MealPlan serialization
# ---------------------------------------------------------------------------

class TestMealPlan:
    def test_to_json(self):
        r = _make_recipe()
        day = DayPlan(day=1, day_name="Monday", meals=[
            MealSlot(meal_type="Lunch", recipe=r)
        ])
        day.compute_totals()
        week = WeekPlan(week=1, days=[day])
        week.compute_averages()

        plan = MealPlan(
            cookbook_id="abc",
            daily_targets={"calories": 2000, "protein": 150},
            weeks=[week],
        )
        j = plan.to_json()
        data = json.loads(j)
        assert data["cookbook_id"] == "abc"
        assert len(data["weeks"]) == 1
        assert len(data["weeks"][0]["days"]) == 1
