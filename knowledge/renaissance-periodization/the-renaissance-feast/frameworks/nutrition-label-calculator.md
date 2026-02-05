# Nutrition Label Calculator

**Type:** Framework
**Status:** Solid
**Source:** The Renaissance Feast, Quick Guides (p. 125)
**Author:** Lori Shaw

## Summary

A simple 2-step calculation method to determine the exact weight of any food needed to hit a specific macro target, using information from nutrition labels.

## The Problem It Solves

You need a specific amount of a macro (e.g., 20g fat) but the food label shows a different serving size (e.g., 2 Tbsp = 16g fat). How much do you actually need to weigh out?

## The Three Numbers You Need

| Variable | Description | Source |
|----------|-------------|--------|
| **A** | Grams of macro you need for your meal | Your diet template |
| **B** | Grams of that macro in one serving | Nutrition label |
| **C** | Weight in grams of one serving | Nutrition label |

## The Two Calculations

### Step 1: Find Your Multiplier
```
D = A ÷ B
```
D is your multiplier - how many "servings worth" of that macro you need.

### Step 2: Calculate Weight Needed
```
Answer = D × C
```
This gives you the grams (by weight) of food to measure out.

## Worked Examples

### Example 1: Almond Butter
**Need:** 22g fat
**Label says:** 2 Tbsp (32g) = 16g fat

```
A = 22 (grams of fat needed)
B = 16 (grams of fat per serving)
C = 32 (grams per serving by weight)

D = 22 ÷ 16 = 1.375 (multiplier)
Answer = 1.375 × 32 = 44g

→ Weigh out 44g of almond butter
```

### Example 2: Greek Yogurt (Protein)
**Need:** 30g protein
**Label says:** 1 cup (245g) = 23g protein

```
A = 30 (grams of protein needed)
B = 23 (grams of protein per serving)
C = 245 (grams per serving)

D = 30 ÷ 23 = 1.304 (multiplier)
Answer = 1.304 × 245 = 319g

→ Weigh out 319g of Greek yogurt
```

### Example 3: Brown Rice (Carbs)
**Need:** 45g carbs
**Label says:** 1 cup cooked (195g) = 45g carbs

```
A = 45 (grams of carbs needed)
B = 45 (grams of carbs per serving)
C = 195 (grams per serving)

D = 45 ÷ 45 = 1.0 (multiplier)
Answer = 1.0 × 195 = 195g

→ Weigh out 195g of cooked brown rice
```

## Important Considerations

### Multi-Macro Foods
If a food has multiple countable macros, apply your multiplier (D) to ALL macros:

**Example:** Using D = 1.375 for almond butter
- Fat: 16g × 1.375 = 22g ✓ (your target)
- Protein: 7g × 1.375 = 9.6g (incidental)
- Carbs: 6g × 1.375 = 8.25g (incidental)

### Works for Any Macro
Same formula works for:
- Fat
- Protein
- Carbohydrates
- Fiber (if tracking)

### Cooked vs Raw
Pay attention to whether the label shows cooked or raw weight:
- Pasta: Usually lists dry weight
- Rice: Check if cooked or uncooked
- Meat: Usually raw weight

## Quick Reference Card

```
NUTRITION LABEL CALCULATOR

NEED: _____ g of [P/C/F]        (A)
LABEL SHOWS: _____ g per serving (B)
SERVING WEIGHS: _____ g          (C)

STEP 1: A ÷ B = _____           (D)
STEP 2: D × C = _____ g         (ANSWER)

→ Weigh out the ANSWER in grams
```

## Why This Matters

1. **Precision** - More accurate than eyeballing tablespoons
2. **Flexibility** - Works with any food, any serving size
3. **Template Compliance** - Hit exact macro targets
4. **Education** - Understand food composition better
5. **Scale Dependency** - Requires a food scale (recommended for dieting)

## Cross-References

- [Macro Tracking Recipe Format](../tactics/macro-tracking-recipe-format.md)
- [Healthy Fat Portions](./healthy-fat-portions.md)
- [Healthy Carb Portions](./healthy-carb-portions.md)
- [Five Principles](../../renaissance-woman/frameworks/five-principles-scientific-dieting.md) - Why macro accuracy matters

## Key Quote

> "Use this if the serving size, as listed on the label, isn't quite what you need."
