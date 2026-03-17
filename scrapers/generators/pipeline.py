"""End-to-end pipeline: input -> cookbook -> meal plan."""

from __future__ import annotations

import json
import os
from datetime import datetime

from config import get_connection
from models import CookbookInput, MealPlanInput, Cookbook, MealPlan
from cookbook_generator import generate_cookbook
from mealplan_generator import generate_mealplan


def run_pipeline(
    cookbook_input: CookbookInput,
    mealplan_input: MealPlanInput | None = None,
    swaps_per_recipe: int = 0,
    macro_tolerance_pct: float = 0.15,
    json_output: bool = False,
    db_source: str = "lake",
) -> tuple[Cookbook, MealPlan | None] | dict:
    """Run the full generation pipeline.

    Args:
        cookbook_input: Input for cookbook generation.
        mealplan_input: Optional input for meal plan generation.
            If None but cookbook_input.mealplan is set, uses that.
        swaps_per_recipe: Number of swap alternatives per recipe (0 = disabled).
        macro_tolerance_pct: Macro tolerance for swap matching (0.15 = 15%).
        json_output: When True, redirect progress to stderr and return a dict
            with cookbook/mealplan/summary instead of a tuple.
        db_source: 'lake' (default) or 'production'.

    Returns:
        When json_output=False: (Cookbook, MealPlan or None)
        When json_output=True: dict with 'cookbook', 'mealplan', 'summary' keys.
    """
    import sys

    # When json_output is True, temporarily replace sys.stdout with sys.stderr
    # so ALL print() calls (including those in sub-modules) go to stderr,
    # keeping stdout clean for the JSON result.
    original_stdout = sys.stdout
    if json_output:
        sys.stdout = sys.stderr

    try:
        with get_connection(db_source=db_source) as conn:
            # Phase 1: Generate cookbook
            print("=" * 60)
            print(f"GENERATING COOKBOOK: {cookbook_input.name}")
            print("=" * 60)

            cookbook = generate_cookbook(cookbook_input, conn, db_source=db_source)

            print(f"\nCookbook generated: {cookbook.stats.total_recipes} recipes")
            print(f"  Solver: {cookbook.solver_status}")

            # Phase 2: Swap enrichment (if requested)
            if swaps_per_recipe > 0:
                print("\n" + "=" * 60)
                print(f"ENRICHING WITH SWAPS ({swaps_per_recipe} per recipe)")
                print("=" * 60)

                from swap_enricher import enrich_cookbook_with_swaps
                cookbook = enrich_cookbook_with_swaps(
                    cookbook, conn,
                    swaps_per_recipe=swaps_per_recipe,
                    macro_tolerance_pct=macro_tolerance_pct,
                    dietary=cookbook_input.global_constraints.dietary or None,
                    db_source=db_source,
                )

            # Phase 3: Generate meal plan (if requested)
            plan = None
            if mealplan_input is None and cookbook_input.mealplan is not None:
                mealplan_input = MealPlanInput.from_mealplan_constraints(
                    cookbook_input.mealplan
                )

            if mealplan_input is not None:
                print("\n" + "=" * 60)
                print("GENERATING MEAL PLAN")
                print("=" * 60)

                plan = generate_mealplan(mealplan_input, cookbook)

                print(f"\nMeal plan generated: {len(plan.weeks)} weeks")
                print(f"  Solver: {plan.solver_status}")
    finally:
        if json_output:
            sys.stdout = original_stdout

    if json_output:
        return _build_json_result(cookbook, plan, mealplan_input)

    return cookbook, plan


def _build_json_result(
    cookbook: Cookbook,
    plan: MealPlan | None,
    mealplan_input: MealPlanInput | None,
) -> dict:
    """Build the JSON output dict for --json-output mode."""
    # Summary stats
    summary = {
        "total_recipes": cookbook.stats.total_recipes,
        "solver_status": cookbook.solver_status,
    }

    if plan is not None and mealplan_input is not None:
        # Compute average daily deviation
        all_cal_devs = []
        all_pro_devs = []
        for week in plan.weeks:
            for day in week.days:
                t = day.totals
                all_cal_devs.append(abs(t.get("calories", 0) - mealplan_input.daily_calories))
                all_pro_devs.append(abs(t.get("protein", 0) - mealplan_input.daily_protein))
        if all_cal_devs:
            summary["avg_daily_cal_deviation"] = round(
                sum(all_cal_devs) / len(all_cal_devs), 1
            )
        if all_pro_devs:
            summary["avg_daily_protein_deviation"] = round(
                sum(all_pro_devs) / len(all_pro_devs), 1
            )

    result = {
        "cookbook": cookbook.to_dict(),
        "mealplan": plan.to_dict() if plan is not None else None,
        "summary": summary,
    }
    return result


