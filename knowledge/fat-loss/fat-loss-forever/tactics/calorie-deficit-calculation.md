# Calorie Deficit Calculation
**Type:** tactic
**Status:** solid
**Last Updated:** 2026-02-03
**Aliases:** Deficit Setting, Calorie Target, TDEE Calculation

## Summary

A systematic approach to determining how many calories to eat for fat loss, based on calculating Total Daily Energy Expenditure (TDEE), selecting an appropriate rate of loss, and subtracting the required deficit. The book provides multiplication factors for different populations to simplify the calculation while accounting for body composition changes.

## Step-by-Step Process

### Step 1: Determine Maintenance Calories (TDEE)

**Method A: Food Tracking (Preferred)**
1. Track intake accurately for 2+ weeks
2. Track daily weight
3. If weight stable → that's your maintenance
— Source: Fat Loss Forever, Ch.6

**Method B: Estimation Equations**

Common formulas mentioned:
- Mifflin-St Jeor
- Harris-Benedict
- Katch-McArdle (if body fat known)

Then multiply by activity factor:
- Sedentary: 1.2
- Light activity: 1.375
- Moderate: 1.55
- Very active: 1.725
- Extremely active: 1.9
— Source: Fat Loss Forever, Ch.6

### Step 2: Select Rate of Weight Loss

**Recommended rates by body fat:**

| Population | Rate/Week |
|------------|-----------|
| Obese (>27% M / >38% F) | 0.7-1.0% BW |
| Overweight (>20% M / >33% F) | 0.5-0.7% BW |
| Normal (10-19% M / 21-33% F) | 0.5-0.7% BW |
| Lean (<10% M / <21% F) | 0.2-0.5% BW |
— Source: Fat Loss Forever, Ch.6

**Key principle:** Leaner = slower loss to preserve muscle

### Step 3: Calculate Required Deficit

The book provides multiplication factors (kcal/kg of weight loss):

| Population | No Training | With RT | HP + RT |
|------------|-------------|---------|---------|
| Obese | 1024 | 1024-1120 | 930 |
| Overweight | 930 | 1024 | 930 |
| Normal | 834 | 930 | 834 |
| Lean | <645 | 740 | 740 |

*HP = High Protein (>1.6g/kg), RT = Resistance Training*
— Source: Fat Loss Forever, Ch.6

**Formula:**
```
Daily Deficit = (Target weekly loss in kg) × (Factor from table)
```

### Step 4: Subtract Deficit from Maintenance

```
Diet Calories = Maintenance Calories - Daily Deficit
```
— Source: Fat Loss Forever, Ch.6

## Worked Example

**100kg male, normal body fat, doing RT + high protein:**

1. Maintenance: 2800 kcal (tracked)
2. Target loss: 0.6% BW/week = 0.6 kg/week
3. Factor (normal, RT, HP): 930 kcal/kg
4. Deficit: 0.6 × 930 = 558 kcal/day
5. Diet calories: 2800 - 558 = **2242 kcal/day**
— Source: Fat Loss Forever, Ch.6

## Important Caveats

### Calculations Are Estimates

> "We want to emphasize again that metabolism is not as clean cut as this math makes it out to be. We're just taking our best estimates... These are just estimates to get us close; they aren't meant to be taken as 100% gospel."
— Ch. 6

### Adjustment Is Expected

- Track progress over 2-4 weeks
- If not losing as expected → reduce calories
- If losing too fast → increase calories
- Metabolic adaptation will require ongoing adjustments
— Source: Fat Loss Forever, Ch.6, Ch.10

### Minimum Calories

The book doesn't specify a hard floor but implies:
- Don't go so low that adherence suffers
- Maintain adequate protein intake
- Preserve training performance
— Source: Fat Loss Forever, Ch.6

## Key Quotes

> "If you haven't noticed, telling people to 'be less fat' doesn't seem to be having the desired effect."
— Ch. 6

> "Knowing the importance of energy balance is different than understanding how to implement that knowledge."
— Ch. 6

## Sources in Collection

| Book | Author | How It's Used | Citation |
|------|--------|---------------|----------|
| Fat Loss Forever | Norton & Baker | Primary method | Ch.6 |
| Muscle & Strength Pyramid Nutrition | Helms et al. | Similar approach | - |

## Related Entities

- [Energy Balance](../concepts/energy-balance.md) - Foundation
- [Macro Distribution](macro-distribution.md) - Next step
- [Progress Monitoring](progress-monitoring.md) - Adjustment basis
- [Fat Loss Forever Pyramid](../frameworks/fat-loss-forever-pyramid.md) - Priority context
