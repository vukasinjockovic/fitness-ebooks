"""Phase 3: MIP-based meal plan generation from a cookbook."""

from __future__ import annotations

import math
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


def generate_mealplan(
    input: MealPlanInput,
    cookbook: Cookbook,
) -> MealPlan:
    """Generate a meal plan by assigning cookbook recipes to week/day/meal slots.

    Uses MIP optimization with slack variables for macro targets (soft constraints)
    and progressive relaxation for hard constraints (variety, no-repeat).

    Each meal slot selects both a recipe AND a serving multiplier from the allowed
    list (e.g. [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]). The solver picks the best
    (recipe, multiplier) combination per slot to match daily macro targets.

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
        print("  WARNING: Cookbook has no recipes")
        return MealPlan(
            cookbook_id=cookbook.cookbook_id,
            solver_status="no_recipes",
        )

    weeks = input.weeks
    days = 7
    multipliers = input.serving_multipliers

    print(f"\n  Meal Plan: {weeks} weeks, {days} days/week")
    print(f"    Meal types: {meal_types}")
    print(f"    Serving multipliers: {multipliers}")
    print(f"    Daily targets: {input.daily_calories} cal, "
          f"{input.daily_protein}g protein, "
          f"{input.daily_carbs}g carbs, {input.daily_fat}g fat")

    # Print achievable range per meal type (with multipliers)
    min_mult = min(multipliers)
    max_mult = max(multipliers)
    for mt in meal_types:
        recipes = recipe_index[mt]
        print(f"    {mt}: {len(recipes)} unique recipes "
              f"(cal: {min(r.calories for r in recipes) * min_mult:.0f}-"
              f"{max(r.calories for r in recipes) * max_mult:.0f}, "
              f"pro: {min(r.protein for r in recipes) * min_mult:.0f}-"
              f"{max(r.protein for r in recipes) * max_mult:.0f})")

    # Calculate theoretical range
    min_daily = sum(
        min(r.calories for r in recipe_index[mt]) * min_mult for mt in meal_types
    )
    max_daily = sum(
        max(r.calories for r in recipe_index[mt]) * max_mult for mt in meal_types
    )
    print(f"    Achievable daily cal range: {min_daily:.0f} - {max_daily:.0f}")

    # Count total decision variables
    total_vars = sum(
        len(recipe_index[mt]) * len(multipliers) * weeks * days
        for mt in meal_types
    )
    print(f"    Decision variables: {total_vars}")

    relaxations = []

    # Try with progressively relaxed constraints
    for attempt in range(3):
        result = _try_solve(
            recipe_index=recipe_index,
            meal_types=meal_types,
            weeks=weeks,
            days=days,
            input=input,
            multipliers=multipliers,
            attempt=attempt,
            relaxations=relaxations,
        )
        if result is not None:
            plan, status = result
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

    # Final fallback: greedy assignment
    print("    Solver infeasible after all relaxations. Using greedy fallback.")
    relaxations.append("greedy_fallback")
    plan = _greedy_assign(recipe_index, meal_types, weeks, days, input, multipliers)
    plan.cookbook_id = cookbook.cookbook_id
    plan.daily_targets = {
        "calories": input.daily_calories,
        "protein": input.daily_protein,
        "carbs": input.daily_carbs,
        "fat": input.daily_fat,
    }
    plan.solver_status = "Infeasible_greedy_fallback"
    plan.relaxations = relaxations
    return plan


def _try_solve(
    recipe_index: dict[str, list[Recipe]],
    meal_types: list[str],
    weeks: int,
    days: int,
    input: MealPlanInput,
    multipliers: list[float],
    attempt: int,
    relaxations: list[str],
) -> tuple[MealPlan, str] | None:
    """Try to solve the MIP with given relaxation level.

    Uses soft constraints (slack variables with penalty) for daily macro targets
    so the solver always finds a feasible solution. Hard constraints are only
    used for structural requirements (exactly 1 recipe+multiplier combo per slot).

    Each meal slot is assigned a (recipe, multiplier) pair. The solver picks
    which combination best matches macro targets.

    attempt 0: Full variety constraints + no repeats within week
    attempt 1: Remove protein variety constraint
    attempt 2: Allow recipe repeats within week
    """
    prob = LpProblem(f"MealPlan_attempt{attempt}", LpMinimize)

    # Create binary variables: x[recipe_id, multiplier, week, day, meal_type]
    # Each variable represents assigning recipe r at multiplier s to slot (w,d,mt)
    x = {}
    for mt in meal_types:
        for r in recipe_index[mt]:
            for s in multipliers:
                for w in range(weeks):
                    for d in range(days):
                        key = (r.id, s, w, d, mt)
                        # Use a safe name (replace dots in multiplier)
                        s_name = str(s).replace(".", "p")
                        x[key] = LpVariable(
                            f"x_{r.id}_{s_name}_{w}_{d}_{mt}", cat="Binary"
                        )

    # Slack variables for each day's macros (positive = over target, negative = under)
    cal_over = {}
    cal_under = {}
    pro_over = {}
    pro_under = {}
    carb_over = {}
    carb_under = {}
    fat_over = {}
    fat_under = {}

    for w in range(weeks):
        for d in range(days):
            cal_over[(w, d)] = LpVariable(f"cal_over_{w}_{d}", lowBound=0)
            cal_under[(w, d)] = LpVariable(f"cal_under_{w}_{d}", lowBound=0)
            pro_over[(w, d)] = LpVariable(f"pro_over_{w}_{d}", lowBound=0)
            pro_under[(w, d)] = LpVariable(f"pro_under_{w}_{d}", lowBound=0)
            carb_over[(w, d)] = LpVariable(f"carb_over_{w}_{d}", lowBound=0)
            carb_under[(w, d)] = LpVariable(f"carb_under_{w}_{d}", lowBound=0)
            fat_over[(w, d)] = LpVariable(f"fat_over_{w}_{d}", lowBound=0)
            fat_under[(w, d)] = LpVariable(f"fat_under_{w}_{d}", lowBound=0)

    # OBJECTIVE: minimize total macro deviation (weighted) - quality bonus
    # - diversity bonus + multiplier deviation penalty
    cal_weight = 1.0 / max(input.daily_calories_tolerance, 1)
    pro_weight = 1.0 / max(input.daily_protein_tolerance, 1)
    carb_weight = 0.5 / max(input.daily_carbs_tolerance, 1)
    fat_weight = 0.5 / max(input.daily_fat_tolerance, 1)

    # Quality bonus (small, to break ties in favor of higher quality)
    quality_bonus = lpSum(
        r.quality_score * 0.001 * x[(r.id, s, w, d, mt)]
        for mt in meal_types
        for r in recipe_index[mt]
        for s in multipliers
        for w in range(weeks)
        for d in range(days)
    )

    # Diversity bonus: reward using more unique recipes per week per meal type.
    diversity_vars = {}
    for mt in meal_types:
        for r in recipe_index[mt]:
            for w in range(weeks):
                uvar = LpVariable(f"used_{r.id}_{w}_{mt}", cat="Binary")
                diversity_vars[(r.id, w, mt)] = uvar
                # used <= sum of assignments across all multipliers and days
                prob += uvar <= lpSum(
                    x[(r.id, s, w, d, mt)]
                    for s in multipliers
                    for d in range(days)
                )
                # used >= x for each day/multiplier (if assigned, used must be 1)
                for s in multipliers:
                    for d in range(days):
                        prob += uvar >= x[(r.id, s, w, d, mt)]

    diversity_weight = cal_weight * 200
    diversity_bonus = lpSum(
        diversity_weight * diversity_vars[(r.id, w, mt)]
        for mt in meal_types
        for r in recipe_index[mt]
        for w in range(weeks)
    )

    # Deviation penalty
    deviation_penalty = lpSum(
        cal_weight * (cal_over[(w, d)] + cal_under[(w, d)])
        + pro_weight * (pro_over[(w, d)] + pro_under[(w, d)])
        + carb_weight * (carb_over[(w, d)] + carb_under[(w, d)])
        + fat_weight * (fat_over[(w, d)] + fat_under[(w, d)])
        for w in range(weeks)
        for d in range(days)
    )

    # Multiplier deviation penalty: prefer 1.0 servings when possible.
    # Must be small relative to macro deviation weights so the solver freely
    # uses non-1.0x multipliers when they improve macro accuracy, but large
    # enough to prefer 1.0x when macros are easily met.
    #
    # Previous value (0.5) was an absolute constant that dominated the
    # macro deviation cost: missing 1g protein costs pro_weight (e.g. 0.05)
    # but using 1.5x cost 0.5*0.5 = 0.25 -- equivalent to missing 5g protein.
    # Across 28 slots/week this heavily suppressed multiplier usage.
    #
    # Fix: scale as a fraction of the average macro weight. Using 10% of the
    # mean weight ensures the penalty is always secondary to macro accuracy
    # but still creates a meaningful tie-breaker toward 1.0x servings.
    # With typical weights (cal=0.007, pro=0.05, carb=0.02, fat=0.03),
    # mean ~ 0.027, so penalty ~ 0.0027 per unit deviation from 1.0.
    # Using 1.5x costs 0.0027*0.5 = 0.0014 per slot vs protein benefit
    # of 0.05*10g = 0.5 -- penalty is ~0.3% of a 10g protein gain.
    avg_macro_weight = (cal_weight + pro_weight + carb_weight + fat_weight) / 4
    multiplier_penalty_weight = 0.1 * avg_macro_weight
    multiplier_penalty = lpSum(
        multiplier_penalty_weight * abs(s - 1.0) * x[(r.id, s, w, d, mt)]
        for mt in meal_types
        for r in recipe_index[mt]
        for s in multipliers
        for w in range(weeks)
        for d in range(days)
    )

    prob += deviation_penalty - quality_bonus - diversity_bonus + multiplier_penalty

    # HARD CONSTRAINT: exactly 1 (recipe, multiplier) combo per meal slot
    for w in range(weeks):
        for d in range(days):
            for mt in meal_types:
                prob += lpSum(
                    x[(r.id, s, w, d, mt)]
                    for r in recipe_index[mt]
                    for s in multipliers
                ) == 1

    # SOFT CONSTRAINTS via slack variables
    for w in range(weeks):
        for d in range(days):
            # Calories: sum of (recipe.calories * multiplier) for each chosen combo
            day_cal = lpSum(
                (r.calories * s) * x[(r.id, s, w, d, mt)]
                for mt in meal_types
                for r in recipe_index[mt]
                for s in multipliers
            )
            prob += day_cal - cal_over[(w, d)] + cal_under[(w, d)] == input.daily_calories

            # Protein
            day_pro = lpSum(
                (r.protein * s) * x[(r.id, s, w, d, mt)]
                for mt in meal_types
                for r in recipe_index[mt]
                for s in multipliers
            )
            prob += day_pro - pro_over[(w, d)] + pro_under[(w, d)] == input.daily_protein

            # Carbs
            day_carbs = lpSum(
                (r.carbohydrates * s) * x[(r.id, s, w, d, mt)]
                for mt in meal_types
                for r in recipe_index[mt]
                for s in multipliers
            )
            prob += day_carbs - carb_over[(w, d)] + carb_under[(w, d)] == input.daily_carbs

            # Fat
            day_fat = lpSum(
                (r.fat * s) * x[(r.id, s, w, d, mt)]
                for mt in meal_types
                for r in recipe_index[mt]
                for s in multipliers
            )
            prob += day_fat - fat_over[(w, d)] + fat_under[(w, d)] == input.daily_fat

    # No recipe repeat within same week (regardless of multiplier) - attempts 0, 1
    if attempt < 2:
        all_recipe_ids = set()
        recipe_mt_map: dict[int, str] = {}
        for mt in meal_types:
            for r in recipe_index[mt]:
                all_recipe_ids.add(r.id)
                recipe_mt_map[r.id] = mt

        for w in range(weeks):
            for rid in all_recipe_ids:
                mt = recipe_mt_map[rid]
                prob += lpSum(
                    x[(rid, s, w, d, mt)]
                    for s in multipliers
                    for d in range(days)
                    if (rid, s, w, d, mt) in x
                ) <= 1
    else:
        if "allowing_recipe_repeats_within_week" not in relaxations:
            relaxations.append("allowing_recipe_repeats_within_week")

    # Protein variety: max 2 meals with same primary_protein per day (attempt 0 only)
    if attempt < 1:
        protein_recipes: dict[str, list[tuple[int, str]]] = {}
        for mt in meal_types:
            for r in recipe_index[mt]:
                p = r.primary_protein or "Unknown"
                protein_recipes.setdefault(p, []).append((r.id, mt))

        for w in range(weeks):
            for d in range(days):
                for p, rid_mt_list in protein_recipes.items():
                    if len(rid_mt_list) > 2:
                        prob += lpSum(
                            x[(rid, s, w, d, mt)]
                            for rid, mt in rid_mt_list
                            for s in multipliers
                            if (rid, s, w, d, mt) in x
                        ) <= 2
    elif attempt == 1:
        if "removed_protein_variety_constraint" not in relaxations:
            relaxations.append("removed_protein_variety_constraint")

    # Solve
    solver = PULP_CBC_CMD(msg=False, timeLimit=60)
    prob.solve(solver)

    if prob.status != LpStatusOptimal:
        print(f"    Attempt {attempt}: solver status = {prob.status} (not optimal)")
        return None

    # Extract solution
    status = "Optimal" if attempt == 0 else f"Optimal_after_{attempt}_relaxations"

    # Report deviation stats
    total_cal_dev = 0
    total_pro_dev = 0
    for w in range(weeks):
        for d in range(days):
            cd = (cal_over[(w, d)].varValue or 0) + (cal_under[(w, d)].varValue or 0)
            pd = (pro_over[(w, d)].varValue or 0) + (pro_under[(w, d)].varValue or 0)
            total_cal_dev += cd
            total_pro_dev += pd
    n_days = weeks * days
    print(f"    Solver: {status}")
    print(f"    Avg daily cal deviation: {total_cal_dev / n_days:.0f}, "
          f"avg daily protein deviation: {total_pro_dev / n_days:.1f}g")

    plan = MealPlan()
    for w in range(weeks):
        week_plan = WeekPlan(week=w + 1)
        for d in range(days):
            day_plan = DayPlan(day=d + 1, day_name=DAY_NAMES[d])
            for mt in meal_types:
                assigned = False
                for r in recipe_index[mt]:
                    if assigned:
                        break
                    for s in multipliers:
                        key = (r.id, s, w, d, mt)
                        if key in x and x[key].varValue is not None and x[key].varValue > 0.5:
                            slot = MealSlot(
                                meal_type=mt,
                                recipe=r,
                                serving_multiplier=s,
                                adjusted_calories=r.calories * s,
                                adjusted_protein=r.protein * s,
                                adjusted_fat=r.fat * s,
                                adjusted_carbs=r.carbohydrates * s,
                                swaps=[sw.to_dict() if hasattr(sw, 'to_dict') else sw
                                       for sw in getattr(r, 'swaps', [])],
                            )
                            day_plan.meals.append(slot)
                            assigned = True
                            break
            day_plan.compute_totals()
            week_plan.days.append(day_plan)
        week_plan.compute_averages()
        plan.weeks.append(week_plan)

    return plan, status


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
