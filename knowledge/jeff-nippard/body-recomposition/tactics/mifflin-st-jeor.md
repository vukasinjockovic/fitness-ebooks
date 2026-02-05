# Mifflin St. Jeor Formula

**Type:** tactic
**Status:** solid
**Last Updated:** 2026-02-03
**Aliases:** BMR formula, basal metabolic rate calculation, Mifflin-St Jeor equation

## Summary

The Mifflin St. Jeor formula is a validated equation for estimating Basal Metabolic Rate (BMR) based on gender, age, height, and weight. It's the foundation for calculating maintenance calories when combined with an activity multiplier.

## The Formula

### For Men:
```
BMR = 10 × weight(kg) + 6.25 × height(cm) − 5 × age(years) + 5
```

### For Women:
```
BMR = 10 × weight(kg) + 6.25 × height(cm) − 5 × age(years) − 161
```

### Quick Estimate (Rough):
```
BMR ≈ 10 × weight(lbs)
```

— Source: Body Recomposition, Ch.4-5

## Example Calculation

**Male, 30 years old, 175 cm (5'9"), 75 kg (165 lbs):**

```
BMR = 10 × 75 + 6.25 × 175 − 5 × 30 + 5
BMR = 750 + 1093.75 − 150 + 5
BMR = 1,699 calories
```

**Quick estimate:** 165 × 10 = 1,650 calories (close!)

## From BMR to Maintenance

BMR is just sitting on a couch breathing. To get maintenance calories (TDEE):

```
Maintenance = BMR × Activity Multiplier
```

See [Activity Multipliers](activity-multipliers.md) for values.

## Limitations

The formula doesn't account for:
- Body composition (lean mass vs fat mass)
- Genetics
- Dietary history (chronic dieting)
- Gut microbiome
- Overall health/diseased states
- Prescription medications
- Ambient/body temperature
- Hormonal factors
- Sympathetic nervous system activity

> "Since there are a plethora of factors that can impact your individual calorie requirements, it's important to recognize that your calorie intake may not be comparable to someone else's, even if you are the same weight and have the same goals."

## When to Use

**Formula method (immediate result):**
- Quick starting point
- New to tracking
- Need a number to begin

**Guess-and-check method (more accurate):**
- Track intake + weight for 2 weeks
- Calculate based on actual response
- Better accounts for individual variation

## Key Quotes

> "For those of you who like math and objective numbers, we recommend using the Mifflin St. Jeor formula to calculate your BMR."

> "This formula is very good, but not perfect."

## Sources in Collection

| Book | Author | How It's Used | Citation |
|------|--------|---------------|----------|
| Body Recomposition | Nippard & Barakat | BMR calculation | Ch.4, Ch.5 |

## Related Entities

- [TDEE Framework](../frameworks/tdee-framework.md) - BMR is one component
- [Activity Multipliers](activity-multipliers.md) - Next step after BMR
- [Calorie Setting Framework](../frameworks/calorie-setting-framework.md) - Apply % to maintenance

## Open Questions

- Accuracy compared to indirect calorimetry?
- Better formulas for athletes with high lean mass?
