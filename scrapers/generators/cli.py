#!/usr/bin/env python3
"""CLI for cookbook and meal plan generation."""

from __future__ import annotations

import argparse
import json
import sys
import os

# Allow running from the generators directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import CookbookInput, MealPlanInput, Cookbook, MealPlan
from pipeline import run_pipeline, save_outputs, print_cookbook_summary, print_mealplan_summary
from cookbook_generator import generate_cookbook
from mealplan_generator import generate_mealplan
from config import get_connection


def _load_input(args):
    """Load input JSON from --stdin or from input_file."""
    if getattr(args, 'stdin', False):
        return json.load(sys.stdin)
    elif getattr(args, 'input_file', None):
        with open(args.input_file) as f:
            return json.load(f)
    else:
        print("Error: either provide input_file or use --stdin", file=sys.stderr)
        sys.exit(1)


def cmd_cookbook(args):
    """Generate a cookbook from input JSON."""
    data = _load_input(args)

    cookbook_input = CookbookInput.from_dict(data)

    # Only generate cookbook, ignore mealplan section
    original_mp = cookbook_input.mealplan
    cookbook_input.mealplan = None

    swaps_per_recipe = getattr(args, 'swaps', 0) or 0
    db_source = getattr(args, 'db_source', 'lake') or 'lake'
    json_output = getattr(args, 'json_output', False)

    if json_output:
        result = run_pipeline(
            cookbook_input,
            swaps_per_recipe=swaps_per_recipe,
            json_output=True,
            db_source=db_source,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        cookbook, _ = run_pipeline(
            cookbook_input,
            swaps_per_recipe=swaps_per_recipe,
            db_source=db_source,
        )
        print_cookbook_summary(cookbook)
        save_outputs(cookbook, None, output_dir=args.output_dir)


def cmd_full(args):
    """Generate cookbook + meal plan from input JSON."""
    data = _load_input(args)

    cookbook_input = CookbookInput.from_dict(data)

    # Override mealplan params from CLI if provided
    if cookbook_input.mealplan is not None:
        if args.weeks:
            cookbook_input.mealplan.weeks = args.weeks
        if args.daily_cal:
            cookbook_input.mealplan.daily_calories = args.daily_cal
        if args.protein:
            cookbook_input.mealplan.daily_protein = args.protein
        if args.multipliers:
            cookbook_input.mealplan.serving_multipliers = [
                float(x.strip()) for x in args.multipliers.split(",")
            ]

    swaps_per_recipe = getattr(args, 'swaps', 0) or 0
    db_source = getattr(args, 'db_source', 'lake') or 'lake'
    json_output = getattr(args, 'json_output', False)

    if json_output:
        result = run_pipeline(
            cookbook_input,
            swaps_per_recipe=swaps_per_recipe,
            json_output=True,
            db_source=db_source,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        cookbook, plan = run_pipeline(
            cookbook_input,
            swaps_per_recipe=swaps_per_recipe,
            db_source=db_source,
        )
        print_cookbook_summary(cookbook)

        if plan is not None:
            mp_input = MealPlanInput.from_mealplan_constraints(cookbook_input.mealplan)
            print_mealplan_summary(plan, mp_input)

        save_outputs(cookbook, plan, output_dir=args.output_dir)


def cmd_mealplan(args):
    """Generate meal plan from existing cookbook JSON."""
    with open(args.cookbook_file) as f:
        data = json.load(f)

    # Reconstruct Cookbook from saved JSON
    from models import Recipe, CookbookGroup, CookbookStats
    cookbook = Cookbook(
        cookbook_id=data.get("cookbook_id", ""),
        name=data.get("name", ""),
    )
    for gdata in data.get("groups", []):
        recipes = []
        for rd in gdata.get("recipes", []):
            recipes.append(Recipe(
                id=rd["recipe_id"],
                source_id="",
                slug=rd.get("slug", ""),
                title=rd["title"],
                url=rd.get("url", ""),
                image=rd.get("image", ""),
                calories=float(rd["calories"]),
                protein=float(rd["protein"]),
                fat=float(rd["fat"]),
                carbohydrates=float(rd["carbohydrates"]),
                total_time=rd.get("total_time", 0),
                serving_size=rd.get("serving_size", 1),
                ingredients=rd.get("ingredients", []),
                method=rd.get("method", []),
                meal_types=rd.get("meal_types", []),
                diet_tags=rd.get("diet_tags", []),
                normalized_cuisines=rd.get("cuisines", []),
                primary_protein=rd.get("primary_protein", ""),
                quality_score=rd.get("quality_score", 0),
            ))
        group = CookbookGroup(
            name=gdata["name"],
            meal_type=gdata["meal_type"],
            recipes=recipes,
        )
        cookbook.groups.append(group)
    cookbook.compute_stats()

    multipliers = None
    if args.multipliers:
        multipliers = [float(x.strip()) for x in args.multipliers.split(",")]

    mp_kwargs = dict(
        weeks=args.weeks or 2,
        daily_calories=args.daily_cal or 2000,
        daily_calories_tolerance=150,
        daily_protein=args.protein or 150,
        daily_protein_tolerance=20,
        daily_carbs=args.carbs or 200,
        daily_carbs_tolerance=25,
        daily_fat=args.fat or 70,
        daily_fat_tolerance=15,
    )
    if multipliers:
        mp_kwargs["serving_multipliers"] = multipliers

    mp_input = MealPlanInput(**mp_kwargs)

    plan = generate_mealplan(mp_input, cookbook)
    print_mealplan_summary(plan, mp_input)
    save_outputs(cookbook, plan, output_dir=args.output_dir)


def main():
    parser = argparse.ArgumentParser(
        description="Cookbook & Meal Plan Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--output-dir", "-o", default="output",
                        help="Output directory (default: output)")

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Shared flags helper
    def _add_common_flags(p, positional_name="input_file"):
        """Add --stdin, --json-output, --db-source to a subparser."""
        if positional_name:
            p.add_argument(positional_name, nargs="?", default=None,
                           help="Path to input JSON file (optional with --stdin)")
        p.add_argument("--stdin", action="store_true",
                       help="Read input JSON from stdin instead of a file")
        p.add_argument("--json-output", action="store_true",
                       help="Output result as JSON to stdout (progress goes to stderr)")
        p.add_argument("--db-source", default="lake", choices=["lake", "production"],
                       help="Recipe database source: 'lake' (default) or 'production'")

    # cookbook command
    p_cb = subparsers.add_parser("cookbook", help="Generate cookbook only")
    _add_common_flags(p_cb, "input_file")
    p_cb.add_argument("--swaps", type=int, default=0,
                      help="Number of swap alternatives per recipe (0 = disabled, default: 0)")
    p_cb.set_defaults(func=cmd_cookbook)

    # full command
    p_full = subparsers.add_parser("full", help="Generate cookbook + meal plan")
    _add_common_flags(p_full, "input_file")
    p_full.add_argument("--weeks", type=int, help="Override weeks")
    p_full.add_argument("--daily-cal", type=int, help="Override daily calories")
    p_full.add_argument("--protein", type=int, help="Override daily protein")
    p_full.add_argument("--multipliers", type=str, default=None,
                        help="Serving multipliers (comma-separated, e.g. 0.5,0.75,1.0,1.25,1.5,2.0)")
    p_full.add_argument("--swaps", type=int, default=0,
                        help="Number of swap alternatives per recipe (0 = disabled, default: 0)")
    p_full.set_defaults(func=cmd_full)

    # mealplan command
    p_mp = subparsers.add_parser("mealplan", help="Generate meal plan from cookbook")
    p_mp.add_argument("cookbook_file", help="Path to cookbook JSON file")
    p_mp.add_argument("--weeks", type=int, default=2, help="Number of weeks")
    p_mp.add_argument("--daily-cal", type=int, default=2000, help="Daily calories")
    p_mp.add_argument("--protein", type=int, default=150, help="Daily protein (g)")
    p_mp.add_argument("--carbs", type=int, default=200, help="Daily carbs (g)")
    p_mp.add_argument("--fat", type=int, default=70, help="Daily fat (g)")
    p_mp.add_argument("--multipliers", type=str, default=None,
                      help="Serving multipliers (comma-separated, e.g. 0.5,0.75,1.0,1.25,1.5,2.0)")
    p_mp.set_defaults(func=cmd_mealplan)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
