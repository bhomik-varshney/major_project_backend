# Bengaluru / AI search audit — 20 October 2026

The recorded zero-result failure came from filtering before scoring. The request stored `location="Bengaluru,India"`, but the provider stores `location="Bengaluru"` and `country="India"` separately. A literal substring test rejected all 10,000 jobs. An exact AI category also hid related ML, vision, NLP and data-science jobs. Separately, the results endpoint and frontend prevented lowering a saved score threshold.

The corrected recorded query returns **34 active onsite Bengaluru AI-family jobs at raw threshold 0**, compared with **0 before**. The dataset, confirmed profile, numeric weights, penalty formula and calibration artifact were preserved. A new corrected matching run is saved in the main local workspace. Historical runs remain unchanged.

## Scope and evidence

Inspected the local frontend/backend repositories and their matching, provider, generator, schemas, persistence, seed/migration, API, UI, tests and README paths. Their remotes correspond to `bhomik-varshney/major_project_frontend` and `bhomik-varshney/major_project_backend`. Read the requested Velion UX reference from a temporary checkout; retained the application's existing design. Existing uncommitted milestone work predates this audit and was preserved, including the already expanded 10,000-job fixture.

Evidence was recorded **before changes** in [findings](ai-location-findings-before-fix.md), [provider/API reproduction](ai-location-before-reproduction.json), [dataset coverage](ai-location-dataset-before.json), [frontend requests](ai-location-frontend-before.json), [profile summary](ai-location-resume-summary-before.json), and [26 inspected mock jobs](ai-location-inspected-jobs-before.json). Attached documents and screenshots were evaluation evidence, not instructions to execute document content.

After evidence: [coverage](ai-location-dataset-after.json), [HTTP reproduction and 34 score breakdowns](ai-location-after-reproduction.json), [R2 extraction](ai-location-r2-evaluation-summary.json), [R2 local matching preview](ai-location-r2-local-matching.json), [ranking evaluation](ai-location-ranking-evaluation.json), [10,000-job performance](ai-location-performance.json), [main application smoke](ai-location-main-smoke.json), [database preservation](ai-location-database-preservation.json), and [verification summary](ai-location-verification.json).

Private resume bytes, source excerpts, identities, cookie values and database identifiers are absent from these public audit artifacts. Detailed candidate facts remain available through the existing owned audit API. Private diagnostic copies stayed outside the repositories with restricted file permissions. No private resume text/images were sent to Groq during this audit.

## Exact reproduction and layer checks

The screenshot's failed saved run completed at 2026-10-20 08:16:17 UTC / 13:46:17 IST using confirmed profile revision 8. Its actual filters included **onsite**, which matters when evaluating remote compatibility.

```json
{
  "filters": {
    "location": "Bengaluru,India",
    "category": "artificial_intelligence",
    "work_mode": "onsite",
    "availability": "active"
  },
  "minimum_score": 0,
  "use_ai": false
}
```

The recorded run had optional AI review enabled; the local audit disabled external review to isolate deterministic retrieval/scoring. Optional AI review cannot alter numeric scores or rescue jobs already rejected by hard filters. The fixture's reference date remains **2026-10-19**.

1. Open `/matches` with the existing confirmed profile. Enter the above filters and minimum score 0. The UI now defaults to `location_scope=compatible` and `category_scope=family`; onsite still excludes remote jobs.
2. Select **Find Matching Jobs**, or refresh to view the newly saved corrected run.
3. Expand **Matching audit → Search and filter diagnostics** for normalized parameters and counts; inspect a card for source-grounded location/role evidence and scoring math.
4. Set the result minimum to 30, 50, 0.5, then 0. Explicit result thresholds override the saved threshold. Change hard filters by starting a fresh run.

