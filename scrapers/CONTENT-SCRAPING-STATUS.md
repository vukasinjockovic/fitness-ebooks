# Content Scraping Status Report

**Last updated:** 2026-03-16
**DB (lake.content):** 182,397 rows
**Disk total:** ~190,000 articles

---

## Category 1: Fitness / Nutrition Science (12,887 articles)

| Site | Articles | Status |
|------|----------|--------|
| nutritionfacts.org | 5,801 | DONE (fix: removed _embed from WP API) |
| chrismasterjohn.com | 1,156 | DONE |
| peterattiamd.com | 993 | DONE |
| bretcontreras.com | 991 | DONE |
| biolayne.com | 753 | DONE |
| precisionnutrition.com | 743 | DONE (208 non-article URLs skipped) |
| strongerbyscience.com | 640 | DONE |
| seannal.com | 435 | DONE |
| weightology.net | 380 | DONE |
| born-fitness.com | 364 | DONE |
| renaissanceperiodization.com | 362 | DONE |
| examine.com | 166 | DONE (free tier only, paywalled content not scraped) |
| thefitness.wiki | 103 | DONE |
| foundmyfitness.com | 0 | BLOCKED: SPA (not WordPress), S3 sitemap returns 0 URLs. Needs custom Playwright scraper. |
| evidencebasedfitness.net | 0 | DEAD: Site shows "Coming Soon" page. Decommissioned. |

---

## Category 2: Bodybuilding / Training (23,711 articles)

| Site | Articles | Status |
|------|----------|--------|
| breakingmuscle.com | 13,972 | DONE |
| t-nation.com | 5,981 | DONE (archive.t-nation.com WP REST API) |
| muscleandstrength.com | 1,220 | RUNNING (Wayback Machine, slow due to rate limits) |
| nerdfitness.com | 1,037 | DONE (WP REST API, 10s crawl delay) |
| bodybuilding-com | 1,004 | RUNNING (Wayback Machine, heavy connection refused errors) |
| trainheroic.com | 497 | DONE (WP REST API) |
| liftvault.com | 0 | BLOCKED: WAF blocks per_page>10 on WP API. Tor exit nodes also blocked (403). Needs residential proxy. ~300 articles. |
| barbend.com | 0 | BLOCKED: TLS handshake failure from server IP AND Tor. Cloudflare IP blacklist. Needs residential proxy. ~5,000 articles. |

**Not built:** simplyshredded.com (~500), advancedhumanperformance.com (~200)

---

## Category 3: Nutrition / Meal Planning (16,082 articles)

| Site | Articles | Status |
|------|----------|--------|
| verywellfit.com | 4,360 | RUNNING (Wayback + HTML scraper, multiple workers) |
| healthline.com | 3,005 | RUNNING (HTML scraper) |
| dietdoctor-articles | 2,300 | DONE |
| medicalnewstoday.com | 1,975 | DONE |
| noom.com | 1,524 | DONE |
| today-health | 848 | RUNNING (WP API, 3 workers) |
| nutritionstripped.com | 801 | DONE |
| harvard-health | 474 | DONE |
| eatingwell-articles | 359 | RUNNING (3 workers) |
| myfitnesspal-blog | 268 | RUNNING (2 workers) |
| mayoclinic.org | 168 | DONE |
| clevelandclinic.org | 0 | BROKEN: Site restructured, all scraped URLs return 404. Needs URL re-mapping from new sitemap. ~1,200 articles. |

**Not built:** webmd.com/diet (~3,000, needs Playwright), nutrition.org (~1,000), eatthismuch.com (~200)

---

## Category 4: GLP-1 / Pharma (NOT STARTED)

Needs Reddit API (PRAW). No scrapers built. ~121K pieces of content (mostly Reddit threads).

---

## Category 5: YouTube Transcripts (4,374 / 26,052 video IDs)

