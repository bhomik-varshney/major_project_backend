# 10,000-job retrieval and matching implementation

## Outcome and scope

Exactly **10,000 deterministic synthetic listings across 56 categories** replace the previous 1,000-record fixture. Every listing retains original wording, canonical skill tokens, safe inactive URLs, source/demo flags, dates and salary availability. No live job provider, application submission, user/profile/resume rewrite or SQL job table was added. Existing saved matching runs retain their original snapshots.

The final engine searches the complete filtered corpus with TF-IDF, BM25 and a skill inverted index, fuses retrieval ranks and reranks a configurable pool (default 1,200). It retains the existing evidence-weighted hybrid formula and raw threshold of 30. Experimental calibrated percentages are separate from raw ranking/threshold scores.

## Before-change diagnosis

[Frozen diagnostic](matching-diagnostic-before.json) was recorded before modifying the corpus or engine. The earlier six-profile evaluation remains in [matching-evaluation.json](matching-evaluation.json). The initial generator allocated almost equal counts to all 46 categories and used one main stack per category; Java/Node backends, mobile alternatives, data engineering, generative AI and several specialist domains were underrepresented or absent. The 20-page/1,000-result snapshot cap would have silently truncated an expanded corpus. Complete profiles already ranked relevant jobs well; sparse Python/SQL profiles and design profiles had few opportunities with demonstrable required foundations.

| Candidate | Old listings ≥30 | Expanded listings ≥30 before pool cut | Old mean relevant raw score | New mean relevant raw score |
|---|---:|---:|---:|---:|
| backend_student | 45 | 830 | 44.91 | 31.02 |
| frontend_aliases | 55 | 1128 | 49.13 | 43.70 |
| data_analyst | 162 | 1563 | 54.50 | 56.70 |
| design_student | 26 | 260 | 31.70 | 36.30 |
| sparse_backend | 1 | 278 | 18.29 | 14.87 |
| ml_student | 63 | 723 | 48.60 | 55.28 |

The new backend mean decreases because the category now includes coherent Java/Go/Node stacks that a Python candidate has not confirmed. This is expected; higher scores across every listing would be misleading. The diagnostic JSON includes quantiles, threshold counts, common missing skills, top-ten component contributions, empty fields, duplicates and date states. Low cosine values are raw word alignment, not probabilities. Missing preferred skills and missing required evidence have distinct effects; missing candidate fields remain unknown with renormalized weights and an evidence ceiling.

## Dataset design and reproducibility

`app/providers/job_catalog.py` contains authored role recipes and relative category weights. `job_dataset.py` uses SHA-256 choices, a fixed seed (`20261019`), frozen reference date (`2026-10-19`) and largest-remainder allocation to produce an exact count. The weights express this student-oriented test corpus, not estimates of actual labor-market demand. Common software/data pathways receive more listings; each uncommon domain retains around 90 examples. Multiple coherent stacks, domain assignments, deliverables, responsibilities, title/level forms, location/work-mode combinations and exact synonyms vary the body of a requisition. Unrelated keywords occur only in labeled challenge descriptions, not random required technologies.

Version: `2026-10-demo-v3-10000`. SHA-256: `53ba456b0baf88d22070c04d9c34453ed5d6f8adfb8562edb93435eb4d1fdf4f`. Read-only JSON persistence is retained. `seed_jobs` validates then atomically replaces only a dedicated job JSON fixture; it never opens a workspace database. Tests reseed twice around a populated database containing profiles, documents, processing/audit records, confirmation receipts and actual saved matching history, comparing every table before/after.

| Category | Listings |
|---|---:|
| ar_vr | 93 |
| artificial_intelligence | 90 |
| backend | 721 |
| blockchain | 90 |
| business_analysis | 180 |
| business_intelligence | 180 |
| cloud | 270 |
| computer_vision | 90 |
| cybersecurity | 216 |
| data_analysis | 450 |
| data_engineering | 287 |
| data_science | 270 |
| database | 90 |
| deep_learning | 90 |
| developer_relations | 90 |
| devops | 271 |
| digital_marketing | 90 |
| ecommerce | 90 |
| edtech | 90 |
| electronics | 90 |
| embedded | 90 |
| ethical_hacking | 90 |
| fintech | 90 |
| frontend | 585 |
| full_stack | 585 |
| game_development | 90 |
| generative_ai | 225 |
| gis | 90 |
| healthtech | 90 |
| hr_technology | 90 |
| industrial_automation | 90 |
| iot | 90 |
| it_support | 162 |
| machine_learning | 315 |
| martech | 90 |
| mobile | 287 |
| natural_language_processing | 90 |
| networking | 90 |
| operations | 90 |
| performance_testing | 90 |
| platform_engineering | 90 |
| product_design | 162 |
| product_management | 90 |
| project_management | 90 |
| quality_assurance | 225 |
| renewable_energy | 90 |
| research | 90 |
| robotics | 90 |
| security_operations | 90 |
| site_reliability | 90 |
| software_engineering | 720 |
| systems | 90 |
| technical_writing | 90 |
| test_automation | 271 |
| ui_ux | 198 |
| web | 267 |