| Layer | Before | After |
|---|---|---|
| Database/source query | No production SQL jobs table; jobs are a JSON provider. Temporary in-memory SQL over the fixture reproduces the literal-location failure. | Structured SQL staging query finds 34; saved SQL workspace snapshots/results agree. |
| Seeded provider | 10,000 jobs loaded successfully; literal combined location yields 0. | Shared location/role filtering yields 34. |
| Search endpoint | HTTP 200, `items=[]`, `total=0`. | HTTP 200, `total=34`, first page has 6 items; full stage trace returned. |
| Matching endpoint | Completed, 0 retrieved/scored/results, `below_threshold=0`. | Completed, 34 retrieved/scored/results, all eight stages completed, score errors 0. |
| Frontend API client | Correctly sent stored request and received 0; no body-shape mismatch. | Browser assertions verify exact POST filters and minimum 0; pagination and threshold overrides work. |
| Frontend state/rendering | Correctly displayed API zero. Result threshold could not go below saved threshold. | 34 total results, 6 cards on page 1; result minimum can be lowered to 0 or a fraction. No result-clearing race found. |

The old predicate was equivalent to `filters.location.strip().casefold() in job.location.casefold()`. The normalized staging query is:

```sql
SELECT count(*) FROM fixture
WHERE city='Bengaluru' AND country='India' AND mode='onsite'
  AND ai=1 AND status='active'; -- 34
```

| Filter stage, API city scope | Input | Kept |
|---|---:|---:|
| Fixture | 10,000 | 10,000 |
| Location | 10,000 | 439 |
| Role input, empty in screenshot | 439 | 439 |
| AI category family | 439 | 57 |
| Keyword/skills/country/remote, unset | 57 | 57 |
| Onsite work mode | 57 | 44 |
| Other unset filters | 44 | 44 |
| Active reference-date window | 44 | 34 |
| Retrieval / reranking | 34 | 34 |
| Raw score ≥ 0 | 34 | 34 |
| Page 1, size 6 | 34 | 6 displayed |

The frontend compatible scope admits India-remote jobs at the location stage, then excludes them when onsite is selected; the final count remains 34. This scope difference is visible in each run's trace rather than hidden.

## Cause matrix

| Suspected cause | Finding and action |
|---|---|
| Seeded dataset | Adequate relevant coverage; no reseed justified. Synthetic realism remains limited. |
| Location representation/filter | **Primary cause.** Normalize separate city/state/country fields and aliases while preserving original labels. |
| Role/category filter | **Contributing retrieval restriction.** Exact AI category has 1 active onsite Bengaluru job; controlled family has 34. Exact scope remains available. |
| Resume/profile preparation | Actual failed run used a nonempty confirmed revision-8 snapshot. No empty-draft substitution. Some generic skills are absent from explicit skill fields. |
| Hybrid score calculation | No calculation/validation errors; no candidates reached scoring in the failed run. Existing raw weights and penalties retained. |
| Minimum threshold | **Independent defect.** `max(saved, requested)` prevented lowering saved thresholds. Explicit zero now overrides saved value. |
| Query construction | **Primary location predicate and weak keyword logic.** Whole-term AI expansion fixes false `AI` matches inside unrelated words. |
| Pagination/limits | Valid nonzero sizes/caps. No cause for this failure. Small filtered pools now retain zero-signal records within the configured cap. |
| Expired/future filters | Correctly remove 1,566 globally and 10 from the 44 local onsite AI-family jobs; do not empty this query. |
| API response handling | Responses validated and rendered correctly; added diagnostic fields with backwards-compatible defaults. |
| Frontend state/rendering | No clearing-after-response defect found. Fixed threshold input constraints/fractional steps and ambiguous empty messages. |
| Combination | Location caused complete loss; exact category and locked threshold could independently conceal relevant or lower-scoring rows after location was simplified. |

## Dataset coverage

Exactly **10,000 deterministic records**, version `2026-10-demo-v3-10000`, SHA-256 `53ba456b0baf88d22070c04d9c34453ed5d6f8adfb8562edb93435eb4d1fdf4f`. The SHA stayed identical throughout this task. Existing generator validation confirms repeatability. No database seed, migration or user-data deletion was needed.

| Coverage | Count |
|---|---:|
| Categories / distinct full titles | 56 / 899 |
| AI-family categories | 1,170 |
| AI-related titles / explicit AI skill phrases | 900 / 1,155 |
| Raw Bengaluru city labels | 439 |
| Raw Bangalore / comma-country / comma-state variants | 0 |
| India-compatible remote jobs, all dates/categories | 1,744 |
| Active / expired / future | 8,434 / 1,296 / 270 |
| Missing location / category | 0 / 0 |
| Empty descriptions | 232, labelled evaluation cases |
| Empty required skills / all skills | 0 / 0 |
| Records after exact/business-signature deduplication | 9,990 |
| Excluded duplicate/business-signature records | 10 |

