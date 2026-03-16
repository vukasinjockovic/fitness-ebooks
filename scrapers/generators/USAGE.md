# Cookbook & Meal Plan Generator

Generates macro-optimized cookbooks and weekly meal plans from the recipe data lake (900K+ recipes in `lake.recipes`).

## Requirements

- Python 3.12+
- PostgreSQL access: `gymzillatribe_dev` on port 5433
- Dependencies: `pip install psycopg2-binary numpy scipy`

## Quick Start

```bash
cd /home/vuk/fitness-books/scrapers/generators

# Generate cookbook + meal plan + swaps (full pipeline)
python3 cli.py full examples/high_protein_cutting.json --swaps 5

# Generate cookbook only
python3 cli.py cookbook examples/keto.json --swaps 5

# Generate meal plan from an existing cookbook
python3 cli.py mealplan output/cookbook_keto_cookbook_20260316.json --weeks 2 --daily-cal 1800 --protein 120
```

Output goes to `output/` by default (override with `-o DIR`).

## Commands

| Command | Description |
|---------|-------------|
| `full` | Cookbook + swap enrichment + meal plan (end-to-end) |
| `cookbook` | Cookbook generation + optional swap enrichment |
| `mealplan` | Meal plan from a previously generated cookbook JSON |

### `full` options

| Flag | Default | Description |
|------|---------|-------------|
| `--swaps N` | 0 | Swap alternatives per recipe (5 recommended) |
| `--weeks N` | from input | Override number of weeks |
| `--daily-cal N` | from input | Override daily calorie target |
| `--protein N` | from input | Override daily protein target (g) |
| `--multipliers` | from input | Serving multipliers (e.g., `0.5,0.75,1.0,1.25,1.5,2.0`) |

## Input JSON Format

```json
{
  "name": "My Cookbook",
  "groups": [
    {
      "name": "Breakfast",
      "meal_type": "Breakfast",
      "count": 10,
      "calorie_range": [300, 500],
      "protein_min": 25
    },
    {
      "name": "Lunch",
      "meal_type": "Lunch",
      "count": 12,
      "calorie_range": [400, 650],
      "protein_min": 30
    },
    {
      "name": "Dinner",
      "meal_type": "Dinner",
      "count": 12,
      "calorie_range": [450, 750],
      "protein_min": 35
    },
    {
      "name": "Snacks",
      "meal_type": "Snack",
      "count": 8,
      "calorie_range": [100, 250],
      "protein_min": 8
    }
  ],
  "global_constraints": {
    "dietary": [],
    "excluded_ingredients": [],
    "preferred_cuisines": [],
    "max_prep_time": 45,
    "min_quality_score": 60,
    "require_image": false,
    "protein_variety": true,
    "min_total_recipes": 20,
    "max_total_recipes": 100
  },
  "mealplan": {
    "weeks": 2,
    "daily_calories": 2000,
    "daily_calories_tolerance": 150,
    "daily_protein": 180,
    "daily_protein_tolerance": 20,
    "daily_carbs": 150,
    "daily_carbs_tolerance": 25,
    "daily_fat": 65,
    "daily_fat_tolerance": 15,
    "meal_calorie_split": {
      "Breakfast": 0.20,
      "Lunch": 0.30,
      "Dinner": 0.35,
      "Snacks": 0.15
    },
    "serving_multipliers": [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
  },
  "swap_config": {
    "enabled": true,
    "swaps_per_recipe": 5,
    "macro_tolerance_pct": 0.15
  }
}
```

### Field Reference

#### `groups[]`

Each group becomes a section in the cookbook (e.g., Breakfast, Lunch).

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Display name for the group |
| `meal_type` | string | One of: `Breakfast`, `Lunch`, `Dinner`, `Snack` |
| `count` | int | Number of recipes to select |
| `calorie_range` | [min, max] | Per-serving calorie range |
| `protein_min` | int | Minimum protein per serving (grams) |

#### `global_constraints`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `dietary` | string[] | `[]` | Diet filters applied to all recipes. Available tags: `Vegan`, `Vegetarian`, `Keto`, `Low-Carb`, `Low-Fat`, `Low-Calorie`, `High-Protein`, `Gluten-Free`, `Dairy-Free`, `Kosher`, `High-Fibre` |
| `excluded_ingredients` | string[] | `[]` | Ingredients to exclude |
| `preferred_cuisines` | string[] | `[]` | Preferred cuisine types |
| `max_prep_time` | int | 60 | Maximum total cook time in minutes |
| `min_quality_score` | int | 50 | Minimum recipe quality score (0-100) |
| `require_image` | bool | false | Only select recipes with images |
| `protein_variety` | bool | true | Enforce no single protein type > 1/3 of group |
| `min_total_recipes` | int | 20 | Minimum total recipes across all groups |
| `max_total_recipes` | int | 100 | Maximum total recipes |

