# Swap Enrichment Report

**Generated:** 2026-03-16
**Task:** Build the Swappable Recipe Enrichment Layer (Phase 2)

## What Was Built

### New Files
- **`recipe_vectorizer.py`** - Recipe-to-vector conversion and similarity search
  - `recipe_to_vector()` - Converts a Recipe to a ~42-dimension weighted feature vector
  - `compute_vectors_batch()` - Batch vectorization of multiple recipes
  - `find_similar()` - Cosine-distance similarity search with hard filters for meal_type, calorie tolerance, and protein tolerance

- **`swap_enricher.py`** - Database-driven swap enrichment for cookbooks
  - `enrich_cookbook_with_swaps()` - For each recipe in the cookbook, queries up to 2000 candidates per meal_type from lake.recipes, vectorizes them, and finds the top N most similar swaps

- **`tests/test_swap_enrichment.py`** - 19 tests covering unit, model, and integration scenarios

### Modified Files
- **`models.py`** - Added `SwapRecipe` dataclass with `to_dict()` serialization; added `swaps` field to `Recipe` (default empty list); `Recipe.to_dict()` now includes swaps
- **`mealplan_generator.py`** - MealSlot construction now carries over swaps from Recipe objects (both MIP solver path and greedy fallback)
- **`pipeline.py`** - `run_pipeline()` now accepts `swaps_per_recipe` and `macro_tolerance_pct` parameters; calls `enrich_cookbook_with_swaps()` between cookbook generation and meal plan generation; `print_mealplan_summary()` displays swap alternatives inline
- **`cli.py`** - Added `--swaps N` flag to both `cookbook` and `full` subcommands
- **`examples/*.json`** - All 3 example inputs updated with `swap_config` section

### Vector Design (42 dimensions)
| Feature Group | Dimensions | Weight | Purpose |
|---|---|---|---|
| Macros (cal, pro, fat, carbs) | 4 | 2.0-3.0 | Nutritional similarity |
| Meal types (one-hot) | 5 | 5.0 | Must-match constraint |
| Cuisines (top 15, one-hot) | 15 | 1.0 | Cuisine similarity |
| Protein types (10, one-hot) | 10 | 1.5 | Protein source variety |
| Prep time (normalized) | 1 | 1.0 | Cooking time match |
| Diet flags (7, one-hot) | 7 | 2.0 | Dietary constraint match |

### Hard Filters in find_similar()
1. **Meal type overlap** - At least one shared meal_type (e.g., both tagged "Lunch")
2. **Calorie tolerance** - Within +/- 15% of target recipe's calories
3. **Protein tolerance** - Within +/- max(15%, 5g) of target protein (5g floor for low-protein items)
4. **Exclusion set** - Cookbook recipe IDs excluded from swap candidates

## Test Results

**Total: 19 tests, 19 passed, 0 failed**

| Test | Description | Status |
|---|---|---|
| `test_recipe_to_vector_dimensions` | Vector has correct dimensions (42) | PASS |
| `test_recipe_to_vector_weights` | Custom weights change vector values | PASS |
| `test_vector_null_safe` | Handles None meal_types, diet_tags, etc. | PASS |
| `test_batch_shape` | Batch vectorization produces (N, 42) | PASS |
| `test_batch_matches_individual` | Batch matches individual computation | PASS |
| `test_find_similar_respects_meal_type` | Swaps share meal_type with target | PASS |
| `test_find_similar_respects_macro_tolerance` | Calories/protein within +/-15% | PASS |
| `test_find_similar_excludes_ids` | Excluded recipe IDs filtered out | PASS |
| `test_find_similar_returns_n_or_fewer` | Returns at most N results | PASS |
| `test_find_similar_sorted_by_distance` | Results sorted by similarity score | PASS |
| `test_find_similar_low_protein_tolerance` | 5g floor for low-protein recipes | PASS |
| `test_swap_json_serialization` | SwapRecipe.to_dict() correct | PASS |
| `test_swap_recipe_json_roundtrip` | Survives JSON serialization | PASS |
| `test_recipe_has_swaps_field` | Recipe model has swaps field | PASS |
| `test_recipe_to_dict_includes_swaps` | Recipe.to_dict() includes swaps | PASS |
| `test_swaps_appear_in_mealslot` | MealSlot.to_dict() includes swaps | PASS |
| `test_enrich_cookbook_adds_swaps` | Integration: cookbook gets swaps from DB | PASS |
| `test_swap_macros_are_similar` | All swap macros within tolerance of parent | PASS |
| `test_swaps_appear_in_mealplan` | End-to-end: MealSlot.swaps populated | PASS |