| Channel | Transcripts | Video IDs | % Complete |
|---------|-------------|-----------|------------|
| greg-doucette | 666 | 4,007 | 16% |
| mindpumptv | 645 | 3,971 | 16% |
| thomas-delauer | 591 | 3,562 | 16% |
| nutritionfacts-yt | 456 | 2,745 | 16% |
| renaissance-periodization | 332 | 2,141 | 15% |
| athlean-x | 283 | 1,500 | 18% |
| remington-james | 214 | 1,218 | 17% |
| biolayne-yt | 176 | 1,135 | 15% |
| jeff-nippard | 140 | 486 | 28% |
| mario-tomic | 118 | 745 | 15% |
| buff-dudes | 117 | 780 | 15% |
| protein-chef | 111 | 643 | 17% |
| megsquats | 98 | 620 | 15% |
| fitmencook | 96 | 598 | 16% |
| will-tennyson | 86 | 459 | 18% |
| huberman-lab | 66 | 389 | 16% |
| sean-nalewanyj | 63 | 399 | 15% |
| jeremy-ethier | 46 | 303 | 15% |
| natacha-oceane | 42 | 231 | 18% |
| foundmyfitness-yt | 28 | 140 | 20% |
| blogilates | 0 | 0 | Phase 1 not run |
| madfit | 0 | 0 | Phase 1 not run |
| krissy-cela | 0 | 0 | Phase 1 not run |
| sydney-cummings | 0 | 0 | Phase 1 not run |

**Status:** RUNNING (50 workers via 97 Tor SOCKS5 proxies, ~0.2-0.3 vids/sec, ~50% success rate). Estimated ~15-20 hours for remaining 21,678 videos.

---

## Category 6: Reddit Subreddits (NOT STARTED)

Needs Reddit API (PRAW). No scrapers built. 18 target subreddits, ~50M+ posts.

---

## Category 7: Forums (NOT STARTED)

Needs Wayback Machine CDX API for BB.com archive. No scrapers built. Massive scale (94M+ posts).

---

## Category 8: Supplement Reviews (4,364 articles)

| Site | Articles | Status |
|------|----------|--------|
| legionathletics.com | 2,765 | DONE |
| illuminatelabs.org | 933 | DONE |
| transparentlabs.com | 384 | DONE |
| labdoor.com | 282 | DONE |

**Not built:** ConsumerLab (~2,000, paywalled), supplementreviews.com, stackguide.com

---

## Category 9: Women's Health (19,997 articles)

| Site | Articles | Status |
|------|----------|--------|
| womenshealthmag.com | 17,578 | DONE |
| girlsgonestrong.com | 716 | DONE |
| drbrighten.com | 462 | DONE |
| axiawh.com | 224 | DONE |
| pcosnutrition.com | 201 | DONE |
| menohello.com | 179 | DONE |
| joinmidi.com | 175 | DONE |
| thepauselife.com | 155 | DONE |
| happyhormonesforlife.com | 150 | DONE |
| femalehealthawareness.org | 116 | DONE |
| thewomensdietitian.com | 40 | DONE |
| larabriden.com | 1 | MOSTLY FAILED: Wayback scraper found 210 URLs but extracted only 1. Parsing bug. ~110 articles. |

---

## Category 10: Coach Education (10,377 articles)

| Site | Articles | Status |
|------|----------|--------|
| twobrainbusiness.com | 2,829 | DONE |
| nfpt.com | 2,371 | DONE |
| theptdc.com | 1,971 | DONE |
| trainerize.com | 1,077 | DONE |
| opexfit.com | 801 | DONE |
| ptpioneer.com | 776 | DONE |
| ptdistinction.com | 332 | DONE |
| mypthub.net | 220 | DONE |

**Category fully scraped.**

---

## Category 11: Scientific Papers (98,483 articles)

