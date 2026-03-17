"""Data models for cookbook and meal plan generation."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

@dataclass
class GroupInput:
    """Specification for one cookbook group (e.g., Breakfast, Lunch)."""
    name: str
    meal_type: str
    count: int
    calorie_range: tuple[int, int]
    protein_min: int


@dataclass
class GlobalConstraints:
    """Global constraints applied to all groups."""
    dietary: list[str] = field(default_factory=list)
    excluded_ingredients: list[str] = field(default_factory=list)
    preferred_cuisines: list[str] = field(default_factory=list)
    max_prep_time: int = 60
    min_quality_score: int = 50
    require_image: bool = False
    protein_variety: bool = True
    min_total_recipes: int = 20
    max_total_recipes: int = 100


_DEFAULT_MULTIPLIERS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]


@dataclass
class MealPlanConstraints:
    """Meal plan section from input JSON."""
    weeks: int = 2
    daily_calories: int = 2000
    daily_calories_tolerance: int = 150
    daily_protein: int = 150
    daily_protein_tolerance: int = 20
    daily_carbs: int = 200
    daily_carbs_tolerance: int = 25
    daily_fat: int = 70
    daily_fat_tolerance: int = 15
    meal_calorie_split: dict[str, float] = field(default_factory=dict)
    serving_multipliers: list[float] = field(default_factory=lambda: list(_DEFAULT_MULTIPLIERS))


@dataclass
class CookbookInput:
    """Full input for cookbook generation."""
    name: str
    groups: list[GroupInput]
    global_constraints: GlobalConstraints = field(default_factory=GlobalConstraints)
    mealplan: Optional[MealPlanConstraints] = None

    @classmethod
    def from_dict(cls, data: dict) -> CookbookInput:
        """Parse from JSON-loaded dict."""
        groups = [
            GroupInput(
                name=g["name"],
                meal_type=g["meal_type"],
                count=g["count"],
                calorie_range=tuple(g["calorie_range"]),
                protein_min=g["protein_min"],
            )
            for g in data["groups"]
        ]
        gc_data = data.get("global_constraints", {})
        gc = GlobalConstraints(
            dietary=gc_data.get("dietary", []),
            excluded_ingredients=gc_data.get("excluded_ingredients", []),
            preferred_cuisines=gc_data.get("preferred_cuisines", []),
            max_prep_time=gc_data.get("max_prep_time", 60),
            min_quality_score=gc_data.get("min_quality_score", 50),
            require_image=gc_data.get("require_image", False),
            protein_variety=gc_data.get("protein_variety", True),
            min_total_recipes=gc_data.get("min_total_recipes", 20),
            max_total_recipes=gc_data.get("max_total_recipes", 100),
        )
        mp = None
        if "mealplan" in data:
            mp_data = data["mealplan"]
            mp = MealPlanConstraints(
                weeks=mp_data.get("weeks", 2),
                daily_calories=mp_data.get("daily_calories", 2000),
                daily_calories_tolerance=mp_data.get("daily_calories_tolerance", 150),
                daily_protein=mp_data.get("daily_protein", 150),
                daily_protein_tolerance=mp_data.get("daily_protein_tolerance", 20),
                daily_carbs=mp_data.get("daily_carbs", 200),
                daily_carbs_tolerance=mp_data.get("daily_carbs_tolerance", 25),
                daily_fat=mp_data.get("daily_fat", 70),
                daily_fat_tolerance=mp_data.get("daily_fat_tolerance", 15),
                meal_calorie_split=mp_data.get("meal_calorie_split", {}),
                serving_multipliers=mp_data.get("serving_multipliers", list(_DEFAULT_MULTIPLIERS)),
            )
        return cls(
            name=data["name"],
            groups=groups,
            global_constraints=gc,
            mealplan=mp,
        )


# ---------------------------------------------------------------------------
# Recipe model
# ---------------------------------------------------------------------------

@dataclass
class Recipe:
    """A recipe loaded from lake.recipes or public.bp_cpts."""
    id: int | str  # int for lake, UUID str for production
    source_id: str
    slug: str
    title: str
    url: str
    image: str
    calories: float
    protein: float
    fat: float
    carbohydrates: float
    total_time: int
    serving_size: int
    ingredients: list[dict]
    method: list[dict]
    meal_types: list[str]
    diet_tags: list[str]
    normalized_cuisines: list[str]
    primary_protein: str
    quality_score: int
    swaps: list[Any] = field(default_factory=list)  # list[SwapRecipe]

    def to_dict(self) -> dict:
        return {
            "recipe_id": self.id,
            "title": self.title,
            "slug": self.slug,
            "image": self.image or "",
            "url": self.url or "",
            "calories": float(self.calories),
            "protein": float(self.protein),
            "fat": float(self.fat),
            "carbohydrates": float(self.carbohydrates),
            "total_time": self.total_time or 0,
            "serving_size": self.serving_size or 1,
            "primary_protein": self.primary_protein or "",
            "cuisines": self.normalized_cuisines or [],
            "meal_types": self.meal_types or [],
            "diet_tags": self.diet_tags or [],
            "quality_score": self.quality_score,
            "ingredients": self.ingredients,
            "method": self.method,
            "swaps": [s.to_dict() if hasattr(s, 'to_dict') else s for s in self.swaps],
        }


# ---------------------------------------------------------------------------
# Swap recipe model
# ---------------------------------------------------------------------------

@dataclass
class SwapRecipe:
    """A swap alternative for a recipe."""
    recipe_id: int | str  # int for lake, UUID str for production
    title: str
    slug: str
    image: str | None
    calories: float
    protein: float
    fat: float
    carbohydrates: float
    total_time: int | None
    primary_protein: str | None
    similarity_score: float  # 0 = identical, higher = more different

    def to_dict(self) -> dict:
        return {
            "recipe_id": self.recipe_id,
            "title": self.title,
            "slug": self.slug,
            "image": self.image or "",
            "calories": float(self.calories),
            "protein": float(self.protein),
            "fat": float(self.fat),
            "carbohydrates": float(self.carbohydrates),
            "total_time": self.total_time or 0,
            "primary_protein": self.primary_protein or "",
            "similarity_score": round(self.similarity_score, 4),
        }


# ---------------------------------------------------------------------------
# Cookbook output models
# ---------------------------------------------------------------------------

@dataclass
class CookbookGroup:
    """A group within a cookbook (e.g., Breakfast recipes)."""
    name: str
    meal_type: str
    recipes: list[Recipe] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "meal_type": self.meal_type,
            "recipe_count": len(self.recipes),
            "recipes": [r.to_dict() for r in self.recipes],
        }


@dataclass
class CookbookStats:
    """Aggregate stats for a cookbook."""
    total_recipes: int = 0
    avg_calories: float = 0.0
    avg_protein: float = 0.0
    avg_fat: float = 0.0
    avg_carbs: float = 0.0
    cuisines_represented: int = 0
    proteins_represented: int = 0

    def to_dict(self) -> dict:
        return {
            "total_recipes": self.total_recipes,
            "avg_calories": round(self.avg_calories, 1),
            "avg_protein": round(self.avg_protein, 1),
            "avg_fat": round(self.avg_fat, 1),
            "avg_carbs": round(self.avg_carbs, 1),
            "cuisines_represented": self.cuisines_represented,
            "proteins_represented": self.proteins_represented,
        }


@dataclass
class Cookbook:
    """A generated cookbook."""
    cookbook_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    groups: list[CookbookGroup] = field(default_factory=list)
    stats: CookbookStats = field(default_factory=CookbookStats)
    solver_status: str = "not_run"
    relaxations: list[str] = field(default_factory=list)

    def compute_stats(self):
        """Compute aggregate stats from all groups."""
        all_recipes = []
        for g in self.groups:
            all_recipes.extend(g.recipes)

        if not all_recipes:
            return

        self.stats.total_recipes = len(all_recipes)
        self.stats.avg_calories = sum(r.calories for r in all_recipes) / len(all_recipes)
        self.stats.avg_protein = sum(r.protein for r in all_recipes) / len(all_recipes)
        self.stats.avg_fat = sum(r.fat for r in all_recipes) / len(all_recipes)
        self.stats.avg_carbs = sum(r.carbohydrates for r in all_recipes) / len(all_recipes)

        all_cuisines = set()
        all_proteins = set()
        for r in all_recipes:
            all_cuisines.update(r.normalized_cuisines or [])
            if r.primary_protein:
                all_proteins.add(r.primary_protein)
        self.stats.cuisines_represented = len(all_cuisines)
        self.stats.proteins_represented = len(all_proteins)

    def to_dict(self) -> dict:
        return {
            "cookbook_id": self.cookbook_id,
            "name": self.name,
            "created_at": self.created_at,
            "solver_status": self.solver_status,
            "relaxations": self.relaxations,
            "groups": [g.to_dict() for g in self.groups],
            "stats": self.stats.to_dict(),
        }

    def to_json(self, indent=2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Meal plan input
# ---------------------------------------------------------------------------

@dataclass
class MealPlanInput:
    """Input constraints for meal plan generation."""
    weeks: int = 2
    daily_calories: int = 2000
    daily_calories_tolerance: int = 150
    daily_protein: int = 150
    daily_protein_tolerance: int = 20
    daily_carbs: int = 200
    daily_carbs_tolerance: int = 25
    daily_fat: int = 70
    daily_fat_tolerance: int = 15
    meal_calorie_split: dict[str, float] = field(default_factory=dict)
    serving_multipliers: list[float] = field(default_factory=lambda: list(_DEFAULT_MULTIPLIERS))

    @classmethod
    def from_mealplan_constraints(cls, mp: MealPlanConstraints) -> MealPlanInput:
        return cls(
            weeks=mp.weeks,
            daily_calories=mp.daily_calories,
            daily_calories_tolerance=mp.daily_calories_tolerance,
            daily_protein=mp.daily_protein,
            daily_protein_tolerance=mp.daily_protein_tolerance,
            daily_carbs=mp.daily_carbs,
            daily_carbs_tolerance=mp.daily_carbs_tolerance,
            daily_fat=mp.daily_fat,
            daily_fat_tolerance=mp.daily_fat_tolerance,
            meal_calorie_split=mp.meal_calorie_split,
            serving_multipliers=list(mp.serving_multipliers),
        )


# ---------------------------------------------------------------------------
# Meal plan output models
# ---------------------------------------------------------------------------

@dataclass
class MealSlot:
    """A single meal assignment in the plan."""
    meal_type: str
    recipe: Recipe
    serving_multiplier: float = 1.0
    adjusted_calories: float = 0.0
    adjusted_protein: float = 0.0
    adjusted_fat: float = 0.0
    adjusted_carbs: float = 0.0
    swaps: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "meal_type": self.meal_type,
            "recipe": self.recipe.to_dict(),
            "serving_multiplier": self.serving_multiplier,
            "adjusted_calories": round(self.adjusted_calories, 1),
            "adjusted_protein": round(self.adjusted_protein, 1),
            "adjusted_fat": round(self.adjusted_fat, 1),
            "adjusted_carbs": round(self.adjusted_carbs, 1),
            "swaps": self.swaps,
        }


@dataclass
class DayPlan:
    """One day's meal assignments."""
    day: int
    day_name: str
    meals: list[MealSlot] = field(default_factory=list)
    totals: dict = field(default_factory=dict)

    def compute_totals(self):
        cals = sum(
            m.adjusted_calories if m.adjusted_calories > 0 else m.recipe.calories
            for m in self.meals
        )
        pro = sum(
            m.adjusted_protein if m.adjusted_protein > 0 else m.recipe.protein
            for m in self.meals
        )
        fat = sum(
            m.adjusted_fat if m.adjusted_fat > 0 else m.recipe.fat
            for m in self.meals
        )
        carbs = sum(
            m.adjusted_carbs if m.adjusted_carbs > 0 else m.recipe.carbohydrates
            for m in self.meals
        )
        self.totals = {
            "calories": round(float(cals), 1),
            "protein": round(float(pro), 1),
            "fat": round(float(fat), 1),
            "carbohydrates": round(float(carbs), 1),
        }

    def to_dict(self) -> dict:
        return {
            "day": self.day,
            "day_name": self.day_name,
            "meals": [m.to_dict() for m in self.meals],
            "totals": self.totals,
        }