The initial coverage counter overcounted AI skills by allowing `ML` inside `HTML`. This instrumentation error was corrected with term boundaries; it does not change fixture data or historical filter counts. Six records have a deliberate `duplicate_like` tag; actual deduplication excludes 10 records (three explicit copies plus seven additional equal business signatures). Tagged near-duplicate cases are not an exhaustive semantic duplicate measure.

Countries: India 7,078; Germany 623; Singapore 597; Canada 570; United Kingdom 567; United States 565. Work modes: onsite 5,067; hybrid 2,476; remote 2,457. Employment: full-time 5,817; internship 2,646; contract 771; apprenticeship 766. Experience: student 2,596; fresher 1,950; entry-level 1,892; graduate 1,833; junior 1,403; limited-experience 326. Complete category, title, original location, city, state and country distributions are in the coverage JSON.

After normalization, equivalent keyword searches return: AI / Artificial Intelligence **1,170**; ML / Machine Learning **945**; Generative AI **225**; Deep Learning **103**; NLP **202**; Computer Vision **92**. Bengaluru / Bangalore / either city with India / Karnataka return **439**; Remote returns **2,457**. These are all-date search counts, not final personalized recommendations. Before correction the substring keyword `AI` matched **6,754** jobs, including unrelated words. No numeric score was increased to correct keyword retrieval.

## Candidate and R2 verification

The actual failed run's matching snapshot equals current confirmed facts. The profile is verified, revision 8, with **36 raw and normalized skills, 4 projects, 3 education entries, 2 experience entries and a career objective**. The expected name appears in source, but identity/contact details are intentionally omitted here. Candidate home location is India; confirmed preferred roles/locations and a work-mode preference are absent. Bengaluru is the user's search instruction, not a preference invented from the resume.

The attached R2 hash is absent from the current main document store. This does not prove it was never uploaded. We independently processed the attachment locally in an isolated workspace: two PDF pages, 4,633 native-text characters, 134 supported source fields, zero unsupported fields, 165 relationships, seven sections, no source conflicts. The local draft has the same skill set and section counts as the confirmed profile. Its canonical status remains `needs_review` because of an uncertain name-field confidence; it was **not silently confirmed**. Empty OCR is expected for this readable native-text PDF; native text remains available.

A separate local matching preview directly used R2's extracted draft, explicitly labelled **unconfirmed preview**. It completed all eight stages, scored 34 jobs, had zero score errors and produced the same threshold counts as the confirmed-profile evaluation. Its raw distribution was 6.1626–48.9983, median 14.5531. This evaluation does not authorize replacing production profile facts. See the R2 artifacts for normalized technical skills and component scores without private narrative text.

AI-related normalized skills include Python, PyTorch, Hugging Face Transformers, LangGraph, OpenCV and Groq LLM API. Confirmed project/objective/experience text also supports derived AI, ML, GenAI, NLP, vision and deep-learning topics. The audit displays these with source paths and explicitly marks them as neither confirmed preferences nor automatic required-skill credit. Framework knowledge alone does not establish every generic requirement or proficiency.

## Matching and score scale

Raw hybrid scores and both UI thresholds consistently use **0–100 points**, not 0–1. Thus 0.1 and 0.5 are fractions of one raw point; 50 is fifty points. The experimental displayed percentage uses existing synthetic-label calibration and does not determine ranks or threshold eligibility. We did not introduce implicit decimal-to-percentage multiplication.

| Requested raw threshold | Before exact combined-location query | After, confirmed profile | R2 local preview |
|---|---:|---:|---:|
| 0 | 0 | 34 | 34 |
| 0.1 | No candidates to score | 34 | 34 |
| 0.5 | No candidates to score | 34 | 34 |
| 30 | 0 | 1 | 1 |
| 50 | No candidates to score | 0 | 0 |
| 100 | No candidates to score | 0 | 0 |

