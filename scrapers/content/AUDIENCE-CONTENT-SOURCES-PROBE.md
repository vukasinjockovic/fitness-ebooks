# Audience Content Sources Probe Report
Generated: 2026-03-16

## Executive Summary

Probed 50+ websites across 16 underserved audience segments for GymZilla's fitness/nutrition RAG pipeline. Identified **23 high-value scrapable sources** with confirmed WP REST API access (the easiest path), plus 12 additional sources scrapable via sitemap/HTML. Combined estimated yield: **~35,000+ articles** across all audiences.

**Top-priority targets by scrapability:**
1. Breaking Muscle (13,972 posts, open WP API, open robots.txt) -- general fitness, calisthenics, rehab, seniors
2. ADDitude Magazine (8,240 posts, WP API) -- ADHD + fitness/nutrition
3. TrainingPeaks (2,708 posts via sitemap, Yoast) -- endurance athletes
4. Chris Kresser (1,155 posts, WP API) -- autoimmune, thyroid, gut health
5. Autoimmune Wellness (1,013 posts, WP API) -- AIP, autoimmune + diet
6. Nerd Fitness (1,037 posts, WP API) -- mental health + fitness, general
7. MamasteFit (919 posts, WP API) -- postpartum/pregnancy fitness
8. Sleep Foundation (912 articles, WP API custom post type) -- sleep + recovery

---

## Confirmed Gaps (Priority 1-5)

---

### 1. ADHD + Fitness/Nutrition

| Site | URL | Est. Articles | Scrapability | Notes |
|------|-----|---------------|-------------|-------|
| **ADDitude Magazine** | additudemag.com | **8,240** | **WP REST API** (200 OK) | Premier ADHD resource. WP API returns full rendered content (~5K chars/post). robots.txt allows crawling. Owned by WebMD. Covers exercise, nutrition, meal prep for ADHD brains. |
| **ADDA (Attention Deficit Disorder Association)** | add.org | **563** | **WP REST API** (200 OK) | Nonprofit. Articles on ADHD diet, break-the-cycle eating, nutrition and brain connection. Evidence-based. |
| **Jackie Silver Nutrition** | jackiesilvernutrition.com | ~50 | HTML scrape | RD specializing in ADHD nutrition. Small but highly targeted archive. |
| **The Nutrition Junky** | thenutritionjunky.com | ~100 | HTML scrape | 50+ ADHD-friendly recipes plus meal planning guides. |

**Recommendation:** ADDitude is the clear winner. 8,240 posts with open WP API. Scrape and filter for exercise/nutrition/meal-prep tagged content. ADDA as supplementary source.

---

### 2. GLP-1 / Semaglutide / Tirzepatide

| Site | URL | Est. Articles | Scrapability | Notes |
|------|-----|---------------|-------------|-------|
| **Potere Health MD** | poterehealthmd.com | ~30 | HTML scrape | Medical practice specializing in GLP-1 and muscle loss. Small but expert. |
| **AZ Dietitians** | azdietitians.com | ~50 | HTML scrape | RD-written GLP-1 nutrition guidance. |
| **PubMed Central (PMC)** | pmc.ncbi.nlm.nih.gov | 100+ relevant | **NCBI E-utilities API** | Free programmatic access. Search "GLP-1 nutrition muscle" for open-access papers. NCBI API allows bulk retrieval. |
| **Endocrine Society** | endocrine.org | ~200 | HTML scrape | News, press releases, research summaries on GLP-1 medications. |

**Recommendation:** This is a very new topic (2023-2026 explosion). Content is primarily in medical journals, not fitness blogs. Best approach: scrape PMC via NCBI E-utilities API for open-access research, supplement with medical practice blogs. This audience needs the most original content creation as a gap-filler.

---

### 3. Sleep + Recovery

| Site | URL | Est. Articles | Scrapability | Notes |
|------|-----|---------------|-------------|-------|
| **Sleep Foundation** | sleepfoundation.org | **912** | **WP REST API** (custom post type `article`) | Authoritative sleep resource. Uses Yoast SEO. Custom post type accessible at `/wp-json/wp/v2/article`. Covers sleep hygiene, physical activity, body composition, recovery. |
| **HPRC (Human Performance Resource Center)** | hprc-online.org | ~300 | HTML scrape (Drupal) | Military performance resource. Sleep, recovery, physical fitness articles. DoD-funded, evidence-based. |
| **GSSI (Gatorade Sports Science Institute)** | gssiweb.org | ~200+ | HTML scrape | Sports Science Exchange articles since 1988. Recovery, sleep, hydration science. WP API blocked (403). |
| **Skratch Labs** | skratchlabs.com | ~50 | Shopify blog | Sleep, recovery, performance articles. Small archive. |

