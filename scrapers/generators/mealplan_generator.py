"""Phase 3: MIP-based meal plan generation from a cookbook.

Uses day-by-day MIP solving instead of a monolithic all-days-at-once approach.
Each day is a small MIP (~168 variables for 42 recipes x 4 multipliers) that
solves in <1s, versus the old monolithic MIP (3,528+ variables) that took ~60s.
"""

from __future__ import annotations

import math
import sys
from collections import Counter

from pulp import (
    LpProblem, LpMaximize, LpMinimize, LpVariable, lpSum,
    PULP_CBC_CMD, LpStatusOptimal, LpStatusNotSolved,
)

from config import DAY_NAMES
from models import (
    MealPlanInput, Cookbook, MealPlan, WeekPlan, DayPlan, MealSlot, Recipe,
)


def _build_recipe_index(cookbook: Cookbook) -> dict[str, list[Recipe]]:
    """Map meal_type -> list of unique recipes from cookbook groups."""
    index: dict[str, list[Recipe]] = {}
    for group in cookbook.groups:
        mt = group.meal_type
        if mt not in index:
            index[mt] = []
        # Deduplicate by recipe id within each meal type
        seen_ids = {r.id for r in index[mt]}
        for r in group.recipes:
            if r.id not in seen_ids:
                index[mt].append(r)
                seen_ids.add(r.id)
    return index


def _solve_day(
    recipe_index: dict[str, list[Recipe]],
    meal_types: list[str],
    input: MealPlanInput,
    multipliers: list[float],
    used_this_week: set,
    enforce_protein_variety: bool = True,
) -> dict[str, tuple[Recipe, float]] | None:
    """Solve a single day's meal assignment via a small MIP.

    Returns dict of meal_type -> (recipe, multiplier) or None if infeasible.

    The MIP is tiny: |meal_types| * |available_recipes| * |multipliers| variables.
    With 4 meal types, ~10 recipes each, 6 multipliers = ~240 binary variables.
    Solves in <0.5s.
    """
    prob = LpProblem("day_plan", LpMinimize)

    # Build variables: x[meal_type][recipe_idx][mult_idx] = binary
    # Only include recipes NOT already used this week
    x: dict[str, dict[int, dict[int, LpVariable]]] = {}
    available_count = 0
    for mt in meal_types:
        x[mt] = {}
        for r_idx, recipe in enumerate(recipe_index[mt]):
            if recipe.id in used_this_week:
                continue
            x[mt][r_idx] = {}
            for m_idx, mult in enumerate(multipliers):
                s_name = str(mult).replace(".", "p")
                x[mt][r_idx][m_idx] = LpVariable(
                    f"x_{mt}_{r_idx}_{s_name}", cat="Binary"
                )
                available_count += 1

    if available_count == 0:
        return None

    # HARD CONSTRAINT: exactly 1 recipe+multiplier per meal slot
    for mt in meal_types:
        slot_vars = []
        for r_idx in x.get(mt, {}):
            for m_idx in x[mt].get(r_idx, {}):
                slot_vars.append(x[mt][r_idx][m_idx])
        if not slot_vars:
            return None  # No recipes available for this meal type
        prob += lpSum(slot_vars) == 1

    # Slack variables for macro deviations
    cal_over = LpVariable("cal_over", lowBound=0)
    cal_under = LpVariable("cal_under", lowBound=0)
    pro_over = LpVariable("pro_over", lowBound=0)
    pro_under = LpVariable("pro_under", lowBound=0)
    carb_over = LpVariable("carb_over", lowBound=0)
    carb_under = LpVariable("carb_under", lowBound=0)
    fat_over = LpVariable("fat_over", lowBound=0)
    fat_under = LpVariable("fat_under", lowBound=0)

    # Daily total macros
    day_cal = lpSum(
        x[mt][r_idx][m_idx] * recipe_index[mt][r_idx].calories * multipliers[m_idx]
        for mt in meal_types
        for r_idx in x.get(mt, {})
        for m_idx in x[mt].get(r_idx, {})
    )
    day_pro = lpSum(
        x[mt][r_idx][m_idx] * recipe_index[mt][r_idx].protein * multipliers[m_idx]
        for mt in meal_types
        for r_idx in x.get(mt, {})
        for m_idx in x[mt].get(r_idx, {})
    )
    day_carbs = lpSum(
        x[mt][r_idx][m_idx] * recipe_index[mt][r_idx].carbohydrates * multipliers[m_idx]
        for mt in meal_types
        for r_idx in x.get(mt, {})
        for m_idx in x[mt].get(r_idx, {})
    )
    day_fat = lpSum(
        x[mt][r_idx][m_idx] * recipe_index[mt][r_idx].fat * multipliers[m_idx]
        for mt in meal_types
        for r_idx in x.get(mt, {})
        for m_idx in x[mt].get(r_idx, {})
    )

    # Link deviation variables
    prob += day_cal - cal_over + cal_under == input.daily_calories
    prob += day_pro - pro_over + pro_under == input.daily_protein
    prob += day_carbs - carb_over + carb_under == input.daily_carbs
    prob += day_fat - fat_over + fat_under == input.daily_fat

    # Protein variety: max 2 meals with same primary_protein
    if enforce_protein_variety:
        protein_vars: dict[str, list] = {}
        for mt in meal_types:
            for r_idx in x.get(mt, {}):
                p = recipe_index[mt][r_idx].primary_protein or "Unknown"
                if p not in protein_vars:
                    protein_vars[p] = []
                for m_idx in x[mt].get(r_idx, {}):
                    protein_vars[p].append(x[mt][r_idx][m_idx])
        for p, var_list in protein_vars.items():
            if len(var_list) > 2:
                prob += lpSum(var_list) <= 2

    # Weights for objective
    cal_weight = 1.0 / max(input.daily_calories_tolerance, 1)
    pro_weight = 1.0 / max(input.daily_protein_tolerance, 1)
    carb_weight = 0.5 / max(input.daily_carbs_tolerance, 1)
    fat_weight = 0.5 / max(input.daily_fat_tolerance, 1)

    # Quality bonus
    quality_bonus = lpSum(
        recipe_index[mt][r_idx].quality_score * 0.001 * x[mt][r_idx][m_idx]
        for mt in meal_types
        for r_idx in x.get(mt, {})
        for m_idx in x[mt].get(r_idx, {})
    )

    # Multiplier deviation penalty (prefer 1.0x)
    avg_macro_weight = (cal_weight + pro_weight + carb_weight + fat_weight) / 4
    multiplier_penalty_weight = 0.1 * avg_macro_weight
    multiplier_penalty = lpSum(
        multiplier_penalty_weight * abs(multipliers[m_idx] - 1.0) * x[mt][r_idx][m_idx]
        for mt in meal_types
        for r_idx in x.get(mt, {})
        for m_idx in x[mt].get(r_idx, {})
    )

    # Objective
    prob += (
        cal_weight * (cal_over + cal_under)
        + pro_weight * (pro_over + pro_under)
        + carb_weight * (carb_over + carb_under)
        + fat_weight * (fat_over + fat_under)
        - quality_bonus
        + multiplier_penalty
    )

    # Solve with 5s limit per day
    solver = PULP_CBC_CMD(msg=False, timeLimit=5)
    prob.solve(solver)

    if prob.status != LpStatusOptimal:
        return None

    # Extract solution
    result: dict[str, tuple[Recipe, float]] = {}
    for mt in meal_types:
        for r_idx in x.get(mt, {}):
            for m_idx in x[mt].get(r_idx, {}):
                val = x[mt][r_idx][m_idx].varValue
                if val is not None and val > 0.5:
                    result[mt] = (recipe_index[mt][r_idx], multipliers[m_idx])

    # Check we got a recipe for every meal type
    if len(result) != len(meal_types):
        return None

    return result


