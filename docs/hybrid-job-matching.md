# Hybrid job matching milestone

Implemented for the existing Ascendra student workspace. All opportunities remain seeded/mock data. No live job adapters, application submission, cover letters or production authentication were added.

## What runs

`CareerMatchingFlow` is an actual `crewai.flow.Flow[MatchingState]`, with decorated sequential stages:

| Stage | Responsibility | Execution |
| --- | --- | --- |
| Candidate Profile Preparation | Load explicit confirmed fields, preserve original values and normalized skill/source mappings | Deterministic Flow task |
| Job Preparation and Normalization | Validate immutable provider records, normalize fields, remove duplicate groups/content | Deterministic Flow task |
| TF-IDF Baseline Matching | Fit a sparse text corpus; compute separate cosine scores | Deterministic Flow task |
| Hybrid Matching and Ranking | Combine evidence components, optional real embeddings, penalties and uncertainty cap | Deterministic Flow task |
| Recommendation Explanation | Build supported reason catalog for every result; optionally select highlights with Groq | Rules, optionally real Crew |
| Ranking Validation | Check numeric calculations, evidence membership, reason catalogs, duplication and stable order | Deterministic Flow task plus optional model review |

With explicit per-run consent, two actual `crewai.Agent` instances execute typed, dependent `crewai.Task` objects in a sequential `crewai.Crew`: **Recommendation Explanation Agent** and **Ranking Validation Agent**. They choose/review existing reason IDs for the configured top K qualifying recommendations. They cannot generate profile facts, new job records, numeric scores or free-form explanation claims. Detailed explanations for all other jobs remain grounded rule outputs. This is deliberately not described as six autonomous LLM agents: deterministic matching should not spend six model calls per job.

Agents have no tools, delegation, code execution, automatic memory, planning or tracing. Native structured responses use the existing Groq transport. Each agent has one bounded call; the transport retains its bounded key rotation/retry policy. Framework retries cannot expand the model budget. Invalid output, unsupported IDs, incomplete selections or contradictory review results retain deterministic explanations atomically.

