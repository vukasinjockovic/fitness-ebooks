# Macro-Tracking Recipe Format

**Type:** Tactic
**Status:** Solid
**Source:** The Renaissance Feast, Introduction
**Author:** Lori Shaw

## Summary

A recipe format optimized for macro tracking that lists nutritional information per ingredient, provides multiple portion options with pre-calculated macros, and includes a "Your Changes" column for customizing recipes to individual macro requirements.

## The Format Structure

### Standard Recipe Layout
```
Recipe Name
[Description/Notes]

INGREDIENTS                           YOUR CHANGES
                  protein  net carbs  fat          protein  net carbs  fat
[ingredient 1]    [macros]                         [your adjusted amounts]
[ingredient 2]    [macros]
...

Total recipe      [P]      [C]       [F]
If divided into X equal portions   [per serving]
If divided into Y equal portions   [per serving]

INSTRUCTIONS
• Step-by-step instructions
```

## Key Design Decisions

### 1. Per-Ingredient Macro Listing
Only count-worthy ingredients have macros listed:
- **Included:** Proteins, fats, carb sources
- **Excluded:** Herbs, spices, low-calorie vegetables, condiments in trace amounts

> "I only listed protein, carbs and fats for items that are 'count-worthy' (i.e. if I used chopped cilantro in a recipe, I didn't list the associated macros, because we don't need to count them)."

### 2. Net Carbs (Not Total Carbs)
Uses net carbs throughout for template compatibility:
- Net Carbs = Total Carbs - Fiber
- Matches RP diet template approach

### 3. Multiple Portion Options
Every recipe includes at least 2 portion breakdowns:
- Smaller portions (more servings)
- Larger portions (fewer servings)

### 4. The "Your Changes" Column
Blank space alongside each ingredient for users to:
- Adjust ingredient amounts
- Substitute ingredients
- Calculate personalized macros
- Scale recipe up or down

## Example Application

### Original Recipe
```
Eat Your Greens Chicken Stew

INGREDIENTS                                    YOUR CHANGES
                        protein  net carbs  fat

4 Tbsp EVOO, divided                          60
1.5 lb chicken breasts  144
4 Cups sweet potato                  91

Total recipe            144       91        60
If divided into 8       18        11         8
If divided into 5       29        18        12
```

### Customized for Post-Workout Meal
For a 200lb male needing ~30g protein, 100g carbs, minimal fat:

```
YOUR CHANGES
                        protein  net carbs  fat

2.5 Tbsp EVOO                                38
1.5 lb chicken          144
8 Cups sweet pot                   182

5 servings              29        96         8
over 1.5 Cups rice              (+60 carb)

TOTAL per serving       29        96         8
```

## Practical Guidelines

### Counting Rules
1. Only count macros that "matter" per serving
2. If tamari/soy sauce carbs are inconsequential per serving, skip counting
3. Cheese should be 2% or part-skim variety
4. Always track protein, prioritize fat and carb accuracy

### Substitution Principles
- Swap fats for equivalent fats (oil for oil, nut butter for nut butter)
- Swap proteins within category (fish for fish, poultry for poultry)
- Adjust carb sources for GI needs (white rice vs brown rice)

### Abbreviations
| Code | Meaning |
|------|---------|
| EVOO | Extra virgin olive oil |
| S&P | Salt and pepper, to taste |
| SF | Sugar free |
| WG | Whole grain |

## Benefits of This Format

1. **Transparency** - See exactly where macros come from
2. **Flexibility** - Easy to adjust any ingredient
3. **Precision** - Pre-calculated options reduce math
4. **Education** - Learn macro content of ingredients over time
5. **Accountability** - No hidden calories in vague instructions

## Cross-References

- [Five Principles](../../renaissance-woman/frameworks/five-principles-scientific-dieting.md) - Macro priorities
- [Nutrition Label Calculator](./nutrition-label-calculator.md) - Precise measurement method
- [Healthy Fat Portions](./healthy-fat-portions.md) - Reference for fat servings

## Key Quotes

> "For every recipe, I've included a section to the right where you can adjust ingredients types or amounts to suit your preferences and your required amounts."

> "This way, if you need 20 grams of carbs instead of 30 grams of carbs, you can adjust the ingredient amount(s) to get you there."