def generate_mealplan(
    input: MealPlanInput,
    cookbook: Cookbook,
) -> MealPlan:
    """Generate a meal plan by solving each day independently.

    Day-by-day approach: solve 14 tiny MIPs (~240 vars each) instead of
    one massive MIP (3,528+ vars). Each day takes <1s, total <14s.

    Progressive relaxation per day:
      1. Try with no-repeat + protein variety
      2. Try without protein variety
      3. Try allowing repeats (clear used_this_week)
      4. Greedy fallback for that day

    Args:
        input: MealPlanInput with daily targets, week count, and serving_multipliers.
        cookbook: Cookbook with recipe groups.

    Returns:
        MealPlan with weeks/days/meals assigned.
    """
    recipe_index = _build_recipe_index(cookbook)

    # Determine which meal types we have
    meal_types = list(recipe_index.keys())
    if not meal_types:
        print("  WARNING: Cookbook has no recipes", file=sys.stderr)
        return MealPlan(
            cookbook_id=cookbook.cookbook_id,
            solver_status="no_recipes",
        )

    weeks = input.weeks
    days = 7
    multipliers = input.serving_multipliers

    print(f"\n  Meal Plan: {weeks} weeks, {days} days/week", file=sys.stderr)
    print(f"    Meal types: {meal_types}", file=sys.stderr)
    print(f"    Serving multipliers: {multipliers}", file=sys.stderr)
    print(f"    Daily targets: {input.daily_calories} cal, "
          f"{input.daily_protein}g protein, "
          f"{input.daily_carbs}g carbs, {input.daily_fat}g fat",
          file=sys.stderr)

    # Print achievable range per meal type (with multipliers)
    min_mult = min(multipliers)
    max_mult = max(multipliers)
    for mt in meal_types:
        recipes = recipe_index[mt]
        print(f"    {mt}: {len(recipes)} unique recipes "
              f"(cal: {min(r.calories for r in recipes) * min_mult:.0f}-"
              f"{max(r.calories for r in recipes) * max_mult:.0f}, "
              f"pro: {min(r.protein for r in recipes) * min_mult:.0f}-"
              f"{max(r.protein for r in recipes) * max_mult:.0f})",
              file=sys.stderr)

    # Calculate theoretical range
    min_daily = sum(
        min(r.calories for r in recipe_index[mt]) * min_mult for mt in meal_types
    )
    max_daily = sum(
        max(r.calories for r in recipe_index[mt]) * max_mult for mt in meal_types
    )
    print(f"    Achievable daily cal range: {min_daily:.0f} - {max_daily:.0f}",
          file=sys.stderr)

    # Per-day variables are much smaller
    per_day_vars = sum(
        len(recipe_index[mt]) * len(multipliers) for mt in meal_types
    )
    print(f"    Day-by-day mode: ~{per_day_vars} vars/day x {weeks * days} days",
          file=sys.stderr)

    relaxations: list[str] = []
    total_cal_dev = 0.0
    total_pro_dev = 0.0
    total_days_solved = 0
    any_relaxation_used = False
    protein_variety_removed = False
    repeats_allowed = False

    plan = MealPlan()

    for w in range(weeks):
        week_plan = WeekPlan(week=w + 1)
        used_this_week: set = set()

        for d in range(days):
            day_result = None

            # Attempt 0: full constraints (no-repeat + protein variety)
            day_result = _solve_day(
                recipe_index, meal_types, input, multipliers,
                used_this_week, enforce_protein_variety=True,
            )

            # Attempt 1: remove protein variety constraint
            if day_result is None:
                if not protein_variety_removed:
                    protein_variety_removed = True
                    any_relaxation_used = True
                day_result = _solve_day(
                    recipe_index, meal_types, input, multipliers,
                    used_this_week, enforce_protein_variety=False,
                )

            # Attempt 2: allow repeats (clear used_this_week for this solve)
            if day_result is None:
                if not repeats_allowed:
                    repeats_allowed = True
                    any_relaxation_used = True
                day_result = _solve_day(
                    recipe_index, meal_types, input, multipliers,
                    set(), enforce_protein_variety=False,
                )

            if day_result is not None:
                day_plan = DayPlan(day=d + 1, day_name=DAY_NAMES[d])
                for mt in meal_types:
                    recipe, mult = day_result[mt]
                    used_this_week.add(recipe.id)
                    slot = MealSlot(
                        meal_type=mt,
                        recipe=recipe,
                        serving_multiplier=mult,
                        adjusted_calories=recipe.calories * mult,
                        adjusted_protein=recipe.protein * mult,
                        adjusted_fat=recipe.fat * mult,
                        adjusted_carbs=recipe.carbohydrates * mult,
                        swaps=[sw.to_dict() if hasattr(sw, 'to_dict') else sw
                               for sw in getattr(recipe, 'swaps', [])],
                    )
                    day_plan.meals.append(slot)
                day_plan.compute_totals()

                # Track deviation stats
                total_cal_dev += abs(day_plan.totals.get("calories", 0) - input.daily_calories)
                total_pro_dev += abs(day_plan.totals.get("protein", 0) - input.daily_protein)
                total_days_solved += 1

                week_plan.days.append(day_plan)
            else:
                # Greedy fallback for this day
                if "greedy_fallback_days" not in relaxations:
                    relaxations.append("greedy_fallback_days")
                day_plan = _greedy_day(
                    recipe_index, meal_types, input, multipliers,
                    w, d, used_this_week,
                )
                # Mark recipes used even from greedy
                for meal in day_plan.meals:
                    used_this_week.add(meal.recipe.id)
                week_plan.days.append(day_plan)
                total_days_solved += 1

            print(f"    Week {w+1} {DAY_NAMES[d]}: done", file=sys.stderr)

        week_plan.compute_averages()
        plan.weeks.append(week_plan)

    # Build relaxation list
    if protein_variety_removed:
        relaxations.append("removed_protein_variety_constraint")
    if repeats_allowed:
        relaxations.append("allowing_recipe_repeats_within_week")

    # Determine solver status
    if not any_relaxation_used and "greedy_fallback_days" not in relaxations:
        status = "Optimal"
    elif "greedy_fallback_days" in relaxations:
        n_relax = sum(1 for r in relaxations if r != "greedy_fallback_days")
        status = f"Optimal_after_{n_relax + 1}_relaxations"
    else:
        n_relax = len([r for r in relaxations if r not in ("greedy_fallback_days",)])
        status = f"Optimal_after_{n_relax}_relaxations"

    if total_days_solved > 0:
        print(f"    Solver: {status}", file=sys.stderr)
        print(f"    Avg daily cal deviation: {total_cal_dev / total_days_solved:.0f}, "
              f"avg daily protein deviation: {total_pro_dev / total_days_solved:.1f}g",
              file=sys.stderr)

    plan.cookbook_id = cookbook.cookbook_id
    plan.daily_targets = {
        "calories": input.daily_calories,
        "protein": input.daily_protein,
        "carbs": input.daily_carbs,
        "fat": input.daily_fat,
    }
    plan.solver_status = status
    plan.relaxations = list(relaxations)

    return plan