References for the integration: [CrewAI Flows](https://docs.crewai.com/en/concepts/flows), [Ollama embed endpoint](https://docs.ollama.com/api/embed). Behavior is also tested against the installed CrewAI 1.15.28 code.

## Confirmed data boundary

`confirmed_fields()` is the sole candidate source. A fully verified current profile is eligible. Partial verification receipts supply only fields whose current values still equal the confirmed values. A changed field loses confirmation; unchanged, previously confirmed fields can remain eligible. Empty values stay empty. The matcher never loads a document extraction draft, document image, resume audit or unconfirmed model output.

Matching uses skills, objective/summary, projects, work experience, education, preferred roles, preferred locations, current location as text context, and career stage. Name, email, phone and document identifiers are excluded. Exact matching accepts explicit profile skills and explicit project/work `technologies`; mentioning a technology in prose alone does not establish skill proficiency. User interests are derived only from explicitly confirmed objective/preferred-role text. Remote preference comes only from an explicit preferred location such as Remote; a home address never implies willingness to relocate.

Profile facts and verification receipts are read-only throughout matching. Each run snapshots confirmed facts, revision and a canonical SHA-256 fingerprint. A changed revision or changed confirmed-data fingerprint marks results stale. Retrying loads current confirmed fields rather than replaying old profile facts.

## Baseline

Text includes explicit candidate fields and job title/category, description, responsibilities, required/preferred skills, education and experience level. Links are excluded. Unicode/case normalization and a small explicit alias list precede word unigrams and adjacent bigrams. C, C++, C#, Java/JavaScript, React/React Native, and SQL/PostgreSQL remain distinct skills; C/C++ stays a combined token rather than implying both skills independently.

For corpus size N and document frequency df:

- TF = 1 + ln(term count)
- IDF = ln((1 + N) / (1 + df)) + 1
- Vectors use L2 normalization; score = 100 × cosine, clamped to 0–100.

The corpus is the candidate plus the unique jobs selected for this run. A filtered corpus can therefore change IDF and baseline scores; scores from different runs/corpora are not calibrated probabilities. The method version, corpus size and vocabulary size are persisted. Baseline remains separately visible even when embeddings are active. A pure-Python sparse implementation avoids introducing a heavy ML dependency for TF-IDF.

## Hybrid formula and rationale

Initial configurable weights total 1.0:

| Component | Weight | Initial rationale |
| --- | ---: | --- |
| Semantic cosine / labelled TF-IDF fallback | 20% | Broader textual relevance without dominating explicit requirements |
| Required skills | 30% | Strongest directly inspectable suitability evidence |
| Preferred skills | 8% | Useful enrichment below required requirements |
| Title/category | 12% | Explicit career direction |
| Projects/work relevance | 10% | Applied evidence beyond a skill list |
| Education | 4% | Small contribution because qualifications often have alternatives |
| Experience | 6% | Dated work where available, otherwise a labelled career-stage heuristic |
| Location/work mode | 5% | Explicit preference, with eligibility caveats |
| Interests | 5% | Confirmed objective/role text; kept small to limit repeated textual influence |

These are documented project-design priors, not learned/optimized weights or evidence of general accuracy. Change `MATCHING_WEIGHTS` as JSON; validation requires exactly the documented keys, nonnegative weights and sum 1. Each run stores the resolved configuration.

Each known component is 0–100. Unknown and not-applicable components use `score=null`, not zero. States distinguish confirmed match, likely match, missing information, explicit mismatch and not applicable. If A is the sum of available configured weights, effective weights are w/A; each contribution is component score × effective weight. TF-IDF takes the semantic component's 20% weight only when embeddings are unavailable, and is named `lexical_similarity`; semantic itself remains null with zero effective weight.

Required/preferred overlap uses distinct explicitly equivalent tokens, with original values and candidate/job source paths. A preferred token repeated as required is not counted twice. Missing means **no equivalent skill in confirmed evidence**, not proof of inability. Explicit skill evidence that matches none of the requirements gives a known overlap score of zero; no explicit candidate skills gives unknown overlap. Transfer relationships (for example JavaScript→TypeScript) are separately explained and never satisfy exact required skills.

All listed required skills are treated as critical evidence requirements in this initial policy. Penalty P = `MATCHING_CRITICAL_PENALTY` (default 15 points) × missing required fraction. If candidate skill evidence or usable requirements are absent, P=0 and the skill component is unknown/not applicable.

To prevent a nearly empty profile from scoring 100 from one known component, uncertainty ceiling C = B + (100−B) × A, where B=`MATCHING_UNKNOWN_CEILING_BASE` (default 50). Final score = max(0, min(100, C, sum(contributions)−P)). The UI exposes A, C, P, weighted contributions and the complete formula. Default recommendation threshold is `MATCHING_MINIMUM_SCORE=30`; results below it are retained for auditing but excluded from recommendation pages. A result-page filter can raise that threshold; lowering it requires recalculation. A score is not a hiring probability or an employer eligibility decision.

Experience months require explicit parseable month/year intervals; overlapping work is merged, future work does not count, and end dates are capped at the run reference date. Internship equivalence is not assumed. Career-stage and qualification-level comparisons are labelled heuristics; education subject, graduation status, work authorization, language, time zones and geographic eligibility still need employer review. The posted upper experience bound is shown but is not treated as a hard rejection of an overqualified candidate.

## Embeddings and fallback

`EmbeddingProvider` separates vector generation from scoring/cache. `OllamaEmbeddings` supports a genuinely running local `/api/embed` service, batches up to 32 inputs, validates vector shape, consistent dimensions, finite numbers and nonzero magnitude, and retries transient failure once within a 30-second embedding-stage budget, additionally reserving five seconds of the shared deadline for deterministic fallback and validation. Configure this with `MATCHING_EMBEDDING_TIMEOUT_SECONDS`. It sends `truncate=false`, so input truncation is not silently accepted. No model is downloaded automatically. Candidate text and project/work text are embedded independently; jobs receive full matching-text embeddings.

Default `MATCHING_EMBEDDING_PROVIDER=none`: no semantic model is claimed active. TF-IDF/skill-based hybrid results complete normally with an explicit semantic-unavailable label. A configured but failed embedding provider marks the run partial and preserves deterministic scoring. Metadata records provider/model/status, vector dimensions and cache hits/misses when active.

SQL cache keys hash provider, model, text and scope. Candidate caches are user-scoped; synthetic job text caches are shared. Identical input deduplicates within batches; invalid cached vectors are discarded, concurrent inserts are harmless, and cross-batch dimension conflicts trigger fallback. Keep a model identifier stable for reproducibility; replacing a mutable model tag requires cache invalidation/model renaming. There is no automatic cache eviction in this milestone.

No embedding service/model is configured in the delivered development environment. Embedding success is verified with fixture vectors and mocked Ollama transport, not claimed as a live semantic-quality evaluation. Groq's configured chat model is not misused as an embedding endpoint.

## Worker, failures and persistence

Matching has its own bounded executor (two workers, four queued/running slots). A database uniqueness lease prevents simultaneous runs for the same user. Different users can run independently. The matching worker shares the existing process-safe three-key manager with resume processing. Rotation handles rate limits, quota/temporary provider errors and retryable network failures; credentials remain backend-only and are never persisted in matching records or responses.

A spawned process isolates CrewAI, observes a shared deadline (default 120 seconds), reports stage updates and emits a validated deterministic checkpoint before optional model work. Cancellation/timeout terminates the child and releases key leases. A timeout/AI failure after the checkpoint yields partial validated results. Failure before a valid checkpoint yields a failed run. Publishing checks the active status atomically, so cancelled runs cannot later publish results. Restart recovery fails interrupted runs and clears leases; retry creates a fresh run. An empty job set completes with zero results and skips AI review even if requested.

Statuses: queued → preparing → normalizing → calculating_baseline → calculating_hybrid → generating_explanations → validating → completed/partial; failed and cancelled are terminal alternatives. Actual stage timings, methods, model names, warnings and validation counts are recorded. Provider diagnostics use safe error codes, not arbitrary exception text.

Additive SQL tables: `matching_runs` (candidate/config/provider/task snapshots), `matching_results` (original and normalized jobs, separate scores, components, explanations and evidence), `embedding_cache`, `saved_opportunities`. Existing tables/columns are untouched. Results cascade when a run is deleted. Bookmarks store owned stable job snapshots. The live development database was backed up before migration and checked for preservation of every preexisting table's rows.

Ranking is stable by descending hybrid, descending baseline, then ascending stable job ID. Duplicate groups and otherwise identical title/company/location/work-mode/required-skill signatures choose the lowest stable ID; excluded IDs are audited. The 1,000-job fixture currently yields 997 unique records before availability filters. Source job snapshots are checked unchanged before publication. Validation verifies score ranges, contribution/weight/coverage/cap/penalty math, exact required/preferred evidence, reason catalog integrity, selected IDs, exact explanation text, contradictions, uniqueness, thresholds and tie order.

## API contracts

All routes below are under `/api/v1/matching`, require the existing owned local browser session and workspace client header, and have typed OpenAPI response models. Private runs and bookmarks cannot be read through another session.

| Method and route | Contract |
| --- | --- |
| GET `/readiness` | Confirmed fields, excluded unconfirmed fields, missing facts, current revision and eligibility |
| POST `/runs` | `filters: JobFilters`, optional `minimum_score`, `use_ai=false`, `allow_external_processing=false`; returns 202 + saved run |
| GET `/runs` | Latest 20 owned runs |
| GET `/runs/{id}` | Status, counts, stale flag, timestamps, config, task/provider metadata and warnings |
| GET `/runs/{id}/results` | Page/page_size (1–50), keyword, category, country, work_mode, repeated skill, minimum_score, saved_only, sort=hybrid/baseline; returns run/items/total/page/page_size |
| GET `/runs/{id}/audit` | Confirmed candidate snapshot, normalized representation, run metadata and explicit draft_data_used=false |
| GET `/runs/{id}/results/{job_id}` | Full recommendation, normalized job and candidate snapshot |
| GET `/runs/{id}/results/{job_id}/explanation` | Grounded text, reasons/evidence, method, highlighted IDs and stale flag |
| POST `/runs/{id}/cancel` | Cancel only an active run |
| POST `/runs/{id}/retry` | Fresh run using current confirmed fields and previous filters/options |
| DELETE `/runs/{id}` | Delete inactive owned run and its results |
| GET `/bookmarks` | Owned saved job snapshots |
| PUT / DELETE `/bookmarks/{job_id}` | Idempotent save / remove |

Example local start: `{"filters":{"availability":"active","category":"backend"},"minimum_score":30,"use_ai":false}`. AI requires both consent flags true. An unconfirmed/no-useful-facts profile returns 409 `confirmed_profile_required`; duplicate active runs return 409; queue exhaustion returns 429; result requests before completion return 409. Errors retain existing sanitized operation IDs.

Start filters reuse the existing job search service: keyword, all selected skills, category, location, work mode/remote, experience, employment type, company, industry, country, date range and availability. Matching can compare the full 1,000 or a filtered subset. Availability uses the displayed dataset reference date, never an implied live opening. Future source providers can replace the existing `JobProvider.snapshot()` adapter; this milestone retains the strict seeded/mock record contract and inactive application URLs.

## Frontend

`/matches` replaces its planned page with the existing dashboard design: readiness, explicit confirmed-field use, comprehensive search controls, Find Matching Jobs/recalculate, stage progress and cancel, saved-run selection with time, failed/cancelled retry, empty/error/loading states, stale warning, ranked cards, bookmarks and pagination. Cards show separate hybrid/baseline scores, exact/missing required and preferred chips, source labels and work location. Expandable panels show all grounded reasons/evidence, component bars/calculations, job details and normalized mappings. Run audit shows candidate facts used, tasks, configuration, provider metadata and validation. No prompts, credentials or private document images are exposed.

Inputs wait until readiness loads, preventing early typing from being reset during initialization. AI consent is off by default; no profile is changed by matching. The dashboard links to matching and shows the latest current run's recommendation count. Existing browsing/filtering routes and planned application/skill-gap routes remain functional.

## Evaluation and validation

See `matching-evaluation.json` for actual reproducible measurements and `tests/fixtures/matching/cases.json` for six authored synthetic career profiles. Relevance uses documented category labels, not the matcher's scores. Hybrid and baseline are compared on the same 997 unique jobs before thresholding; served hybrid metrics additionally show the effect of threshold 30. Precision@10 always divides by 10, padding absent recommendations as irrelevant. Recall denominators include all category-labelled relevant jobs. NDCG@10 uses binary category relevance.

Evaluation results: hybrid precision@10 **0.983333**, recall@10 **0.181764**, NDCG@10 **0.988958**; baseline **0.950000**, **0.178393**, **0.965158**. With the default threshold, served hybrid precision@10 is **0.833333**, NDCG@10 **0.858973**, mean recall across all served recommendations **0.584858**. The sparse backend profile receives only one recommendation at 30; lower thresholds broaden output. Do not hide this precision/coverage tradeoff.

Eight independent hand-written required/missing-skill set checks passed. All **9,222** selected reason-ID checks stayed inside the stored catalogs, and exact explanation reconstruction had zero unsupported outputs. Repeated full rankings were identical for all six profiles. All six evaluation cases deliberately used semantic fallback (100% fallback frequency). Measured mean in-process Flow time was approximately **1.06 seconds**; this excludes spawned-worker startup, persistence, browser rendering and live model latency. These small synthetic results are regression evidence, not general extraction, semantic or hiring accuracy claims.

The first synthetic live Groq review exposed an overly broad reviewer rule (requiring an optional location highlight). Explicit allowed error codes and mandatory-reason IDs corrected that disagreement without weakening source checks. Final synthetic-only live smoke: real spawned worker, **997 jobs**, both CrewAI agents completed through `openai/gpt-oss-120b`, **3 AI-selected grounded explanations**, **0 unsupported reasons**, **0 score errors**, stable ordering. No private candidate/resume data was loaded or exported for this smoke test. The safe summary is saved in `matching-live-smoke.json`.

Final verification (2026-10-20):

| Check | Actual result |
| --- | --- |
| Full backend `python -m pytest` | **263 passed**; existing dependency deprecation warnings remain |
| New matching-focused tests | **42 passed**; confirmed-only isolation, score/evidence checks, aliases, missing fields, embeddings/cache/retry/fallback, actual Crew tasks, malformed/conflicting AI output, shared-key rotation, timeouts/cancellation/checkpoints, multi-user concurrency, pagination/filtering, stale/retry/deletion and restart recovery |
| Chromium `E2E_BASE_URL=http://localhost:5174 E2E_API_BASE_URL=http://localhost:8001/api/v1 npx playwright test` | **31 passed**, including existing document/profile/audit/job regressions and four new matching browser cases |
| `npm run typecheck` | Passed |
| `npm run lint` | Passed: 0 errors, 10 preexisting Fast Refresh warnings |
| `npm run build` | Passed; no deployment performed |
| Python compilation / both repositories' `git diff --check` | Passed |
| Live synthetic Groq worker smoke | Completed, 997 unique jobs, two actual agents, 3 reviewed explanations, 0 score errors/unsupported reasons |
| Independent API smoke | Completed, 837 unique active jobs, 70 recommendations for its synthetic profile, paged results/explanations consistent |
| Desktop / mobile visual inspection | Reviewed 1440px and 390px screenshots; no horizontal overflow |
| Additive migration / preservation | Four new tables; all rows in **9 preexisting tables unchanged**, including 7 profiles; private backup saved before initialization |
| Synthetic ranking evaluation | Six profiles × two identical runs; detailed metrics above and in JSON |

Browser and API testing used a separate temporary SQLite database and disabled all Groq keys. Live Groq testing used only a built-in synthetic candidate, never private user documents. The normal localhost backend was restarted with the final implementation; the existing frontend remains on port 5173.

## Known limits and next milestone

Weights, education/career-stage rules, binary category evaluation and transfer relations need broader human evaluation. Remote match does not imply geographic eligibility. Interest/text components have correlated evidence; weights intentionally limit their influence. Thresholds and scores are not calibrated across corpora/providers. Semantic quality has not been measured against a running local embedding model. Dataset availability is intentionally frozen. AI review highlights only top K; all other explanations remain deterministic. Run histories/cache need a retention policy before multi-user production. The current session model remains local development access.

Recommended next milestone: confirmed-job skill-gap analysis and a larger human-labelled relevance set, with a locally configured embedding model and measured ablations of semantic versus lexical scoring. Live provider adapters can follow with explicit real-source validation and availability policies.