**Recommendation:** Sleep Foundation is the primary target -- 912 articles via WP API custom post type. HPRC as supplementary source for military/athletic recovery angle.

---

### 4. Seniors / Aging / Sarcopenia

| Site | URL | Est. Articles | Scrapability | Notes |
|------|-----|---------------|-------------|-------|
| **SilverSneakers** | silversneakers.com | ~300+ | Needs Playwright | Blog at /blog/. Timed out on WP API check. Content covers exercises, strength training, balance, nutrition for 65+. May need JS rendering. |
| **Breaking Muscle** | breakingmuscle.com | **13,972** total | **WP REST API** (200 OK) | Massive archive. Filter for senior/aging content. Open robots.txt (no Disallow). Contains articles on sarcopenia, aging, strength for older adults. |
| **Nerd Fitness** | nerdfitness.com | **1,037** | **WP REST API** (200 OK) | Beginner-friendly fitness. Some senior-targeted content. Great for accessibility. |
| **PMC/PubMed** | pmc.ncbi.nlm.nih.gov | 500+ relevant | **NCBI E-utilities API** | Rich sarcopenia research. Search "sarcopenia exercise nutrition elderly" for open-access systematic reviews. |

**Recommendation:** Breaking Muscle filtered for aging/senior content, plus SilverSneakers blog (may need Playwright). PMC for evidence base.

---

### 5. Mental Health + Fitness

| Site | URL | Est. Articles | Scrapability | Notes |
|------|-----|---------------|-------------|-------|
| **HelpGuide** | helpguide.org | **443** | **WP REST API** (200 OK) | Nonprofit mental health resource. Exercise for depression/anxiety, body image, eating disorders. Medically reviewed. |
| **Nerd Fitness** | nerdfitness.com | **1,037** | **WP REST API** (200 OK) | Strong mental health + fitness angle. Depression, anxiety, body image articles. Beginner-friendly voice. |
| **NAMI (National Alliance on Mental Illness)** | nami.org | ~500 | Blocked (403 on WP API) | Blog articles on exercise and mental health. Would need HTML scraping or Playwright. |
| **National Eating Disorders Association** | nationaleatingdisorders.org | ~200 | HTML scrape | Eating disorder recovery + exercise guidelines. |

**Recommendation:** HelpGuide (443 posts, WP API) and Nerd Fitness (1,037 posts, WP API) are both easily scrapable and cover this audience well. NAMI would require HTML scraping.

---

## Additional Audiences (Priority 6-16)

---

### 6. Postpartum / Pregnancy Fitness

| Site | URL | Est. Articles | Scrapability | Notes |
|------|-----|---------------|-------------|-------|
| **MamasteFit** | mamastefit.com | **919** | **WP REST API** (200 OK) | Postpartum fitness programs, return to exercise, diastasis recti. Large blog archive. |
| **Girls Gone Strong** | girlsgonestrong.com | **716** | **WP REST API** (200 OK) | Pre/postnatal fitness, women's health. Evidence-based, RD/PT-written. |
| **Get Mom Strong** | getmomstrong.com | **145** | **WP REST API** (200 OK) | Postpartum-specific fitness programs and articles. Smaller but focused. |
| **BIRTHFIT** | birthfit.com | ~100 | Blocked (403 on WP API) | Prenatal/postpartum training. Would need HTML scraping. |
| **ACOG** | acog.org | ~50 relevant | HTML scrape | Official pregnancy exercise guidelines. Clinical authority. |

**Recommendation:** MamasteFit (919 posts) + Girls Gone Strong (716 posts) = 1,635 easily scrapable posts via WP API. Excellent coverage of this audience.

---

### 7. Diabetes / Prediabetes + Fitness