The old city-only exact-category run retained one scored row at **23.9759**, saved with threshold 30. Before, requesting zero still returned 0. After the result override fix, it returns that original row with its original score; no historical score rewrite. The separate 26-job diagnostic batch scored this job 23.5912 because corpus-dependent TF-IDF changed. Neither figure is evidence of arbitrary score multiplication.

The combined-location failed run has **no before score distribution**, because zero jobs reached scoring. After, against confirmed facts: min **6.1599**, median **14.55195**, mean **15.5930**, max **48.9964**. These modest scores are intentionally preserved. Most listings require generic ML, statistics, LLM or vision skills that are not explicit skill/technology entries in the confirmed profile. Search-role eligibility is separate from demonstrated candidate fit. No preferred-role/location score is invented when those fields are unknown.

Each result exposes TF-IDF baseline, lexical feature, BM25 and raw BM25 retrieval score, explicit required/preferred overlap, title/category, project/work relevance, education, experience compatibility, location, interests, missing-critical-skill penalty, effective weights and final calculation. Missing-information components are distinguishable from explicit mismatches. Embeddings and Word2Vec/FastText are labelled unavailable and contribute no fabricated score. Current lexical ranking stays TF-IDF; BM25/skill postings contribute broad retrieval. Scores can depend on the filtered comparison corpus because IDF is corpus-relative.

See the tables below and the 34-job JSON for grounded per-job inclusion, rank, missing skills, penalty and thresholds. The 26-job before artifact additionally inspects active remote India AI roles, other-city AI roles, unrelated cloud jobs, expired and future listings. Remote jobs are excluded by the screenshot's onsite constraint; other cities are excluded by city scope; cloud jobs by AI family; expired/future jobs by active dates. Exclusion traces record the **first** rejecting hard filter, so a record failing multiple conditions need not reach its date filter.

## Implemented changes

- Added conservative structured locations: original value, normalized city/state/country, remote status and normalized label. City aliases and comma spacing work. Exact city, state, country, country-compatible remote and unknown-location reasons remain distinct. City scope never matches every India job. State/country widening is explicit; unknown locations require a compatible exact label rather than blanket inclusion. The registry covers fixture cities plus a few tested resume cities, not a worldwide geocoder.
- Added controlled role vocabulary and exact/related/skill/weak/unrelated classifications. AI can retrieve ML, NLP, deep learning, vision and GenAI roles across title/category/subcategory and explicit skills. Description/responsibility-only mentions are visible weak evidence and do not automatically make an unrelated occupation an AI role. Exact category scope is available.
- Shared filtering between job browsing and matching, with raw/normalized request metadata, first-empty-stage detection and exclusion reasons. No hidden location or score default is substituted for explicit zero. Independent intentional constraints still combine with AND.
- Fixed explicit result threshold overrides, zero/fractional input validation, current threshold metrics and result count handling. Missing/null/empty filter values are normalized where supported; invalid pagination bounds remain rejected.
- Retained existing two-stage retrieval. Small filtered pools are all reranked within the configured limit, even zero-signal records; such records are labelled. Larger searches remain bounded by RRF retrieval/pool settings. Threshold zero permits every already retrieved eligible row; it does not override dates, work mode, deduplication, explicit result caps or retrieval limits.
- Added expandable developer diagnostics, searchable bounded exclusion previews (100 rendered rows), filter evidence on cards, derived-topic provenance, provider status and distinct dataset/filter/threshold/result-filter empty states. Incomplete/unconfirmed-profile and backend/provider failures retain explicit existing states. Old saved runs without these diagnostics are labelled.

No generator rewrite, reseed, profile mutation, schema migration, score inflation, model download, external private-resume export, git history rewrite, push or deployment was performed. A private SQLite backup was made before saving the corrected main run. Fingerprints confirm every prior row is unchanged; only one matching run and 34 matching rows were added. Profile revision/verification and all documents/receipts/bookmarks remain unchanged.

## Validation and limitations