| Source | Articles | Status |
|--------|----------|--------|
| PubMed (bulk MeSH queries) | 58,534 | DONE (55 queries covering exercise science, supplements, weight management, women's health, aging) |
| Nutrients (MDPI) | 23,647 | DONE (journal-specific search) |
| Frontiers in Nutrition | 12,200 | DONE (journal-specific search) |
| BMC Sports Science | 1,703 | DONE (journal-specific search) |
| JISSN | 1,417 | DONE (pre-existing) |
| Sports Medicine Open | 982 | DONE (journal-specific search) |

**Content type:** Abstracts only (~300-600 words each). Full text available for PMC open-access subset (not yet scraped).

---

## Category 12: Podcast Transcripts (NOT STARTED)

13 podcast directories created, research docs exist (PROBE-BATCH-1.md, PROBE-BATCH-2.md), but no scrapers built.

**Approach options:**
- YouTube captions for podcasts with video versions (cheapest)
- Whisper on RSS MP3s (highest quality but compute-heavy)
- ~6,000+ episodes across 13 podcasts

---

## Sites Requiring Residential Proxy

These sites block both the server's direct IP and Tor exit nodes:

| Site | Category | Est. Articles | Block Type |
|------|----------|---------------|------------|
| barbend.com | 2-Bodybuilding | ~5,000 | Cloudflare TLS blacklist (IP + Tor) |
| liftvault.com | 2-Bodybuilding | ~300 | WAF per_page cap + Tor 403 |

---

## Sites Needing Scraper Fixes

| Site | Category | Issue | Est. Articles |
|------|----------|-------|---------------|
| clevelandclinic.org | 3-Nutrition | Site restructured, old URLs 404. Needs URL re-mapping. | ~1,200 |
| foundmyfitness.com | 1-Fitness Science | SPA, not WordPress. Needs Playwright/scrapling SPA scraper. | ~100 |
| larabriden.com | 9-Women's Health | Wayback scraper parsing bug (1/210 extracted). | ~110 |

---

## Unbuilt Scraper Sources

| Source | Category | Est. Articles | Difficulty | Notes |
|--------|----------|---------------|------------|-------|
| Reddit (18 subs) | 4, 6 | 50K+ threads | Medium | Needs PRAW + Reddit API |
| Podcast transcripts | 12 | 6,000+ episodes | Medium | YouTube captions or Whisper |
| webmd.com/diet | 3 | ~3,000 | Medium | Needs Playwright (JS) |
| simplyshredded.com | 2 | ~500 | Easy | Standard HTML |
| nutrition.org | 3 | ~1,000 | Hard | Academic paywall |
| ConsumerLab | 8 | ~2,000 | Blocked | $99/yr paywall |
| BB.com forum archive | 7 | millions | Hard | Wayback CDX, massive scale |
| PMC full-text | 11 | ~5-10K | Medium | E-fetch XML, open access subset |
| advancedhumanperformance.com | 2 | ~200 | Easy | Standard HTML |
| eatthismuch.com | 3 | ~200 | Easy | Standard blog |
| supplementreviews.com | 8 | ~5,000 | Medium | Community reviews |

---

## Summary

| Category | Articles | Status |
|----------|----------|--------|
| 1. Fitness/Nutrition Science | 12,887 | 13/15 sites done |
| 2. Bodybuilding/Training | 23,711 | 6/8 sites done, 2 blocked |
| 3. Nutrition/Meal Planning | 16,082 | 5 still running, 1 broken |
| 4. GLP-1/Pharma | 0 | NOT STARTED (needs Reddit API) |
| 5. YouTube Transcripts | 4,374 | RUNNING (16% of 26K videos) |
| 6. Reddit Subreddits | 0 | NOT STARTED (needs Reddit API) |
| 7. Forums | 0 | NOT STARTED (massive scale) |
| 8. Supplement Reviews | 4,364 | DONE (4/4 free sites) |
| 9. Women's Health | 19,997 | 11/12 sites done |
| 10. Coach Education | 10,377 | DONE (all 8 sites) |
| 11. Scientific Papers | 98,483 | DONE (6 sources) |
| 12. Podcast Transcripts | 0 | NOT STARTED (research done) |
| **TOTAL** | **190,275** | |
