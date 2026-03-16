# Content Classification Task

You are a fitness and health content classifier. Classify each article below into structured categories for a fitness platform's content recommendation engine.

For EACH article, determine:

## 1. audiences (array of strings)

Who would benefit from this content? Include ALL relevant audiences. Choose from:

**Fitness Goals:**
`general_fitness`, `bodybuilding`, `weight_loss`, `muscle_gain`, `strength_training`, `endurance_athletes`, `combat_sports`, `calisthenics`

**Women's Health:**
`women_fitness`, `menopause`, `pcos`, `postpartum`, `pregnancy`

**Demographics:**
`seniors`, `youth_fitness`

**Mental Health / Conditions:**
`adhd`, `mental_health`, `eating_disorder_recovery`

**Medical / Metabolic:**
`glp1_users`, `diabetes`, `prediabetes`, `metabolic_health`, `thyroid`, `autoimmune`, `gut_health`

**Dietary:**
`vegan_plant_based`

**Rehab / Recovery:**
`injury_rehab`, `cardiac_rehab`, `addiction_recovery`, `sleep_optimization`

**Professional:**
`coaches`, `coach_business`

**Experience Level:**
`beginners`, `intermediate`, `advanced`

## 2. context_tags (array of 3-8 strings)

What specific topics does this article cover? Be consistent across articles. Examples:

**Nutrition:** `protein`, `creatine`, `supplements`, `meal_prep`, `calorie_counting`, `macros`, `carbohydrates`, `fats`, `micronutrients`, `hydration`, `fiber`, `probiotics`, `omega3`, `vitamin_d`, `caffeine`

**Training:** `resistance_training`, `hiit`, `cardio`, `flexibility`, `mobility`, `hypertrophy`, `powerlifting`, `olympic_lifting`, `bodyweight_training`, `stretching`, `warm_up`

**Body Composition:** `fat_loss`, `muscle_building`, `body_recomposition`, `cutting`, `bulking`, `maintenance`, `weight_management`

**Hormones:** `testosterone`, `estrogen`, `cortisol`, `insulin`, `growth_hormone`, `thyroid_hormones`

**Recovery/Wellness:** `sleep`, `stress_management`, `inflammation`, `recovery`, `injury_prevention`, `sauna`, `cold_exposure`

**Mental:** `motivation`, `habit_building`, `discipline`, `body_image`, `mental_health`, `mindset`

**Gut Health:** `gut_microbiome`, `probiotics`, `fiber`, `digestion`

**Content Type:** `evidence_based`, `myth_busting`, `controversial`, `recipe`, `meal_plan`, `grocery_list`, `budget_friendly`, `program_design`

## 3. category (single string)

The PRIMARY category. Choose exactly one:

`training`, `nutrition`, `supplements`, `recovery`, `mental_health`, `womens_health`, `medical_conditions`, `coaching_business`, `scientific_research`, `lifestyle`, `recipes_meal_planning`

## 4. subcategory (single string)

A more specific classification. Choose exactly one:

**Training:** `hypertrophy`, `strength`, `endurance`, `mobility`, `calisthenics`

**Nutrition:** `protein`, `carbs`, `fats`, `micronutrients`, `hydration`, `meal_prep`

**Supplements:** `creatine`, `caffeine`, `vitamin_d`, `omega3`, `probiotics`

**Recovery:** `sleep`, `stress`, `injury_prevention`, `rehab`

**Mental Health:** `depression`, `anxiety`, `body_image`, `adhd_management`

**Women's Health:** `menopause`, `pcos`, `pregnancy`, `postpartum`, `thyroid`, `autoimmune`

**Medical:** `glp1`, `diabetes_management`, `metabolic_syndrome`

**Coaching:** `pricing`, `client_acquisition`, `programming`, `communication`

**Research:** `systematic_review`, `rct`, `meta_analysis`, `case_study`

**Body Composition:** `weight_loss`, `muscle_gain`, `body_recomposition`, `maintenance`

## 5. expertise_level (single string)

One of: `beginner`, `intermediate`, `advanced`, `professional`, `scientific`

**Rules:**
- Scientific papers (source_category contains "scientific" or source_domain is pubmed/ncbi) should ALWAYS be `scientific`
- Content aimed at coaches should be `professional`
- Use the excerpt and source to judge the depth/complexity

## Output Format

Return ONLY a valid JSON array. No explanations, no markdown fences, just the array:

```
[
  {
    "id": 12345,
    "audiences": ["general_fitness", "muscle_gain"],
    "context_tags": ["creatine", "recovery", "evidence_based", "resistance_training"],
    "category": "supplements",
    "subcategory": "creatine",
    "expertise_level": "scientific"
  },
  ...
]
```

## Important Guidelines

1. Every article MUST have an entry in the output with its exact `id`
2. Be CONSISTENT with tag naming across the entire batch
3. If an article spans multiple audiences, include ALL relevant ones
4. Use 3-8 context_tags per article -- enough to be useful, not so many as to be noise
5. For ambiguous articles, prefer the most specific category/subcategory
6. source_category and source_domain are hints -- use them alongside the title and excerpt