#### `mealplan`

| Field | Type | Description |
|-------|------|-------------|
| `weeks` | int | Number of weeks to plan |
| `daily_calories` | int | Daily calorie target |
| `daily_calories_tolerance` | int | Acceptable deviation (+/-) |
| `daily_protein` | int | Daily protein target (g) |
| `daily_protein_tolerance` | int | Acceptable deviation (+/-) |
| `daily_carbs` | int | Daily carb target (g) |
| `daily_carbs_tolerance` | int | Acceptable deviation (+/-) |
| `daily_fat` | int | Daily fat target (g) |
| `daily_fat_tolerance` | int | Acceptable deviation (+/-) |
| `meal_calorie_split` | object | Fraction of daily cals per meal type (must sum to 1.0) |
| `serving_multipliers` | float[] | Allowed serving sizes the solver can use |

#### `swap_config`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | true | Enable swap enrichment |
| `swaps_per_recipe` | int | 5 | Alternatives per recipe |
| `macro_tolerance_pct` | float | 0.15 | Max calorie/protein deviation for swaps (+/-15%) |

## Examples

### High-Protein Cutting (2000 cal, 180g protein)
```bash
python3 cli.py full examples/high_protein_cutting.json --swaps 5
```

### Keto (1800 cal, 25g carbs, 140g fat)
```bash
python3 cli.py full examples/keto.json --swaps 5
```

### Vegetarian Balanced (2200 cal)
```bash
python3 cli.py full examples/vegetarian_balanced.json --swaps 5
```

### Busy Person Quick Meals (20 min max prep)
```bash
python3 cli.py full examples/busy_person_quick.json --swaps 5
```

### Vegan + Keto Edge Case
```bash
python3 cli.py full examples/vegan_keto_edge_case.json --swaps 5
```

## Pipeline Architecture

```
Input JSON
    |
    v
[1. Cookbook Generator] -- MIP solver selects recipes per group
    |                     - Queries lake.recipes for candidates
    |                     - Enforces calorie/protein/variety constraints
    |                     - Cross-group deduplication (no recipe in 2 groups)
    |                     - Title-level deduplication (no same-name recipes)
    |                     - Progressive relaxation if infeasible
    v
[2. Swap Enricher] -- Finds 5 alternatives per recipe
    |                - Same meal_type, within +/-15% calories & protein
    |                - Respects dietary constraints (Vegan swaps for Vegan cookbook)
    |                - Cosine similarity ranking for best matches
    v
[3. Meal Plan Generator] -- MIP solver assigns recipes to days
    |                      - Adjusts serving multipliers to hit daily macros
    |                      - No recipe repeated in same week
    |                      - Progressive relaxation if needed
    v
Output JSON (cookbook + meal plan)
```

## Output Format

### Cookbook JSON
```json
{
  "name": "High-Protein Cutting Cookbook",
  "generated_at": "2026-03-16T09:00:00",
  "solver_status": "Optimal",
  "groups": [
    {
      "name": "Breakfast",
      "meal_type": "Breakfast",
      "recipes": [
        {
          "id": 123456,
          "title": "Egg White Delight McMuffin Recipe",
          "calories": 411,
          "protein": 28,
          "fat": 15,
          "carbohydrates": 38,
          "total_time": 15,
          "primary_protein": "Eggs",
          "cuisine": "American",
          "quality_score": 100,
          "swaps": [
            {"id": 789, "title": "Scrambled Eggs on Toast", "calories": 466, "protein": 22, ...},
            ...
          ]
        },
        ...
      ]
    }
  ]
}
```

### Meal Plan JSON
```json
{
  "weeks": [
    {
      "week": 1,
      "days": [
        {
          "day": "Monday",
          "meals": [
            {
              "meal_type": "Breakfast",
              "recipe_id": 123456,
              "title": "Egg White Delight McMuffin Recipe",
              "servings": 1.25,
              "calories": 514,
              "protein": 35,
              "swaps": [...]
            }
          ],
          "totals": {"calories": 2008, "protein": 178, "carbs": 148, "fat": 67}
        }
      ]
    }
  ]
}
```

## Data Quality Tools

### Fix Mistagged Diet Tags
```bash
# Dry run (preview changes)
python3 fix_diet_tags.py

# Apply fixes to database
python3 fix_diet_tags.py --apply
```

Identifies recipes incorrectly tagged as Vegan/Vegetarian based on `primary_protein` and title/ingredient keyword analysis.

## Tests

```bash
python3 -m pytest tests/ -v
# 34 tests (18 core + 16 bug fix tests)
```