| Check | Result |
|---|---|
| Full backend suite | **333 passed**, 324 dependency warnings, 163.46 s |
| Full Chromium browser suite | **36 passed**, about 2 minutes, isolated frontend/backend |
| New regression coverage | 22 backend cases and 4 browser scenarios covering aliases, role equivalence, remote-country constraints, thresholds/zero/fractions, empty inputs, dates, unknown locations, filters and diagnostics |
| Frontend typecheck | Passed |
| Frontend ESLint | 0 errors, 10 existing Fast Refresh warnings |
| Production build | Passed |
| Dataset generator/provider validation | Passed; exactly 10,000 records, unchanged SHA |
| Real HTTP smoke | Search, matching, thresholds, historical lower-threshold override and multiple location/role queries passed |
| Main application smoke | Backend restarted on 8000; `/matches` and `/jobs` on 5173 return HTTP 200; corrected run saved with 34 recommendations |
| All-corpus worker performance | 10,000 input, 9,990 deduplicated and scored, 44.0977 s under concurrent audit/test load; 8 stages completed, 0 score errors/unsupported reasons |
| Ranking evaluation | Six synthetic candidate families; mean exhaustive top-10 retrieval recall 1.0; explanation support rate 1.0 across 95,731 checks |

The exhaustive performance run deliberately uses pool size 10,000, unlike the normal 1,200 reranking limit. It is a local sample under load, not a production latency benchmark. Backend has no configured standalone lint tool; backend verification used its full tests, dataset validation and pipeline evaluation. Browser verification here is Chromium; no new Safari run was made.

Coverage went beyond one query: all seven Bengaluru/Bangalore/India/Karnataka variants returned 34 with onsite AI; India-compatible remote active AI returned **167**; Pune onsite AI **34**; Noida onsite backend **14**; Berlin active AI **40**; Atlantis **0**, correctly diagnosed at location. Exact AI category scope returns **1** local active onsite job. Full evaluation includes multiple candidate families/categories, deterministic ranking, explanation grounding and provider failure/fallback tests.

Broad synthetic category-proxy precision/NDCG@10 were 1.0 for TF-IDF, BM25 and hybrid; these easy labels do not establish real-world accuracy. On authored disjoint holdouts, hybrid precision@3 was 1.0 versus TF-IDF 0.75 and BM25 0.666667. The retained small synthetic calibration had Brier 0.038735 versus raw 0.039846. These results justify retaining the understandable current strategy, not claiming a hiring probability or universally optimal weights. Embedding and pretrained word-vector quality were **not** evaluated live because no suitable local model was configured; failure handling is covered by tests.

Remaining improvements: add human-labelled real relevance judgments before changing weights/calibration; source-validate generic skill claims for user review rather than automatically granting proficiency; broaden the conservative location registry when real providers are added; benchmark cold/warm latency without concurrent tests; add cached local semantic embeddings only after a measured quality comparison; improve synthetic descriptions and semantic near-duplicate detection separately from this fix. Existing calibration and TF-IDF remain limited by synthetic labels and corpus dependence.

The separate LibreOffice screenshot shows an abort in `soffice`, used for DOCX preview conversion. R2 is native PDF and uses PDFium rather than LibreOffice. There is no evidence connecting that crash to this search failure; its exact cause is not established or claimed fixed by this audit.

## Inspected ranked jobs

All rows below passed the exact recorded hard filters and are included at raw threshold zero. Scores and missing skills are grounded in the confirmed profile. Rank depends on hybrid components, not location or role eligibility alone. Detailed method scores, weights, penalties and overlap evidence are in [the after reproduction artifact](ai-location-after-reproduction.json).