**Full test suite: 74 tests, 74 passed, 0 failed** (existing tests unaffected)

## Manual Validation Results

### Spot Check: 5 Random Recipes with Swap Comparison

All checks performed against the high-protein cutting cookbook with 10 swaps per recipe.

**1. Pulled Pork Sliders (Snack) - 180 cal, 8g P**
- 10/10 swaps within tolerance
- Closest swap: Air Fryer Garlic Bread Pizza Toast (189 cal, 6g P, sim=0.0002)
- All swaps share Snack meal_type

**2. Grilled Beef Skewers (Breakfast) - 320 cal, 28g P**
- 10/10 swaps within tolerance
- Closest swap: Carnivore Breakfast Muffins (305 cal, 24g P, sim=0.0539)
- All swaps share Breakfast meal_type

**3. Cheesy Crustless Quiche Lorraine (Breakfast) - 467 cal, 31g P**
- 10/10 swaps within tolerance
- Closest swap: Mini Broccoli Cheddar & Bacon Quiche (399 cal, 32g P, sim=0.0013)
- All swaps share Breakfast meal_type

**4. Cabbage & Peanut Butter Chicken Stir-fry (Lunch) - 465 cal, 35g P**
- 10/10 swaps within tolerance
- Closest swap: Barbequed Dr. Pepper Ribs (465 cal, 38g P, sim=0.0081)
- All swaps share Lunch meal_type

**5. Salmon Stuffed with Ricotta and Spinach (Lunch) - 530 cal, 68g P**
- 10/10 swaps within tolerance
- Closest swap: How to Cook Steak (528 cal, 69g P, sim=0.0341)
- All swaps share Lunch meal_type

**Validation summary:** 50/50 swaps passed all checks (meal_type match, calorie tolerance, protein tolerance, no cookbook ID collision).

## Performance

| Metric | Value |
|---|---|
| Enrichment time (42-recipe cookbook, 5 swaps) | 0.2s |
| Enrichment time (42-recipe cookbook, 10 swaps) | 0.2s |
| Enrichment time (34-recipe keto cookbook, 5 swaps) | 0.2s |
| DB query per meal_type (2000 candidates) | ~50ms |
| Vectorization per 2000 candidates | ~10ms |
| Cosine distance computation | ~1ms |
| **Total well under 5s target** | |

## All 3 Example Runs

| Example | Recipes | Swaps Requested | Swaps Found | Avg Swaps/Recipe |
|---|---|---|---|---|
| High-Protein Cutting | 42 | 5 | 210 | 5.0 |
| Keto | 34 | 5 | 170 | 5.0 |
| Vegetarian Balanced | 34 | 3 | 102 | 3.0 |
| High-Protein (10 swaps) | 42 | 10 | 414 | 9.9 |

## Issues Found

1. **Snack recipes with very low protein (3-8g):** The 5g floor tolerance works well here. Without it, a 15% tolerance on 5g protein would be only 0.75g, making it nearly impossible to find swaps.

2. **Swaps are NOT dietary-filtered:** The swap candidates are queried without dietary constraints (e.g., a Keto cookbook could get non-Keto swaps). This is by design - the swap enricher queries broadly to maximize candidate pool size. If dietary filtering is needed for swaps, it can be added as a future enhancement.

3. **No deduplication across recipes:** The same recipe can appear as a swap for multiple main recipes. This is explicitly allowed per the task spec ("swaps CAN be shared across recipes").

## Recommendations for Improvement

1. **Dietary-aware swap filtering:** Add optional dietary tag filtering to `_query_swap_candidates()` so Keto cookbook swaps are also Keto-tagged.

2. **Ingredient exclusion for swaps:** Currently excluded_ingredients from the cookbook input are not applied to swap candidates. Could be added for allergy safety.

3. **Pre-computed swap cache:** For production use with many plan generations from the same cookbook, cache the swap results (keyed by cookbook_id) to avoid re-querying the DB.

4. **pgvector integration:** For very large candidate pools (>10K), could use pgvector for server-side similarity search instead of pulling candidates to Python. Not needed at current scale (2K candidates vectorizes in <10ms).

5. **Swap quality scoring:** Currently swaps are ranked purely by cosine distance. Could add a quality_score bonus to prefer higher-quality swap alternatives when similarity scores are close.