| Site | URL | Est. Articles | Scrapability | Notes |
|------|-----|---------------|-------------|-------|
| **American Diabetes Association** | diabetes.org | ~1,000+ | Drupal sitemap (2 pages) | Comprehensive fitness/nutrition content. No WP API. Sitemap available. HTML scrape with structured selectors. |
| **diaTribe** | diatribe.org | ~500 | Cloudflare-protected | Diabetes news and education. Heavy CF protection -- needs Playwright with stealth. |
| **The Diabetic Friend** | thediabeticfriend.org | ~50 | HTML scrape | Sports nutrition for T1D athletes specifically. |
| **Beyond Type 1** | beyondtype1.org | ~300 | Redirect (301) | T1D lifestyle, fitness, nutrition articles. |

**Recommendation:** ADA (diabetes.org) via Drupal sitemap is the highest-value target. HTML scraping with proper selectors. diaTribe would be valuable but CF-protected.

---

### 8. Thyroid + Fitness

| Site | URL | Est. Articles | Scrapability | Notes |
|------|-----|---------------|-------------|-------|
| **Thyroid Pharmacist (Dr. Izabella Wentz)** | thyroidpharmacist.com | **321** | **WP REST API** (200 OK) | Hashimoto's diet, exercise, weight management. Pharmacist-written. Popular in thyroid community. |
| **The Thyroid Trainer** | thethyroidtrainer.com | ~50 | Redirect (302 on WP API) | Exercise programming specifically for Hashimoto's. Niche but targeted. |
| **Chris Kresser** | chriskresser.com | **1,155** | **WP REST API** (200 OK) | Functional medicine. Extensive thyroid, autoimmune, gut health content. Evidence-informed. |
| **Cleveland Clinic Health Essentials** | health.clevelandclinic.org | ~5,000+ | HTML scrape | Medical authority. Thyroid + exercise articles. Large archive but would need careful scraping. |

**Recommendation:** Thyroid Pharmacist (321 posts) is the primary target -- WP API, focused content. Chris Kresser (1,155 posts) covers thyroid plus autoimmune and gut health (triple-duty source).

---

### 9. Autoimmune + Diet

| Site | URL | Est. Articles | Scrapability | Notes |
|------|-----|---------------|-------------|-------|
| **Autoimmune Wellness** | autoimmunewellness.com | **1,013** | **WP REST API** (200 OK) | Definitive AIP resource. 250+ recipes, 100+ articles on AIP protocol. Now archived (not publishing new), but content is comprehensive. |
| **Chris Kresser** | chriskresser.com | **1,155** | **WP REST API** (200 OK) | Functional medicine approach to autoimmune conditions, anti-inflammatory diet, gut health. |
| **Autoimmune Institute** | autoimmuneinstitute.org | ~50 | HTML scrape | Culinary medicine for autoimmunity. Smaller but clinical authority. |
| **AFPA Fitness** | afpafitness.com | ~200 | HTML scrape | AIP definitive guide and related fitness/nutrition content. |