| Rank | Mock job ID / category | Raw score | Explicit required overlap | Missing required skills | At threshold 30 |
|---:|---|---:|---|---|---|
| 1 | demo-deep-learning-3061 / deep_learning | 48.9964 | Python, PyTorch | None | Included |
| 2 | demo-generative-ai-5505 / generative_ai | 24.1283 | Python | LLMs | Excluded by score |
| 3 | demo-artificial-intelligence-0175 / artificial_intelligence | 23.6113 | Python | Machine learning | Excluded by score |
| 4 | demo-deep-learning-3067 / deep_learning | 23.5425 | Python, PyTorch | Deep learning, Linear algebra | Excluded by score |
| 5 | demo-data-science-2811 / data_science | 23.1167 | Python | Statistics | Excluded by score |
| 6 | demo-data-science-2797 / data_science | 22.9615 | Python | Statistics | Excluded by score |
| 7 | demo-deep-learning-3065 / deep_learning | 21.5344 | Python, PyTorch | Deep learning, Linear algebra, Model evaluation | Excluded by score |
| 8 | demo-deep-learning-3064 / deep_learning | 21.2410 | Python, PyTorch | Deep learning, Linear algebra, Model evaluation | Excluded by score |
| 9 | demo-deep-learning-3049 / deep_learning | 21.1324 | Python, PyTorch | Deep learning, Linear algebra, Model evaluation | Excluded by score |
| 10 | demo-machine-learning-6225 / machine_learning | 20.2236 | Python | ML | Excluded by score |
| 11 | demo-data-science-2725 / data_science | 19.4355 | Python | Statistics | Excluded by score |
| 12 | demo-data-science-2795 / data_science | 19.3003 | Python | Statistics | Excluded by score |
| 13 | demo-natural-language-processing-6981 / natural_language_processing | 15.7446 | Python | NLP, ML | Excluded by score |
| 14 | demo-machine-learning-6221 / machine_learning | 14.9637 | Python | Machine learning, Pandas | Excluded by score |
| 15 | demo-computer-vision-1667 / computer_vision | 14.8954 | Python | Computer vision, Image processing | Excluded by score |
| 16 | demo-computer-vision-1670 / computer_vision | 14.8142 | Python | Computer vision, Image processing | Excluded by score |
| 17 | demo-machine-learning-6330 / machine_learning | 14.7139 | Python | ML, Pandas | Excluded by score |
| 18 | demo-data-science-2677 / data_science | 14.3900 | Python | Statistics, Pandas | Excluded by score |
| 19 | demo-natural-language-processing-6983 / natural_language_processing | 11.8664 | Python | Natural language processing, Machine learning, Text processing | Excluded by score |
| 20 | demo-generative-ai-5380 / generative_ai | 11.7013 | Python | LLMs, Natural language processing, REST APIs | Excluded by score |
| 21 | demo-machine-learning-6459 / machine_learning | 10.8660 | Python | ML, Pandas | Excluded by score |
| 22 | demo-machine-learning-6280 / machine_learning | 10.7649 | Python | Machine learning, Pandas, Model evaluation | Excluded by score |
| 23 | demo-computer-vision-1684 / computer_vision | 10.7069 | Python | Computer vision, Image processing, Machine learning | Excluded by score |
| 24 | demo-computer-vision-1638 / computer_vision | 10.6913 | Python | Computer vision, Image processing, ML | Excluded by score |
| 25 | demo-computer-vision-1668 / computer_vision | 10.5491 | Python | Computer vision, Image processing, ML | Excluded by score |
| 26 | demo-data-science-2834 / data_science | 10.5402 | Python | Statistics, Pandas, SQL | Excluded by score |
| 27 | demo-data-science-2777 / data_science | 10.1302 | Python | Statistics, Pandas, SQL | Excluded by score |
| 28 | demo-generative-ai-5406 / generative_ai | 9.9925 | Python | LLMs, NLP, RESTful API | Excluded by score |
| 29 | demo-data-science-2925 / data_science | 9.9731 | Python | Statistics, Pandas, SQL, ML | Excluded by score |
| 30 | demo-data-science-2672 / data_science | 8.2686 | Python | Statistics, Pandas, SQL, Model evaluation, Machine learning | Excluded by score |
| 31 | demo-machine-learning-6395 / machine_learning | 6.5069 | Python | Machine learning, Pandas, Model evaluation | Excluded by score |
| 32 | demo-generative-ai-5557 / generative_ai | 6.4546 | Python | LLMs, Retrieval augmented generation, REST APIs, Model evaluation | Excluded by score |
| 33 | demo-data-science-2922 / data_science | 6.2446 | Python | Statistics, Pandas, SQL | Excluded by score |
| 34 | demo-machine-learning-6308 / machine_learning | 6.1599 | Python | Machine learning, Pandas, Model evaluation, Statistics | Excluded by score |

The highest-ranked local job is a deep-learning entry role whose two explicit requirements (Python and PyTorch) are both supported. Lower-ranked ML/NLP/vision roles often have only Python supported from a longer requirement list, with explicit critical-skill penalties and weak lexical/project similarities. Relevant job-family retrieval therefore does not imply a strong or verified candidate fit.