def _greedy_day(
    recipe_index: dict[str, list[Recipe]],
    meal_types: list[str],
    input: MealPlanInput,
    multipliers: list[float],
    week: int,
    day: int,
    used_this_week: set,
) -> DayPlan:
    """Greedy fallback for a single day: pick best calorie-matching recipe."""
    n_meals = len(meal_types)
    per_meal_target = input.daily_calories / n_meals if n_meals > 0 else 500

    day_plan = DayPlan(day=day + 1, day_name=DAY_NAMES[day])
    for mt in meal_types:
        recipes = recipe_index[mt]
        if not recipes:
            continue

        # Prefer unused recipes, fall back to any
        available = [r for r in recipes if r.id not in used_this_week]
        if not available:
            available = recipes

        # Rotate through available
        idx = (week * 7 + day) % len(available)
        r = available[idx]

        best_s = 1.0
        best_diff = float("inf")
        for s in multipliers:
            diff = abs(r.calories * s - per_meal_target)
            if diff < best_diff:
                best_diff = diff
                best_s = s

        slot = MealSlot(
            meal_type=mt,
            recipe=r,
            serving_multiplier=best_s,
            adjusted_calories=r.calories * best_s,
            adjusted_protein=r.protein * best_s,
            adjusted_fat=r.fat * best_s,
            adjusted_carbs=r.carbohydrates * best_s,
            swaps=[sw.to_dict() if hasattr(sw, 'to_dict') else sw
                   for sw in getattr(r, 'swaps', [])],
        )
        day_plan.meals.append(slot)
    day_plan.compute_totals()
    return day_plan