@dataclass
class WeekPlan:
    """One week of meal assignments."""
    week: int
    days: list[DayPlan] = field(default_factory=list)
    averages: dict = field(default_factory=dict)

    def compute_averages(self):
        if not self.days:
            return
        n = len(self.days)
        self.averages = {
            "avg_daily_calories": round(sum(d.totals.get("calories", 0) for d in self.days) / n, 1),
            "avg_daily_protein": round(sum(d.totals.get("protein", 0) for d in self.days) / n, 1),
            "avg_daily_fat": round(sum(d.totals.get("fat", 0) for d in self.days) / n, 1),
            "avg_daily_carbs": round(sum(d.totals.get("carbohydrates", 0) for d in self.days) / n, 1),
        }

    def to_dict(self) -> dict:
        return {
            "week": self.week,
            "days": [d.to_dict() for d in self.days],
            "averages": self.averages,
        }


@dataclass
class MealPlan:
    """Full meal plan output."""
    plan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    cookbook_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    daily_targets: dict = field(default_factory=dict)
    weeks: list[WeekPlan] = field(default_factory=list)
    solver_status: str = "not_run"
    relaxations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "cookbook_id": self.cookbook_id,
            "created_at": self.created_at,
            "daily_targets": self.daily_targets,
            "solver_status": self.solver_status,
            "relaxations": self.relaxations,
            "weeks": [w.to_dict() for w in self.weeks],
        }

    def to_json(self, indent=2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