**Recommendation:** Autoimmune Wellness (1,013 posts) is the top source -- WP API, comprehensive AIP content. Chris Kresser overlaps here and with thyroid (#8).

---

### 10. Endurance Athletes

| Site | URL | Est. Articles | Scrapability | Notes |
|------|-----|---------------|-------------|-------|
| **TrainingPeaks** | trainingpeaks.com | **~2,708** | **Sitemap** (Yoast SEO, 3 post sitemaps: 1000+1000+708) | WP API blocked (403) but Yoast sitemap fully enumerated. Marathon, triathlon, cycling nutrition. HTML scrape with sitemap URLs. |
| **GSSI** | gssiweb.org | ~200+ | HTML scrape | Sports Science Exchange articles. Hydration, carb loading, endurance fueling. Gold-standard sports science. |
| **Stronger By Science** | strongerbyscience.com | **640** | **WP REST API** (200 OK) | Evidence-based training and nutrition. Endurance crossover content. Greg Nuckols' research-grade articles. |
| **Precision Nutrition** | precisionnutrition.com | ~300 | Blocked (401 on WP API) | Evidence-based nutrition coaching. WP API requires auth. Would need HTML scraping of blog. |

**Recommendation:** TrainingPeaks (2,708 posts via sitemap) is the primary target. Scrape HTML using sitemap URLs. Stronger By Science (640 posts, WP API) as supplementary for evidence-based depth.

---

### 11. Combat Sports / MMA

| Site | URL | Est. Articles | Scrapability | Notes |
|------|-----|---------------|-------------|-------|
| **Warrior Collective** | warriorcollective.co.uk | **143** | **Shopify sitemap** | MMA, BJJ, boxing nutrition, weight cutting, conditioning. Shopify-based with blog sitemap. |
| **Onnit** | onnit.com | ~200 | HTML scrape | Fighter diet plans, MMA conditioning. Well-produced content. |
| **Breaking Muscle** | breakingmuscle.com | 13,972 total | **WP REST API** | Filter for combat sports, MMA, martial arts content. Large archive includes combat-specific articles. |
| **ISSN (via PMC)** | pmc.ncbi.nlm.nih.gov | ~50 relevant | **NCBI API** | Official position stand on combat sports nutrition (2025). Open access. |

**Recommendation:** Breaking Muscle filtered for combat/MMA tags, plus Warrior Collective (143 articles via Shopify sitemap). Combat sports is a niche audience -- total available content is lower.

---

### 12. Calisthenics / Bodyweight

| Site | URL | Est. Articles | Scrapability | Notes |
|------|-----|---------------|-------------|-------|
| **Breaking Muscle** | breakingmuscle.com | 13,972 total | **WP REST API** | Filter for bodyweight, calisthenics, mobility content. Has dedicated calisthenics articles. |
| **Bodyweight Training Arena** | bodyweighttrainingarena.com | **386** | **WP REST API** (200 OK) | Progressive calisthenics, mobility, bodyweight progressions. Focused archive. |
| **Antranik.org** | antranik.org | **912** | **WP REST API** (200 OK) | Mobility, flexibility, bodyweight fitness. Very popular in r/bodyweightfitness. |
| **GMB Fitness** | gmb.io | **244** | **WP REST API** (200 OK) | Bodyweight skills, mobility, movement. Quality content aimed at non-athletes. |

**Recommendation:** All four sites have WP API access. Combined: ~1,542 focused calisthenics/bodyweight posts + Breaking Muscle filtered content. Antranik (912) is the largest and most respected in the community.

---

### 13. Injury Rehab + Return to Training

| Site | URL | Est. Articles | Scrapability | Notes |
|------|-----|---------------|-------------|-------|
| **E3 Rehab** | e3rehab.com | **145** | **WP REST API** (200 OK) | Evidence-based rehab. ACL, hamstring, tendon injuries. Return to sport protocols. Excellent clinical quality. |
| **Physiopedia** | physio-pedia.com | **~33,000+** | **MediaWiki API** | Massive wiki-style PT encyclopedia. ACL rehab, back pain, shoulder rehab -- all covered in detail. MediaWiki API available for bulk scraping. |
| **Breaking Muscle** | breakingmuscle.com | 13,972 total | **WP REST API** | Filter for rehab, injury, recovery content. |

**Recommendation:** Physiopedia via MediaWiki API is a massive untapped source. E3 Rehab for clinical return-to-sport protocols. Both have open API access.

---

### 14. Gut Health + Fitness

| Site | URL | Est. Articles | Scrapability | Notes |
|------|-----|---------------|-------------|-------|
| **ZOE** | zoe.com | **~1,081** /learn articles | **Sitemap** (custom framework, not WP) | Science-backed gut health, microbiome, nutrition. 1,081 learn articles in sitemap. HTML scrape -- clean Next.js structure. |
| **Chris Kresser** | chriskresser.com | 1,155 total | **WP REST API** | Extensive gut health, microbiome, probiotics content. |
| **Mennō Henselmans** | mennohenselmans.com | **422** | **WP REST API** (200 OK) | Evidence-based fitness/nutrition. Some gut health + protein content. |

**Recommendation:** ZOE (1,081 articles via sitemap) is the primary target for gut health. Clean sitemap, science-backed. Chris Kresser for functional medicine angle.

---

### 15. Vegan / Plant-Based Athletes

| Site | URL | Est. Articles | Scrapability | Notes |
|------|-----|---------------|-------------|-------|
| **No Meat Athlete** | nomeatathlete.com | **645** | **WP REST API** (200 OK) | Premier plant-based athlete resource. Protein, B12, iron, supplementation, marathon nutrition. Huge community. |
| **Girls Gone Strong** | girlsgonestrong.com | 716 total | **WP REST API** | Some plant-based content within women's fitness focus. |
| **Great Vegan Athletes** | greatveganathletes.com | ~100 | HTML scrape | Profiles and articles about elite vegan athletes. |

**Recommendation:** No Meat Athlete (645 posts, WP API) is the obvious primary target. Dedicated entirely to this audience.

---

### 16. Alcohol + Fitness

| Site | URL | Est. Articles | Scrapability | Notes |
|------|-----|---------------|-------------|-------|
| **Working Against Gravity** | workingagainstgravity.com | ~100 | HTML scrape (WP API check pending) | Flexible dieting, including alcohol and fitness goals articles. |
| **Breaking Muscle** | breakingmuscle.com | 13,972 total | **WP REST API** | Some alcohol + recovery articles. |
| **PMC** | pmc.ncbi.nlm.nih.gov | ~30 relevant | **NCBI API** | Research on alcohol and athletic performance, muscle protein synthesis, body composition. |
| **Outside Online** | outsideonline.com | ~20 relevant | HTML scrape | Articles on alcohol and workout recovery. |

**Recommendation:** This is a thin content area. No single site is dedicated to this topic. Best approach: aggregate from Breaking Muscle, PMC research, and general fitness sites. May benefit most from original content creation for the RAG pipeline.

---

## Scrapability Summary Table

### Tier 1: WP REST API (Easiest -- JSON, paginated, full content)

| Site | Posts | Audiences Served | Priority |
|------|-------|------------------|----------|
| Breaking Muscle | 13,972 | Seniors, Calisthenics, Combat, Rehab, General | **CRITICAL** |
| ADDitude Magazine | 8,240 | ADHD + Fitness/Nutrition | **CRITICAL** |
| Chris Kresser | 1,155 | Thyroid, Autoimmune, Gut Health | HIGH |
| Nerd Fitness | 1,037 | Mental Health + Fitness, Seniors | HIGH |
| Autoimmune Wellness | 1,013 | Autoimmune + Diet, AIP | HIGH |
| MamasteFit | 919 | Postpartum/Pregnancy | HIGH |
| Sleep Foundation | 912 | Sleep + Recovery (custom post type: `article`) | HIGH |
| Antranik.org | 912 | Calisthenics, Mobility | HIGH |
| Girls Gone Strong | 716 | Postpartum, Vegan, Women's Fitness | HIGH |
| No Meat Athlete | 645 | Vegan/Plant-Based Athletes | HIGH |
| Stronger By Science | 640 | Endurance, Evidence-Based Training | MEDIUM |
| ADDA | 563 | ADHD + Nutrition | MEDIUM |
| Mennō Henselmans | 422 | Gut Health, Evidence-Based | MEDIUM |
| HelpGuide | 443 | Mental Health + Fitness | MEDIUM |
| Bodyweight Training Arena | 386 | Calisthenics | MEDIUM |
| Thyroid Pharmacist | 321 | Thyroid + Fitness | MEDIUM |
| GMB Fitness | 244 | Calisthenics, Mobility | MEDIUM |
| E3 Rehab | 145 | Injury Rehab, Return to Training | MEDIUM |
| Get Mom Strong | 145 | Postpartum Fitness | LOW |

**Subtotal: ~31,829 posts via WP REST API**

### Tier 2: Sitemap + HTML Scrape (Moderate effort)

| Site | Est. Articles | Audiences | Method |
|------|---------------|-----------|--------|
| TrainingPeaks | ~2,708 | Endurance Athletes | Yoast sitemap + HTML |
| ZOE | ~1,081 | Gut Health, Nutrition Science | Sitemap + Next.js HTML |
| ADA (diabetes.org) | ~1,000+ | Diabetes + Fitness | Drupal sitemap + HTML |
| Warrior Collective | 143 | Combat Sports / MMA | Shopify sitemap + HTML |
| GSSI | ~200+ | Endurance, Recovery, Hydration | HTML scrape |

**Subtotal: ~5,132+ posts via sitemap/HTML**

### Tier 3: API (Specialized)

| Site | Est. Articles | Audiences | Method |
|------|---------------|-----------|--------|
| PubMed Central | Thousands | GLP-1, Sarcopenia, All medical | NCBI E-utilities API |
| Physiopedia | ~33,000+ | Injury Rehab, All PT topics | MediaWiki API |

**Subtotal: Massive but requires filtering**

### Tier 4: Needs Playwright / Blocked

| Site | Est. Articles | Issue |
|------|---------------|-------|
| SilverSneakers | ~300+ | WP API timeout / JS rendering needed |
| NAMI | ~500 | WP API 403 |
| Verywell Fit/Mind | ~5,000+ each | Cloudflare protection |
| diaTribe | ~500 | Cloudflare protection |
| Healthline | ~30,000+ | Custom CMS, crawl-delay: 5 |
| Precision Nutrition | ~300 | WP API 401 (auth required) |

---

## Priority Scrape Order

### Phase 1: Quick Wins (WP API, high article count, fill confirmed gaps)

1. **ADDitude Magazine** -- 8,240 posts, fills ADHD gap (confirmed zero in lake)
2. **Breaking Muscle** -- 13,972 posts, covers 5+ audiences (seniors, calisthenics, combat, rehab, general)
3. **Sleep Foundation** -- 912 articles, fills sleep/recovery gap (confirmed zero)
4. **Autoimmune Wellness** -- 1,013 posts, fills autoimmune gap
5. **HelpGuide** -- 443 posts, fills mental health + fitness gap

### Phase 2: Audience Expansion (WP API, targeted audiences)

6. **Chris Kresser** -- 1,155 posts (thyroid + autoimmune + gut health -- triple duty)
7. **Nerd Fitness** -- 1,037 posts (mental health + fitness, beginner-friendly)
8. **MamasteFit** -- 919 posts (postpartum/pregnancy)
9. **Girls Gone Strong** -- 716 posts (postpartum, women's fitness)
10. **No Meat Athlete** -- 645 posts (vegan/plant-based athletes)
11. **ADDA** -- 563 posts (ADHD supplementary)
12. **Antranik.org** -- 912 posts (calisthenics/bodyweight)
13. **Thyroid Pharmacist** -- 321 posts (thyroid + fitness)

### Phase 3: Sitemap Scraping (Higher effort, high value)

14. **TrainingPeaks** -- 2,708 posts (endurance athletes)
15. **ZOE** -- 1,081 articles (gut health, microbiome science)
16. **ADA (diabetes.org)** -- 1,000+ (diabetes + fitness)

### Phase 4: Specialized APIs

17. **Physiopedia** -- 33,000+ pages via MediaWiki API (injury rehab)
18. **PubMed Central** -- Open-access research via NCBI E-utilities (GLP-1, sarcopenia, all medical)

### Phase 5: Playwright Required

19. **SilverSneakers** -- seniors fitness
20. **NAMI** -- mental health
21. **Verywell Fit/Mind** -- general fitness/mental health (if Cloudflare bypass possible)

---

## Audience Coverage Assessment

| Audience | Content Available | Primary Sources | Gap Level |
|----------|-------------------|-----------------|-----------|
| ADHD + Fitness | Excellent | ADDitude (8,240), ADDA (563) | **FILLED** |
| GLP-1 / Semaglutide | Poor (too new) | PMC only | **STILL GAP** -- needs original content |
| Sleep + Recovery | Good | Sleep Foundation (912), HPRC | **FILLED** |
| Seniors / Sarcopenia | Good | Breaking Muscle, SilverSneakers, PMC | **FILLED** |
| Mental Health + Fitness | Good | HelpGuide (443), Nerd Fitness (1,037) | **FILLED** |
| Postpartum / Pregnancy | Excellent | MamasteFit (919), Girls Gone Strong (716) | **FILLED** |
| Diabetes + Fitness | Good | ADA, diaTribe (blocked), PMC | **MOSTLY FILLED** |
| Thyroid + Fitness | Good | Thyroid Pharmacist (321), Chris Kresser (1,155) | **FILLED** |
| Autoimmune + Diet | Excellent | Autoimmune Wellness (1,013), Chris Kresser | **FILLED** |
| Endurance Athletes | Excellent | TrainingPeaks (2,708), Stronger By Science (640) | **FILLED** |
| Combat Sports / MMA | Moderate | Warrior Collective (143), Breaking Muscle | **PARTIALLY FILLED** |
| Calisthenics / Bodyweight | Excellent | Antranik (912), BWA (386), GMB (244) | **FILLED** |
| Injury Rehab | Excellent | Physiopedia (33K+), E3 Rehab (145) | **FILLED** |
| Gut Health + Fitness | Good | ZOE (1,081), Chris Kresser (1,155) | **FILLED** |
| Vegan / Plant-Based | Good | No Meat Athlete (645) | **FILLED** |
| Alcohol + Fitness | Poor | No dedicated sources | **STILL GAP** -- needs original content |

---

## Remaining Content Gaps

Two audiences have insufficient dedicated content sources:

1. **GLP-1 / Semaglutide**: Topic exploded 2023-2025. Most content is in medical journals (PMC) or behind paywalls. No large blog archive exists yet. Recommendation: scrape PMC open-access papers + monitor emerging sites.

2. **Alcohol + Fitness**: Scattered articles across general fitness sites. No dedicated resource. PMC has ~30 relevant papers. Recommendation: aggregate what exists from Breaking Muscle + PMC, and flag for original content creation.

---

## Technical Notes

### WP REST API Scraping Pattern
```
GET /wp-json/wp/v2/posts?per_page=100&page={n}&_fields=id,title,content,excerpt,date,link,tags,categories
```
- Paginate with `page` parameter
- Use `X-WP-TotalPages` header to know when to stop
- `_fields` parameter reduces payload size
- Content is in `content.rendered` (full HTML)

### Sleep Foundation Custom Post Type
```
GET /wp-json/wp/v2/article?per_page=100&page={n}
```
Uses custom post type `article` instead of `posts`.

### TrainingPeaks (Sitemap Approach)
```
1. Fetch https://www.trainingpeaks.com/post-sitemap.xml (pages 1-3)
2. Extract all <loc> URLs
3. Scrape each URL for article content
```

### Physiopedia (MediaWiki API)
```
GET https://www.physio-pedia.com/api.php?action=query&list=allpages&aplimit=500&format=json
GET https://www.physio-pedia.com/api.php?action=parse&page={title}&format=json
```

### PubMed Central (NCBI E-utilities)
```
# Search
GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pmc&term=GLP-1+nutrition+muscle&retmax=100
# Fetch full text
GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={PMCID}&rettype=xml
```

---

## Sources

- [ADDitude Magazine](https://www.additudemag.com/) -- ADHD resource, WebMD-owned
- [ADDA](https://add.org/) -- Attention Deficit Disorder Association
- [Sleep Foundation](https://www.sleepfoundation.org/) -- Sleep health education
- [Breaking Muscle](https://breakingmuscle.com/) -- Fitness information hub
- [Autoimmune Wellness](https://autoimmunewellness.com/) -- AIP protocol resource
- [Chris Kresser](https://chriskresser.com/) -- Functional medicine
- [Nerd Fitness](https://www.nerdfitness.com/) -- Beginner-friendly fitness
- [MamasteFit](https://mamastefit.com/) -- Postpartum fitness
- [Girls Gone Strong](https://www.girlsgonestrong.com/) -- Women's fitness
- [No Meat Athlete](https://www.nomeatathlete.com/) -- Plant-based athletics
- [Thyroid Pharmacist](https://thyroidpharmacist.com/) -- Hashimoto's/thyroid
- [HelpGuide](https://www.helpguide.org/) -- Mental health nonprofit
- [TrainingPeaks](https://www.trainingpeaks.com/) -- Endurance training
- [Stronger By Science](https://www.strongerbyscience.com/) -- Evidence-based training
- [ZOE](https://zoe.com/) -- Gut health and microbiome science
- [Antranik.org](https://antranik.org/) -- Bodyweight fitness and mobility
- [Bodyweight Training Arena](https://bodyweighttrainingarena.com/) -- Calisthenics
- [GMB Fitness](https://gmb.io/) -- Bodyweight skills
- [E3 Rehab](https://e3rehab.com/) -- Evidence-based rehabilitation
- [Physiopedia](https://www.physio-pedia.com/) -- Physical therapy wiki
- [Warrior Collective](https://warriorcollective.co.uk/) -- Combat sports
- [GSSI](https://www.gssiweb.org/) -- Gatorade Sports Science Institute
- [Mennō Henselmans](https://mennohenselmans.com/) -- Evidence-based fitness
- [ADA](https://diabetes.org/) -- American Diabetes Association
- [PMC/PubMed](https://pmc.ncbi.nlm.nih.gov/) -- Open-access medical research
