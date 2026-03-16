Classify each article. Return ONLY a JSON array.

Per article: {"id":N,"audiences":[],"context_tags":[],"category":"","subcategory":"","expertise_level":""}

audiences (pick all relevant):
general_fitness,bodybuilding,weight_loss,muscle_gain,strength_training,endurance_athletes,combat_sports,calisthenics,women_fitness,menopause,pcos,postpartum,pregnancy,seniors,youth_fitness,adhd,mental_health,eating_disorder_recovery,glp1_users,diabetes,prediabetes,metabolic_health,thyroid,autoimmune,gut_health,vegan_plant_based,injury_rehab,cardiac_rehab,addiction_recovery,sleep_optimization,coaches,coach_business,beginners,intermediate,advanced

context_tags (3-8): protein,creatine,supplements,meal_prep,macros,carbs,fats,micronutrients,hydration,resistance_training,hiit,cardio,mobility,hypertrophy,fat_loss,muscle_building,body_recomposition,cutting,bulking,testosterone,estrogen,cortisol,insulin,sleep,stress_management,inflammation,recovery,injury_prevention,motivation,habit_building,body_image,gut_microbiome,evidence_based,myth_busting,recipe,meal_plan,program_design

category (one): training,nutrition,supplements,recovery,mental_health,womens_health,medical_conditions,coaching_business,scientific_research,lifestyle,recipes_meal_planning

subcategory (one): hypertrophy,strength,endurance,mobility,calisthenics,protein,carbs,fats,micronutrients,meal_prep,creatine,caffeine,vitamin_d,omega3,sleep,stress,injury_prevention,rehab,depression,anxiety,body_image,adhd_management,menopause,pcos,pregnancy,postpartum,thyroid,autoimmune,glp1,diabetes_management,metabolic_syndrome,pricing,client_acquisition,programming,systematic_review,rct,meta_analysis,weight_loss,muscle_gain,body_recomposition

expertise_level (one): beginner,intermediate,advanced,professional,scientific

Rules:
- pubmed/scientific papers = scientific expertise_level
- Coach/business content = professional expertise_level
- subcategory MUST always be filled — pick the most specific match
- audiences: include ALL relevant, not just the primary one
- context_tags: 3-8 specific topic tags per article