def save_outputs(
    cookbook: Cookbook,
    plan: MealPlan | None,
    output_dir: str = "output",
) -> tuple[str, str | None]:
    """Save cookbook and plan to JSON files.

    Returns (cookbook_path, plan_path or None).
    """
    os.makedirs(output_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = cookbook.name.lower().replace(" ", "_")[:40]

    cookbook_path = os.path.join(output_dir, f"cookbook_{safe_name}_{ts}.json")
    with open(cookbook_path, "w") as f:
        f.write(cookbook.to_json())
    print(f"\nCookbook saved: {cookbook_path}")

    plan_path = None
    if plan is not None:
        plan_path = os.path.join(output_dir, f"mealplan_{safe_name}_{ts}.json")
        with open(plan_path, "w") as f:
            f.write(plan.to_json())
        print(f"Meal plan saved: {plan_path}")

    return cookbook_path, plan_path


def print_cookbook_summary(cookbook: Cookbook):
    """Print a human-readable summary of the cookbook."""
    print("\n" + "=" * 60)
    print(f"COOKBOOK SUMMARY: {cookbook.name}")
    print("=" * 60)

    s = cookbook.stats
    print(f"\nTotal recipes: {s.total_recipes}")
    print(f"Average macros: {s.avg_calories:.0f} cal, "
          f"{s.avg_protein:.1f}g protein, "
          f"{s.avg_fat:.1f}g fat, "
          f"{s.avg_carbs:.1f}g carbs")
    print(f"Cuisines represented: {s.cuisines_represented}")
    print(f"Proteins represented: {s.proteins_represented}")
    print(f"Solver: {cookbook.solver_status}")

    if cookbook.relaxations:
        print(f"Relaxations: {', '.join(cookbook.relaxations)}")

    for group in cookbook.groups:
        print(f"\n  {group.name} ({group.meal_type}): {len(group.recipes)} recipes")
        if group.recipes:
            avg_cal = sum(r.calories for r in group.recipes) / len(group.recipes)
            avg_pro = sum(r.protein for r in group.recipes) / len(group.recipes)
            avg_fat = sum(r.fat for r in group.recipes) / len(group.recipes)
            avg_carbs = sum(r.carbohydrates for r in group.recipes) / len(group.recipes)
            print(f"    Avg: {avg_cal:.0f} cal, {avg_pro:.1f}g P, "
                  f"{avg_fat:.1f}g F, {avg_carbs:.1f}g C")

            from collections import Counter
            proteins = Counter(r.primary_protein or "?" for r in group.recipes)
            print(f"    Proteins: {dict(proteins)}")

            cuisines = set()
            for r in group.recipes:
                cuisines.update(r.normalized_cuisines or [])
            print(f"    Cuisines: {', '.join(sorted(cuisines)[:8])}")

            for r in group.recipes:
                print(f"      - {r.title} ({r.calories:.0f} cal, "
                      f"{r.protein:.0f}g P) [score={r.quality_score}]")


def print_mealplan_summary(plan: MealPlan, input: MealPlanInput):
    """Print a human-readable summary of the meal plan."""
    print("\n" + "=" * 60)
    print("MEAL PLAN SUMMARY")
    print("=" * 60)

    print(f"\nTargets: {input.daily_calories} cal, "
          f"{input.daily_protein}g P, "
          f"{input.daily_carbs}g C, "
          f"{input.daily_fat}g F")
    print(f"Solver: {plan.solver_status}")

    if plan.relaxations:
        print(f"Relaxations: {', '.join(plan.relaxations)}")

    for week in plan.weeks:
        print(f"\n--- WEEK {week.week} ---")
        for day in week.days:
            t = day.totals
            cal_dev = t.get("calories", 0) - input.daily_calories
            pro_dev = t.get("protein", 0) - input.daily_protein

            print(f"\n  {day.day_name} (Day {day.day}):")
            for meal in day.meals:
                r = meal.recipe
                s = meal.serving_multiplier
                # Use adjusted macros if available, else raw recipe values
                cal = meal.adjusted_calories if meal.adjusted_calories > 0 else r.calories
                pro = meal.adjusted_protein if meal.adjusted_protein > 0 else r.protein
                fat = meal.adjusted_fat if meal.adjusted_fat > 0 else r.fat
                carb = meal.adjusted_carbs if meal.adjusted_carbs > 0 else r.carbohydrates
                serving_label = f" ({s} servings)" if s != 1.0 else " (1.0 serving)"
                print(f"    {meal.meal_type}: {r.title}{serving_label} "
                      f"- {cal:.0f} cal, {pro:.0f}g P, "
                      f"{fat:.0f}g F, {carb:.0f}g C")

                # Print swap alternatives if present
                if meal.swaps:
                    swap_strs = []
                    for sw in meal.swaps[:3]:  # Show up to 3 swaps inline
                        if isinstance(sw, dict):
                            swap_strs.append(
                                f"{sw.get('title', '?')} "
                                f"({sw.get('calories', 0):.0f} cal, "
                                f"{sw.get('protein', 0):.0f}g P)"
                            )
                        else:
                            swap_strs.append(str(sw))
                    remaining = len(meal.swaps) - 3
                    suffix = f" +{remaining} more" if remaining > 0 else ""
                    print(f"      Swaps: {' | '.join(swap_strs)}{suffix}")

            print(f"    TOTAL: {t.get('calories', 0):.0f} cal, "
                  f"{t.get('protein', 0):.0f}g P, "
                  f"{t.get('fat', 0):.0f}g F, "
                  f"{t.get('carbohydrates', 0):.0f}g C  "
                  f"[cal dev: {cal_dev:+.0f}, pro dev: {pro_dev:+.0f}]")

        avg = week.averages
        print(f"\n  Week {week.week} averages: "
              f"{avg.get('avg_daily_calories', 0):.0f} cal, "
              f"{avg.get('avg_daily_protein', 0):.0f}g P, "
              f"{avg.get('avg_daily_fat', 0):.0f}g F, "
              f"{avg.get('avg_daily_carbs', 0):.0f}g C")
