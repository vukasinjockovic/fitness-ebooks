"""Phase 1: MIP-based cookbook generation using PuLP."""

from __future__ import annotations

import math
from collections import Counter

from pulp import (
    LpProblem, LpMaximize, LpVariable, lpSum,
    PULP_CBC_CMD, LpStatusOptimal,
)

from models import (
    CookbookInput, Cookbook, CookbookGroup, Recipe,
    GroupInput, GlobalConstraints,
)
from candidate_pool import get_candidates


def validate_total_recipe_count(
    groups: list[GroupInput],
    gc: GlobalConstraints,
) -> list[str]:
    """Validate that sum of group counts is within min/max total recipes.

    Returns a list of warning strings. Empty list means valid.
    """
    warnings: list[str] = []
    total = sum(g.count for g in groups)

    if total < gc.min_total_recipes:
        warnings.append(
            f"Total recipe count ({total}) is below minimum ({gc.min_total_recipes}). "
            f"Consider increasing group counts."
        )

    if total > gc.max_total_recipes:
        warnings.append(
            f"Total recipe count ({total}) exceeds maximum ({gc.max_total_recipes}). "
            f"Consider reducing group counts."
        )

    return warnings


def _solve_group(
    candidates: list[Recipe],
    count: int,
    calorie_range: tuple[int, int],
    protein_min: int,
    protein_variety: bool,
    preferred_cuisines: list[str],
) -> tuple[list[Recipe], str, list[str]]:
    """Select exactly `count` recipes from candidates via MIP.

    Returns (selected_recipes, solver_status, relaxations_applied).
    """
    if len(candidates) <= count:
        return candidates, "all_candidates_used", ["pool_smaller_than_count"]

    relaxations = []

    # Try with full constraints, then progressively relax
    for attempt in range(4):
        prob = LpProblem(f"CookbookGroup_attempt{attempt}", LpMaximize)

        # Binary variables
        x = {}
        for i, r in enumerate(candidates):
            x[i] = LpVariable(f"x_{i}", cat="Binary")

        # Objective: maximize quality + cuisine bonus
        obj_terms = []
        for i, r in enumerate(candidates):
            score = r.quality_score
            # Cuisine bonus: +5 for each preferred cuisine match
            if preferred_cuisines and r.normalized_cuisines:
                cuisine_bonus = sum(
                    5 for c in r.normalized_cuisines if c in preferred_cuisines
                )
                score += cuisine_bonus
            obj_terms.append(score * x[i])
        prob += lpSum(obj_terms)

        # Constraint: select exactly `count` recipes
        prob += lpSum(x[i] for i in range(len(candidates))) == count

        # Protein variety constraint (attempts 0, 1)
        if protein_variety and attempt < 2:
            max_per_protein = math.ceil(count / 3)
            protein_groups: dict[str, list[int]] = {}
            for i, r in enumerate(candidates):
                p = r.primary_protein or "Unknown"
                protein_groups.setdefault(p, []).append(i)

            for p, indices in protein_groups.items():
                if len(indices) > max_per_protein:
                    prob += lpSum(x[i] for i in indices) <= max_per_protein

        # Calorie spread constraint (attempts 0, 1, 2)
        if attempt < 3:
            cal_center = (calorie_range[0] + calorie_range[1]) / 2
            if attempt == 0:
                tolerance = 0.15  # ±15% of center
            elif attempt == 1:
                tolerance = 0.25
                relaxations.append("widened_calorie_tolerance_to_25pct")
            else:
                tolerance = 0.40
                relaxations.append("widened_calorie_tolerance_to_40pct")

            avg_cal_lower = cal_center * (1 - tolerance)
            avg_cal_upper = cal_center * (1 + tolerance)

            # Σ cal[r] * x[r] / count within range
            # => Σ cal[r] * x[r] between count * lower and count * upper
            total_cal = lpSum(candidates[i].calories * x[i] for i in range(len(candidates)))
            prob += total_cal >= count * avg_cal_lower
            prob += total_cal <= count * avg_cal_upper
        else:
            relaxations.append("removed_calorie_spread_constraint")

        # Solve
        solver = PULP_CBC_CMD(msg=False, timeLimit=10)
        prob.solve(solver)

        if prob.status == LpStatusOptimal:
            selected = [
                candidates[i] for i in range(len(candidates))
                if x[i].varValue is not None and x[i].varValue > 0.5
            ]
            status = "Optimal" if attempt == 0 else f"Optimal_after_{attempt}_relaxations"
            return selected, status, relaxations

        # Record what we'll relax next
        if attempt == 0:
            relaxations.append("relaxing_protein_variety")
        elif attempt == 1:
            relaxations.append("relaxing_calorie_spread")
        elif attempt == 2:
            relaxations.append("removing_all_soft_constraints")

    # Final fallback: just take top `count` by quality score
    fallback = sorted(candidates, key=lambda r: r.quality_score, reverse=True)[:count]
    relaxations.append("fallback_to_greedy_selection")
    return fallback, "Infeasible_greedy_fallback", relaxations