Experience: entry_level: 1892, fresher: 1950, graduate: 1833, junior: 1403, limited_experience: 326, student: 2596.

Work arrangements: hybrid: 2476, onsite: 5067, remote: 2457.

Frozen availability: active: 8434, expired: 1296, future: 270.

Controlled cases include missing salary/preferred skills, two-to-nine coherent requirements, overlapping/optional skills, semantic wording, unrelated keyword overlap, incomplete metadata/descriptions, unusual internship requirements, expired/future dates and three explicitly grouped near-duplicate pairs. Strict schema checks reject impossible structural conflicts; unusual but schema-valid eligibility combinations remain visibly tagged. The matching deduplicator removes 10 listings in this corpus: three explicit duplicate copies and seven additional matching business signatures. These listings remain in the 10,000-record browsing fixture. Empty fields are deliberately labeled; the validator checks unique IDs, required fields, valid categories/dates/ranges, canonical tokens, index metadata, content fingerprints, common credential patterns and byte-for-byte reseeding. See [distribution and validation](dataset-10000-validation.json).

## Retrieval methods and selection

All methods implement or return the independent `RetrievalStrategy` / `RetrievalScores` contract. TF-IDF remains separately visible. BM25 uses term-frequency saturation and document-length normalization (`k1=1.2`, `b=.75`), following the [Stanford IR text](https://nlp.stanford.edu/IR-book/html/htmledition/okapi-bm25-a-non-binary-model-1.html), with positive smoothed IDF `ln(1+(N-df+.5)/(df+.5))`. Raw BM25 units and a bounded fraction of its query saturation upper bound are both retained; neither is a probability.

| Method | Production use | Evaluation / availability |
|---|---|---|
| TF-IDF cosine | Retrieval and default lexical feature | Full corpus and holdout pairs |
| BM25 | Retrieval, independently visible score; configurable alternative lexical feature | Compared independently and within hybrid |
| Exact/synonym skill overlap | Inverted retrieval postings and evidence-backed required/preferred features | Independent strategy and exact source oracles |
| Word2Vec / fastText text vectors | Optional supplied local `.vec` artifact; unavailable by default | No suitable pretrained artifact present. Loader, malformed vectors and OOV coverage tested with explicitly toy vectors; no semantic-quality metric claimed |
| Semantic embeddings | Existing optional local Ollama adapter; content-addressed persistent cache | No configured live embedding service. Success/cache/dimension/failure paths tested using fixtures; live semantic quality not measured |
| Hybrid ensemble | Final detailed ranking | Compared with both TF-IDF and BM25 lexical variants |

The optional text-vector loader supports the [documented fastText text-vector format](https://fasttext.cc/docs/en/english-vectors.html), not fastText binary subword inference. Unknown words are ignored and coverage is reported; low query coverage makes the method unavailable. It accepts a bounded user-supplied artifact and reports its hash/vocabulary/dimensions. No model was trained on the small synthetic job corpus, no download was initiated, and lexical/hash vectors are never called pretrained semantics.

Every eligible, deduplicated listing enters retrieval; there is no first-N corpus shortcut. Positive-score top lists feed reciprocal rank fusion `sum(1/(60+method_rank))`. Their union is trimmed to `MATCHING_RETRIEVAL_POOL_SIZE` (50–10,000, default 1,200), also settable per run. Zero-signal opportunities and listings outside the pool have explicit exclusion reasons. Semantic retrieval joins only when real vectors are available; provider failure keeps lexical/skill retrieval and marks the provider failure. Detailed hybrid ranking applies only to the selected pool. The API audit stores all retrieval candidate IDs/ranks and excluded IDs/reasons. Routine run/list polling returns compact counts/method metadata; full exclusions load only through the owned audit endpoint.

## Normalization, scoring and calibration

The central `skill_taxonomy.py` is shared by browsing and matching. It distinguishes exact, synonym, related, transferable and unrelated skill relationships. Java/JavaScript, C/C++/C#, React/React Native and SQL/PostgreSQL remain distinct. Transferable/related foundations do not satisfy a required skill. Confirmed technologies/tools retain their precise profile source paths. Original job and profile values remain available alongside canonical mappings. Case/spacing/title/location aliases are normalized conservatively; a home address never becomes a relocation preference.

The transparent raw hybrid formula is unchanged in scale and weights:

```text
weights = lexical/semantic .20, required .30, preferred .08, role .12,
          projects/work .10, education .04, experience .06, location .05, interests .05
A = sum(configured weights for known components)
weighted = sum(component_score * configured_weight / A)
penalty = 15 * unconfirmed_required_count / distinct_required_count
          (only when candidate explicit skill evidence exists)
ceiling = 50 + 50*A
raw_hybrid = max(0, min(100, ceiling, weighted - penalty))
```

Semantic cosine supplies its feature only when active; otherwise a clearly named lexical feature uses the same weight. BM25 and word vectors were not blindly added as extra final-score weights. Project relevance compares confirmed project/work text with the substantive job description/responsibilities. Unknown/not-applicable features are omitted; explicit mismatches are scored separately. Every result contains the component state, original evidence, weights, contribution, penalty, ceiling, exact final arithmetic and independent method scores. The validator checks finite values, source membership, no false skill equivalence, exact explanation catalog membership/text, calculation, calibration applicability and stable ordering.

The displayed percentage uses fixed 20-point raw-score bins, Laplace-smoothed relevance frequencies and weighted pool-adjacent-violators monotonic pooling. Training uses 68 authored synthetic pairs; 68 pairs from disjoint candidate families are held out. TF-IDF training uses the full background job corpus rather than an isolated two-document cosine. The frozen artifact records the training hash, formula configuration, bin support and label counts. Numeric ranking, thresholding and tie breaking continue to use raw hybrid scores; equal coarse percentage bins do not reorder results. Calibration is withheld for an unseen semantic/BM25 configuration, changed weights/penalty/ceiling, or invalid/missing artifact. It is explicitly experimental synthetic relevance frequency, not a hiring probability.

Held-out Brier score (lower is better): calibrated **0.038735**, raw score/100 **0.039846**. The small difference does not establish robust calibration. The sparse and synthetic labels need a larger independent human-labeled benchmark before production confidence claims.

## Ranking evaluation

[Machine-readable comparison](retrieval-evaluation.json) separates broad category-based corpus labels from authored holdout suitability pairs. Across six representative profiles and 9,990 deduplicated listings, TF-IDF, BM25 and hybrid all achieve category precision@10/nDCG@10 of 1.0; skill-only precision@10 is .666667. Broad domain labels are easy proxies and do not establish employer suitability. The retrieval pool retains 100% of the exhaustive hybrid top ten for all six profiles. Recall@10 is .012964 because there are many relevant listings; that denominator differs from the smaller original corpus.

| Method | Holdout precision@3 | Holdout nDCG@3 | Holdout nDCG@10 |
|---|---:|---:|---:|
| tfidf | 0.750000 | 0.824020 | 0.959604 |
| bm25 | 0.666667 | 0.765361 | 0.955830 |
| skill_overlap | 1.000000 | 1.000000 | 1.000000 |
| hybrid | 1.000000 | 1.000000 | 0.991446 |
| hybrid_bm25 | 1.000000 | 1.000000 | 0.987169 |

The final method keeps the existing hybrid with TF-IDF lexical relevance. It improves holdout top-three ranking over lexical methods; its nDCG@10 is slightly higher than the BM25 hybrid variant. Skill-only also performs well on these narrowly authored pairs but underperforms on the broad corpus and lacks location/experience/education considerations. The report includes precision/recall/hit rate/MRR/nDCG, relevant/irrelevant score means, thresholds and elapsed time. Four original skill-oracle cases produce eight exact matched/missing-list checks, all correct. The 95,731 explanation-ID checks show 100% structural support and zero unsupported reasons; this is catalog/source consistency, not independently human-judged explanation accuracy. Semantic fallback occurs intentionally in every offline case because no embedding service is configured; no configured-service failure is concealed.

## CrewAI architecture and frontend

Career Orchestrator is a genuine `crewai.Flow`. Schema-checked local actors cover Candidate Profile Preparation, Job Normalization, Multi-Method Retrieval, Hybrid Ranking, Score Calibration, Recommendation Explanation and Ranking Validation, plus the separately traceable TF-IDF baseline stage. Each trace has an execution ID, input/output contract, dependency, responsibility, failure policy, method and elapsed time. Deterministic preparation/scoring stages are Flow actors, not eight independently billed LLM agents. With per-run consent, the existing actual two-agent CrewAI Crew selects only grounded explanation IDs and validates selections. It cannot change scores, eligibility facts, profiles or listings. Timeout/cancellation/invalid AI output retain only validated checkpoints. Groq key slots 1/2/3 continue through the existing backend-only rotating manager; provider-failure and rotation regression tests remain in the full suite.

`/matches` keeps the existing rose/dark interface with ranked synthetic cards, experimental percentage, raw hybrid/TF-IDF, matched/missing/transferable skills, location state and short grounded reasons. Expandable sections show method scores (including unavailable methods), component arithmetic, calibration, job details, normalized sources, candidate snapshot, run timestamp, retrieval candidates/exclusions and actual model metadata. The retrieval-pool control is in advanced filters. Existing loading/error/empty/bookmark/stale/cancel/retry behavior remains. Desktop and 390px mobile screenshots were inspected; the mobile header now puts the score on a separate row so titles retain usable width.

## Verification and performance

- Dataset validation: exactly 10,000 records, 56 categories, reproducible bytes, unique IDs and credential scan passed.
- Full-corpus spawned performance: 10,000 input listings, 9,990 after deduplication, 8,819 positive-signal results reranked with a 10,000 pool cap in 17.045s; all eight stages completed, zero score/unsupported-reason errors. Zero-signal listings remain explicitly excluded. See [performance](retrieval-performance-all-10000.json).
- Real isolated HTTP smoke: 8,430 active unique listings searched, 1,200 reranked, 767 results ≥30, 10.671s through matching/audit/bookmark checks; warm Python search median 23.304ms. Profile remained identical. See [API smoke](retrieval-api-smoke.json).
- Real Groq smoke: `completed`, 3 synthetic top recommendations reviewed, all numeric/evidence validation checks passed. No private resume/profile was loaded. See [live smoke](retrieval-live-smoke.json).
- Complete browser suite: 32 passed; four matching tests passed again after the mobile header adjustment.
- Type checking, linting and production build passed; lint retains ten pre-existing Fast Refresh warnings.
- Backend: the complete 310-test suite passed, followed by 46 passing focused compatibility/job checks covering the added regression (311 unique backend tests covered).
- All 1,000 historical v2 listings and all 1,683 stored ranked results validate under the new schemas. Version-specific C/C++ token validation preserves original v2 snapshots; v3 matching retains the conservative distinct taxonomy.
- Main database: all 13 table fingerprints unchanged, integrity `ok`; 7 profiles, 8 verification receipts and 12 matching runs retained. See [database checks](retrieval-database-check.json).
- Main backend health and frontend `/matches` and `/jobs` return HTTP 200. Existing dataset runs show a notice to create a new run against the current 10,000 listings.

Commands (backend `.venv` activated; frontend commands in frontend repository):

```sh
python -m scripts.seed_jobs
python -m scripts.validate_jobs
python -m scripts.evaluate_retrieval --fit-calibration
python -m scripts.smoke_matching --pool-size 10000
python -m scripts.smoke_matching --live
python -m scripts.smoke_retrieval_api --base http://localhost:8001/api/v1
python -m pytest
npm run typecheck
npm run lint
npm run build
E2E_BASE_URL=http://localhost:5174 E2E_API_BASE_URL=http://localhost:8001/api/v1 npx playwright test
```

Use an isolated test database/server for HTTP/browser suites; ordinary tests disable live Groq keys. Refit calibration only intentionally and restart backend workers afterward so the frozen artifact is consistent across processes. Existing provider interfaces remain ready for later Adzuna/Arbeitnow adapters; neither service is integrated.

## Limits and next milestone

Synthetic job and relevance templates are useful regression fixtures, not evidence of general hiring accuracy. Live semantic/word-vector quality is unmeasured without suitable configured models. Exhaustive retrieval/optional embedding preparation still scales linearly with this corpus; 10,000 works locally, while a production corpus needs a persistent retrieval index, offline embedding precomputation and a production worker/queue. Source-grounded skill evidence does not establish proficiency, country eligibility or employer acceptance. The next milestone should collect a larger independently judged set of real career-query/job pairs, evaluate a configured semantic model against these lexical/hybrid baselines, and recalibrate on held-out human labels before live-provider integration.