def _greedy_assign(
    recipe_index: dict[str, list[Recipe]],
    meal_types: list[str],
    weeks: int,
    days: int,
    input: MealPlanInput,
    multipliers: list[float],
) -> MealPlan:
    """Greedy fallback: rotate through recipes for each meal type.

    Picks the multiplier closest to achieving per-slot calorie share.
    """
    plan = MealPlan()

    # Determine target calories per meal type (equal split if no split given)
    n_meals = len(meal_types)
    per_meal_target = input.daily_calories / n_meals if n_meals > 0 else 500

    for w in range(weeks):
        week_plan = WeekPlan(week=w + 1)
        for d in range(days):
            day_plan = DayPlan(day=d + 1, day_name=DAY_NAMES[d])
            for mt in meal_types:
                recipes = recipe_index[mt]
                if recipes:
                    idx = (w * 7 + d) % len(recipes)
                    r = recipes[idx]

                    # Pick best multiplier for calorie target
                    best_s = 1.0
                    best_diff = float("inf")
                    for s in multipliers:
                        diff = abs(r.calories * s - per_meal_target)
                        if diff < best_diff:
                            best_diff = diff
                            best_s = s

                    slot = MealSlot(
                        meal_type=mt,
                        recipe=r,
                        serving_multiplier=best_s,
                        adjusted_calories=r.calories * best_s,
                        adjusted_protein=r.protein * best_s,
                        adjusted_fat=r.fat * best_s,
                        adjusted_carbs=r.carbohydrates * best_s,
                        swaps=[sw.to_dict() if hasattr(sw, 'to_dict') else sw
                               for sw in getattr(r, 'swaps', [])],
                    )
                    day_plan.meals.append(slot)
            day_plan.compute_totals()
            week_plan.days.append(day_plan)
        week_plan.compute_averages()
        plan.weeks.append(week_plan)

    return plan