def generate_cookbook(input: CookbookInput, conn) -> Cookbook:
    """Generate a cookbook by selecting optimal recipes for each group.

    Args:
        input: CookbookInput with group specs and constraints.
        conn: psycopg2 connection to the database.

    Returns:
        Cookbook with selected recipes per group.
    """
    cookbook = Cookbook(name=input.name)
    gc = input.global_constraints

    # Validate min/max total recipe counts
    count_warnings = validate_total_recipe_count(input.groups, gc)
    for w in count_warnings:
        print(f"  WARNING: {w}")

    all_relaxations = []

    # Cross-group deduplication: accumulate selected recipe IDs so the
    # same recipe cannot appear in multiple groups (Bug 3 fix).
    selected_ids: set[int] = set()

    for group_input in input.groups:
        print(f"\n  Group: {group_input.name} ({group_input.meal_type})")
        print(f"    Target: {group_input.count} recipes, "
              f"calories {group_input.calorie_range[0]}-{group_input.calorie_range[1]}, "
              f"protein >= {group_input.protein_min}g")

        # Get candidate pool, excluding already-selected recipe IDs
        candidates = get_candidates(
            conn=conn,
            meal_type=group_input.meal_type,
            calorie_range=group_input.calorie_range,
            protein_min=group_input.protein_min,
            dietary=gc.dietary,
            excluded_ingredients=gc.excluded_ingredients,
            preferred_cuisines=gc.preferred_cuisines,
            max_prep_time=gc.max_prep_time,
            min_quality_score=gc.min_quality_score,
            require_image=gc.require_image,
            limit=200,
            exclude_ids=selected_ids,
        )
        print(f"    Candidate pool: {len(candidates)} recipes")

        if not candidates:
            print(f"    WARNING: No candidates found for {group_input.name}")
            group = CookbookGroup(name=group_input.name, meal_type=group_input.meal_type)
            cookbook.groups.append(group)
            all_relaxations.append(f"{group_input.name}: no_candidates")
            continue

        # MIP selection
        selected, status, relaxations = _solve_group(
            candidates=candidates,
            count=group_input.count,
            calorie_range=group_input.calorie_range,
            protein_min=group_input.protein_min,
            protein_variety=gc.protein_variety,
            preferred_cuisines=gc.preferred_cuisines,
        )

        print(f"    Selected: {len(selected)} recipes (solver: {status})")
        if relaxations:
            print(f"    Relaxations: {', '.join(relaxations)}")
            all_relaxations.extend(f"{group_input.name}: {r}" for r in relaxations)

        # Print variety info
        proteins = Counter(r.primary_protein or "Unknown" for r in selected)
        cuisines = set()
        for r in selected:
            cuisines.update(r.normalized_cuisines or [])

        print(f"    Protein variety: {dict(proteins)}")
        print(f"    Cuisines: {len(cuisines)} unique ({', '.join(sorted(cuisines)[:5])}...)")
        avg_cal = sum(r.calories for r in selected) / len(selected) if selected else 0
        avg_pro = sum(r.protein for r in selected) / len(selected) if selected else 0
        print(f"    Avg calories: {avg_cal:.0f}, Avg protein: {avg_pro:.1f}g")

        # Accumulate selected IDs for cross-group deduplication
        for r in selected:
            selected_ids.add(r.id)

        group = CookbookGroup(
            name=group_input.name,
            meal_type=group_input.meal_type,
            recipes=selected,
        )
        cookbook.groups.append(group)

    cookbook.solver_status = "Optimal" if not all_relaxations else "Relaxed"
    cookbook.relaxations = all_relaxations
    cookbook.compute_stats()

    return cookbook
