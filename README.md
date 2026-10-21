# Agentic AI Career Companion — backend

FastAPI backend for the CareerOS student workspace. Implements persistent profiles, private document storage, PDF/DOCX/TXT text extraction, source-linked review drafts, explicit confirmation, and database-backed dashboard summaries. A genuine two-task CrewAI Resume Intelligence Crew uses Groq only when configured and explicitly selected for that document. 10,000 reproducible, clearly labeled seeded jobs across 56 categories support browsing and filtering; live job retrieval remains planned; confirmed-profile matching is implemented. See the [CrewAI report](docs/crewai-jobs-milestone.md) and current [10,000-job matching report](docs/job-matching-10000.md).

The next milestone adds [Skill Gap Analysis and Career Guidance](docs/skill-gap-guidance.md): a genuine CrewAI Flow, optional grounded two-agent Groq review, multi-job aggregation, priority calculations and temporary score-impact estimates using the existing scorer. Confirmed profiles and matching history remain read-only inputs.

[Application Preparation and Tracking](docs/application-preparation-tracking.md) adds confirmed-fact form mapping, document selection, revision-bound prefill approval, a real MCP App over stdio, replayable AG-UI events, A2UI interaction surfaces, and a persistent application timeline. A real six-agent CrewAI review is optional; safe local preparation uses a genuine CrewAI Flow. The default adapter is a clearly labelled local demo for the synthetic job provider. External submission is never performed by the backend or agents.

[Application workspace continuation](docs/application-recovery-continuation.md) adds durable partial-prefill recovery, separate opened/prefilled/submitted milestones, explicit confirmation dialogs, richer field mappings, typed tracking/approvals, follow-up completion and eight real-stdio mock scenarios. The default remains mock mode; no employer submission is automated.

[Readable application previews](docs/application-readable-preview.md) now transform confirmed profile structures into grouped cards and employer-ready text, classify professional/project links, prepare evidence-grounded answer drafts, and generate a strict preview through the local MCP server. Form, Preview, Audit and Tracking views keep user review separate from external prefilling and submission. Existing drafts remain readable without automatic data changes.

## Local setup

Use Python 3.12 or 3.13 (verified with Homebrew Python 3.13.2). From this repository:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
python -m app.db.migrate
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --no-access-log
```

OpenAPI documentation: <http://localhost:8000/docs>. Health: <http://localhost:8000/api/v1/health>.

Run the frontend on `http://localhost:5173` and configure its `VITE_API_BASE_URL=http://localhost:8000/api/v1`. Use the same hostname on both sides: mixing `localhost` and `127.0.0.1` prevents the browser session cookie from working reliably.

## Configuration

| Variable | Default / purpose |
| --- | --- |
| `APP_ENV` | `development`; this milestone supports only local development and tests. |
| `DATABASE_URL` | `sqlite:///./.data/careeros.db`; persistent local SQLite. |
| `DATA_DIR` | `.data`; stores the locally generated signing secret. |
| `SESSION_SECRET` | Optional, at least 32 characters. Otherwise generated once in `DATA_DIR/session-secret` with owner-only permissions. Never expose it to the frontend. |
| `CORS_ORIGINS` | Comma-separated loopback frontend origins; defaults include localhost/127.0.0.1 on 5173 and 8080. |
| `MAX_DOCUMENT_BYTES` | `5242880` (5 MiB). |
| `MAX_DOCUMENT_SIZE_MB` | Optional MiB override (5 in the example); takes precedence over `MAX_DOCUMENT_BYTES`. |
| `MAX_DOCUMENTS_PER_USER` | `20`. |
| `DOCUMENT_STORAGE_PATH` | `.data/documents`; private temporary parser working directory. Original bytes remain transactionally stored in SQL, outside the frontend. |
| `MAX_EXTRACTION_PAGES` | `50`; larger PDFs fail with a structured limit error. |
| `MAX_EXTRACTION_CHARS` | `60000`; longer text is truncated with a review warning. |
| `GROQ_API_KEY_1`, `_2`, `_3` | Optional backend-only rotating credential slots. Empty slots are skipped. |
| `GROQ_API_KEY` | Legacy slot-1 alias, used only when `_1` is empty. Duplicate key values are deduplicated. |
| `GROQ_MODEL` | `openai/gpt-oss-120b`. |
| `GROQ_TIMEOUT_SECONDS` | `30`; per-request timeout, capped by the remaining crew deadline. |
| `GROQ_MAX_OUTPUT_TOKENS` | `3500`; bounded extraction output reservation (512–8000). Larger reservations may exceed your provider tier. |
| `GROQ_KEY_COOLDOWN_SECONDS` | `5`; temporary failures cool down exponentially, capped at 120 seconds. |
| `CREW_TIMEOUT_SECONDS` | `75`; total child-process time budget, including framework startup and requests (maximum 120). |
| `AUTO_INIT_DB` | `true`; add missing tables at startup. |

No external credentials are required for local extraction. Keep any optional Groq key in the ignored backend `.env`; never use a `VITE_*` variable. `.env`, `.data`, and `.venv` are ignored. `.env.example` contains placeholders only.

## Database and identity

SQLAlchemy supports local SQLite and a PostgreSQL connection such as `postgresql+psycopg://user:password@localhost:5432/careeros`; the PostgreSQL driver is included. SQLite is tested; a live PostgreSQL instance has not been exercised. `python -m app.db.migrate` uses additive `Base.metadata.create_all`, also available at startup with `AUTO_INIT_DB=true`. The multimodal increment adds `resume_processing_audits` and `resume_page_artifacts` alongside the existing agent-run metadata, bringing the schema to nine tables; existing columns and saved facts remain intact. Stop the application and back up an existing database before upgrading. This command does not alter existing tables; future column changes require versioned migrations. See [resume processing milestone](docs/resume-processing-milestone.md).

Implemented entities:

- `User`: UUID identity issued by the server for one local browser session.
- `CandidateProfile`: one per user; typed profile facts stored as JSON, revision, explicit verification state, timestamps.
- `Document`: owner, validated metadata, hash, stored bytes, upload/extraction states, timestamp.
- `Activity`: owner-scoped, dated workflow events without profile contents or filenames.
- `DocumentExtraction`: versioned owner/document-linked processing run, typed draft values, source evidence, private text blocks, warnings, revision, and workflow timestamps; linked to the candidate profile when one exists or is confirmed.
- `ResumeAgentRun`: one per new extraction, agent name/version, execution engine, task stages/results, warnings, safe error code and timestamps; no verified profile writes.
- `VerificationRecord`: confirmation receipt linked to the profile and optional extraction/document, selected values/sections, resulting section snapshots, source evidence, and confirmation time.

Education, experience, projects, skills, certifications, and achievements reuse typed nested JSON profile facts. Drafts and receipts keep evidence separate from confirmed facts; duplicate normalized skill/education tables are deferred. JSON works with both supported database dialects; relational ownership and uniqueness are enforced.

The frontend calls `POST /session` once to establish an HttpOnly, signed, SameSite=Lax browser cookie. The backend derives ownership from that cookie, never from a client-supplied owner ID. Private routes require `X-Workspace-Client: careeros-web` and reject unapproved origins. Non-loopback private clients are rejected in development. The header is a CSRF/preflight control, not a secret or login token.

This is **local browser identity, not production authentication**. The API must remain bound to loopback; proxying a public service to it is unsupported. Cookies last 30 days. Clearing cookies or changing browser loses access to that workspace; account login/recovery is planned. Keep `.data` private. Local SQL storage is not encrypted by this application.

## Implemented API

All paths below begin with `/api/v1`. Schemas and examples are available at `/docs`.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/health` | Service/version/database connectivity; 503 when database is unavailable. |
| POST | `/session` | Create or resume a local browser workspace. |
| POST | `/profile` | Create a profile; 409 if one already exists. |
| GET | `/profile` | Read own profile; 404 before creation. |
| PUT | `/profile` | Replace own profile using `expected_revision`; advances revision and clears verification. |
| POST | `/profile/verify` | Explicitly confirm the exact saved revision with `confirmed: true`; advances revision. |
| GET | `/documents/policy` | Allowed formats, byte/count limits, and extraction availability. |
| POST | `/documents/upload` | Multipart `file`; validate and store; identical owner/content returns the existing document. |
| GET | `/documents` | List own document metadata, newest first. |
| GET | `/documents/{id}` | Read own document metadata; no raw-content disclosure. |
| DELETE | `/documents/{id}` | Delete own document, bytes, extraction runs/text, and receipt source quotes; active processing must finish or be cancelled first. Approved profile facts remain. |
| POST | `/documents/{id}/process` | Start a versioned local run; optional `use_groq` requires `allow_external_processing`. `restart` explicitly starts another completed run. |
| GET | `/documents/{id}/status` | Durable state, stage, run/revision, safe errors/warnings, timestamps. 404 before the first run. |
| GET | `/documents/{id}/extracted-data` | Review values, field evidence, current profile revision/values, applied sections. |
| PUT | `/documents/{id}/extracted-data` | Save edited draft with processing ID/expected revision; edited leaves are labeled user corrections. |
| POST | `/documents/{id}/verify` | Confirm selected saved sections using draft/profile revisions and `confirmed: true`; explicit `replace_fields` required for replacement. |
| POST | `/documents/{id}/request-correction` | `action: correction`, `reject`, or `cancel`, with draft ID/revision. Does not change the profile. |
| GET | `/jobs` | Search/filter 10,000 seeded demonstration jobs; bounded pagination, date and availability filters. No external requests or application submission. Matching has a separate `/matching` API. |
| GET | `/jobs/options` | Dataset-derived categories, industries, countries, work modes, employment/experience options, version and reference date. |
| GET | `/dashboard/summary` | Actual profile completion, total/reviewed document-derived skill counts, document count, verification, and recent activity. |

Profile creation requires a name. Other fields are optional; invalid fields are rejected rather than invented. Manual verification confirms the whole saved profile. Document confirmation applies selected top-level sections only; lists append unique entries unless replacement is explicitly selected. A different existing scalar requires explicit replacement. Incomplete selected education/work records must be corrected before confirmation. Relevant manual edits clear profile verification. Concurrent stale writes return 409 without overwriting saved facts. Profile deletion is deferred because full owner-data deletion/retention semantics need a dedicated workflow.

Profile completion is the percentage of ten non-empty fields/sections: name, email, location, objective, education, skills, projects, preferred roles, preferred locations, and experience level. It is not a candidate-quality or hiring score. Prior employment is not required for completion.

Documents support PDF, DOCX, and UTF-8 TXT. Extension, declared MIME, nonempty content, size, and integrity are checked. Encrypted/corrupt PDFs, invalid DOCX containers/XML, and invalid text are rejected. Scanned PDFs use bounded local Tesseract OCR when available; unreadable or unavailable OCR produces an explicit warning/manual-review route. PDFium renders private PDF previews, and local LibreOffice enables DOCX page previews. Bytes and metadata commit together. Existing `upload_status=stored` and legacy `extraction_status` remain compatible; additive `processing_status` and `verification_status` expose the workflow. The count/size/parser limits bound this local prototype.

Processing runs through queued, text preparation, local identification, optional CrewAI extraction and dependent quality validation, deterministic source/quality checks, and review preparation. Extraction completion produces `awaiting_review`, never a verified profile. Corrections/rejection/cancellation are explicit states. Confirmation atomically writes the profile, receipt, extraction verification, and activity. A single bounded in-process worker is intended for one local application process; interrupted runs become failed/retryable at startup, rather than silently resuming or calling Groq again.

Local parsing recognizes expanded heading synonyms and complete Summary/Objective/Profile statements. It groups project headings/bullets and explicit technologies, work entries, and labeled/table education. PDF block positions and DOCX paragraph styles, lists, tabs, and table order are retained where available; possible columns trigger uncertainty warnings. Complex layouts can still be missed. CrewAI uses real agents/tasks, sequential dependencies and a custom Groq LLM with compact native structured output; accepted leaves must match real quotes and the correct section/entry group. Unsupported/inferred leaves are withheld. Confidence labels are heuristic and source matching is not a semantic correctness guarantee: the student must review every selected section. Missing-key/provider/JSON errors retain the local draft and show a limited-mode warning.

Dashboard `data_mode=live_local` identifies actual database data. Recommendation counts come from the latest current matching run; application status and pending-review counts come from owned application records. Jobs remain `feature_states.jobs=seeded_demo`; applications are available. Seeded vacancies never count as recommendations. `profile_skill_count` counts current profile skills; `extracted_skill_count` counts distinct current resume-derived skills in confirmation receipts, excluding user-entered review additions/corrections. Unreviewed suggestions never count as confirmed skills.

## Errors, persistence, and deletion

Errors use `{ "error": { "code", "message", "operation_id", "fields" } }`. Field errors omit submitted values. Database/internal errors omit connection strings, provider secrets, and document content. Responses carry operation IDs and `Cache-Control: no-store`; structured logs record method/status/time without request bodies. Save/upload success is returned only after commit. Saved data survives backend restart, and duplicate uploads do not duplicate activity.

For now, profiles, documents, and activity remain until explicitly removed or the local database is reset. Document deletion removes bytes, extraction text, audits and rendered page blobs from the active database and scrubs receipt source quotes. Approved profile facts/confirmation snapshots and historical source name/ID remain; the profile marks that source unavailable. Browser cookie expiry does not delete data. Full account deletion, retention schedules, backups, SQLite page reclamation, and recovery remain unimplemented. Stop the backend before deliberately removing a disposable `.data` directory; this removes all local records and the signing secret, not external backups.

## Tests

```sh
python -m pytest
```

## Skill-gap analysis

Open `/skill-gaps` after confirming profile facts. Analyse a selected fixture job, selected saved recommendations, top-ranked results, or a role/category target. The service stores immutable source snapshots, typed comparisons/gaps, frequency and priority components, simulations, task/provider traces and review annotations. Only `required_skills` creates mandatory skill requirements; preferred entries stay preferred and other listed tools remain optional. Missing evidence is not proof of inability.

`POST /api/v1/skill-gaps/runs` accepts `mode=single|selected|top_ranked|target`, `job_ids` (up to 30), optional owned `matching_run_id`, `top_k` (1–30), existing `JobFilters`, and explicit `use_ai`/`allow_external_processing`. Status, paginated results, audit, per-gap evidence, review, retry and cancellation are under `/skill-gaps/runs/{id}`. See the [full contracts, priority formula, validation and limitations](docs/skill-gap-guidance.md).

`python -m app.db.migrate` additively creates `skill_gap_runs` and `skill_gap_reviews`. Existing facts, receipts, jobs and matching rows are preserved. Startup uses the same additive initialization. Priority weights are configurable with `SKILL_GAP_PRIORITY_WEIGHTS`; the default analysis deadline is `SKILL_GAP_TIMEOUT_SECONDS=120`. Existing three-key Groq rotation is reused without new credentials or frontend secrets.

```sh
python -m pytest
python -m scripts.evaluate_skill_gaps --output docs/skill-gap-evaluation.json
python -m scripts.smoke_skill_gaps --output docs/skill-gap-api-smoke.json
# Explicit live verification, using built-in synthetic data only:
python -m scripts.smoke_skill_gaps --live --output docs/skill-gap-live-smoke.json
```

The 204 automated backend tests use temporary SQLite databases and isolated sessions. They cover the original foundation plus PDF/DOCX/TXT extraction, evidence, edits/confirmation/merging/conflicts, correction/rejection/cancellation, source deletion, additive initialization, restart recovery, atomic confirmation rollback, missing-key/provider fallback, invalid AI JSON, combined profile limits, bounded retry, and hallucinated-source withholding. These tests are offline; provider responses are mocked. CrewAI tests execute the actual framework with scripted outputs; process tests exercise real child termination and shared key state. A separate live two-task CrewAI/Groq check used synthetic student text only; it is not part of pytest. See the report for results and commands.

Frontend browser tests live in the frontend repository. Start both local services before running `npm run test:e2e` there.

## Implemented, partial, and planned

**Implemented:** local workspace foundation, private document lifecycle, text extraction, review/edit/select/confirm/reject/cancel, provenance receipts, optional consented CrewAI/Groq extraction and quality tasks, rotating backend keys, labeled seeded job browsing/filtering, profile/dashboard integration, additive initialization, and tests.

**Partial:** deterministic resume structure recognition and source-anchor validation; single-process worker/restart recovery; section-level selection/merging rather than per-leaf profile merging; additive initialization without general schema upgrades; PostgreSQL configuration is untested against a live server; identity is local-only.

**Fallback/mock:** limited deterministic extraction is real local parsing, not fabricated data. Missing or failing Groq shows warnings and retains it. Automated provider failures are mocked only in tests. Only `/jobs` uses the explicitly labeled seeded dataset; profile/document/dashboard data remains real local data.

**Planned:** extraction-quality evaluation on broader consented layouts, production authentication/queue/deletion/migrations, normalized evidence where needed, live-job adapters, broader semantic evaluation, tailored document generation, and site-specific application/browser adapters beyond the current provider-agnostic stdio contract.

The implemented typed job-provider interface is `app/providers/jobs.py`. Future service sketches are in `app/services/contracts.py`; they are not implemented agents. See [project analysis](docs/project-analysis.md), [foundation decisions](docs/implementation-milestone.md), the earlier [resume processing report](docs/resume-processing-milestone.md), and the current [CrewAI report](docs/crewai-jobs-milestone.md).

## Parser quality and skill editing

The existing `values` and `skills: string[]` response contracts remain. Extracted-data responses additionally include typed `structured` sections, explicit not-detected fact states, classification (directly found, normalized, ambiguous, not found), original heading/type, heuristic confidence, source anchors, and server-owned review/confirmation metadata. Project/education/work records have additive optional details. Profile responses add `skill_details` with per-skill source and confirmation status. Inputs accept either skill strings or `{name, source, verified}` objects; client source/verification claims are discarded. Confirmation is established only by the explicit verification endpoints.

The parser-quality increment did not need a column migration. The CrewAI increment added a separate metadata table; the multimodal increment adds two private audit/artifact tables. Existing JSON and verified profiles are not rewritten on read or automatically reprocessed. New optional members have empty defaults. Existing uploads can explicitly **Reprocess with current parser** to create a separate review version; profile changes still require confirmation.

Run the synthetic regression evaluation with `python -m scripts.evaluate_extraction`. It covers 15 resumes and 104 oracle checks plus 178 source-leaf checks, including zero unsupported projects/technology associations in this fixture set. This does not establish general extraction accuracy. See [evaluation](docs/extraction-evaluation.md) and [parser quality report](docs/parser-quality-milestone.md) for all aliases, changed files, limitations, and checks. Groq remains optional with the same model, a configurable 3,500-token extraction output budget by default, and local fallback for provider token limits. The current multimodal increment adds real local OCR, optional visual CrewAI, and an eight-step audit interface; see [multimodal report](docs/multimodal-resume-intelligence.md).

## CrewAI execution and seeded jobs

Select the optional **Groq enhancement with CrewAI** checkbox for each document run. An unchecked run stays local. A checked, configured text run invokes two actual CrewAI agents/tasks; an additional explicit image-consent option runs a real visual layout task before them; schema, provider, timeout or source failures retain a labeled local review draft. `agent_run` on status/extracted-data responses identifies the actual engine and outcome. The legacy `method=groq_enhanced` remains for compatibility. Confirmation endpoints remain the only route from extracted drafts into verified profile data.

`GET /api/v1/jobs?category=backend&skill=Python&skill=SQL&work_mode=hybrid&employment_type=full_time&page=1&page_size=6` returns `{items,total,matched_total,page,page_size,source,dataset_version,reference_date,warnings,filters,audit,match_context}`. Independent filters combine with AND; repeated skill filters must all match. Skills use explicit aliases (JS/JavaScript, ML/machine learning, NLP/natural language processing). Location uses structured city/state/country normalization, including Bangalore/Bengaluru aliases. `location_scope=city` is the API default; `compatible` also permits remote jobs in the requested country; `state` and `country` explicitly widen reach. Work mode still applies independently. AI-related categories expand to a controlled family by default; choose `category_scope=exact` for one category. `role=AI` recognizes related AI/ML/NLP/vision roles across listing fields. Generic keyword tokens use word boundaries, so `AI` does not match `maintain`. Country/work-mode/experience/employment remain exact filters; company/industry retain substring matching. The legacy `remote` boolean remains supported (`false` includes onsite and hybrid). `GET /api/v1/jobs/options` supplies the frontend filter choices.

Other filters: `posted_from`, `posted_to` (inclusive ISO dates), `availability=all|active|expired|future`, and `as_of` (default fixed dataset reference date `2026-10-19`). Page size is 1–50; `max_results` bounds the complete result window to 1–10000. `matched_total` counts all filter matches before this cap; `total` is the capped count used for pagination. Every record is synthetic with `source=seeded_mock`, `generated_data=true`, `metadata.is_demo=true` and an inactive `example.invalid` URL. There is no job SQL table or search-history write.


For an explicit live check using only a built-in synthetic resume:

```sh
python -m scripts.smoke_resume_crew --live
```

Keys stay in backend configuration and are never printed. This command makes real Groq requests. Provider quotas remain applicable across keys, especially keys belonging to the same account. Larger resumes or tier limits can still trigger a transparent local fallback.

## Generate, reset and validate the 10,000-job fixture

From the backend repository with the virtual environment active:

```sh
python -m scripts.seed_jobs
python -m scripts.validate_jobs
python -m scripts.job_evaluation_cases > /tmp/job-evaluation-cases.json
```

The generator combines coherent multi-stack role recipes across 56 categories and ten compatibility anchors with SHA-256 choices from the fixed seed `20261019`. It writes exactly 10,000 records, deterministically across Python hash seeds and test processes. It uses a fixed reference date, never the current clock or an external API. `app/providers/data/jobs.json` is generated output; edit the recipes in `app/providers/job_catalog.py` instead of hand-authoring listings.

Running `seed_jobs` again safely resets/reseeds only the JSON job fixture via an atomic replacement. It never opens the workspace database or deletes profiles, documents, processing runs or applications. The provider notices fixture replacement on the next request; `SeededJobProvider.reset()` only clears its in-memory cache. For disposable tests, use `python -m scripts.seed_jobs --output /tmp/jobs.json --seed 123` then `python -m scripts.validate_jobs --path /tmp/jobs.json --seed 123`. Non-job JSON and database paths are rejected.

Validation checks exactly 10,000 complete schema-valid records, unique IDs, canonical skill tokens, ordered dates/ranges, version/reference metadata, common credential patterns, no unintended identical listings, repeatable loads, domain/edge-case coverage and byte-for-byte reproducibility. Deliberately empty descriptions, unknown metadata, near duplicates and unusual requirements are labeled evaluation cases, not malformed records. Future-dated/expired labels use the displayed reference date, not a claim about live availability.

The fixture is behind `JobProvider.snapshot()`. Future Adzuna/Arbeitnow adapters can normalize into the same job/filter/page shape and replace or combine snapshots. Live adapters and their real-source validation/labels remain future work; no generated job should ever be labeled as a real provider result. See [dataset design, coverage and checks](docs/seeded-jobs-dataset.md).

## Multimodal resume audit

See [implementation report](docs/multimodal-resume-intelligence.md) and [17-document evaluation](docs/multimodal-evaluation.md). Install Python requirements as usual. Local PDF rendering is included; optional system integrations are **Tesseract** (English OCR) and **LibreOffice** (DOCX → PDF rendering). Install them with your OS package manager if absent, or set `LIBREOFFICE_PATH` to its executable. Missing converters/OCR retain exact text with explicit warnings; no paid or remote document-conversion service is used.

DOCX text is read directly from its ZIP/XML without LibreOffice. Only page previews/layout rendering use LibreOffice, always with `--headless --nologo --nofirststartwizard --norestore --nodefault --nolockcheck` and a fresh temporary `-env:UserInstallation=file:///...` profile for each attempt. No GUI launch or macOS UI automation is used. Conversion captures stdout, stderr (bounded to 8,000 characters each in the private audit), exit status, timeout, and output existence; validates the generated PDF; and retries once with a different isolated profile on failure. Stale-worker detection/cleanup only signals same-user soffice processes matching that attempt's exact private profile; other profiles and sessions are left alone. Failed rendering preserves the original document and direct text, and returns an explicit warning.

Backend model configuration is `GROQ_TEXT_MODEL=openai/gpt-oss-120b`, `GROQ_VISION_MODEL_PRIMARY=qwen/qwen3.8-27b`, with an optional `GROQ_VISION_MODEL_FALLBACK`. Model-list checks are performed at execution. On the checked account GPT-OSS text and Qwen vision are available. The previously configured Llama 4 models are unavailable and have been removed from active configuration. Model names can be updated without frontend changes. Keep credentials in ignored backend `.env` only and restart after configuration changes.

`GET /api/v1/documents/{id}/audit` returns typed quality, original parser text, separate OCR, private preview metadata, layout, agent/task results, warnings, current edited draft evidence and review decision. `GET /api/v1/documents/{id}/audit/pages/{artifact_id}` returns an owned PNG with the existing session/local-client header. There are no public image paths. Process payloads add optional `use_vision` / `allow_image_processing`; existing text-only clients retain their prior consent scope. PDF/DOCX page images require additional explicit per-run consent before Groq transmission. Publications are an optional confirmed profile section.

Defaults: 50s local preparation, 12 rendered/OCR pages, 3 vision pages, 2,200px maximum side, 3 MiB per preview and 18 MiB per run. Omitted/failed page work is explicit. Preview blobs remain for the two newest runs of a document; source deletion removes all its derivative audit data. Exact parser text is never presented as visual interpretation, and visual-only claims cannot become profile facts without native/OCR corroboration. Conflicting readings stay visible and require review.

Additional checks: `python -m scripts.evaluate_multimodal`, and optional synthetic-only live checks `python -m scripts.smoke_multimodal --live` / `python -m scripts.smoke_resume_crew --live`. The actual CrewAI visual task is exercised offline with scripted models and by a successful synthetic live frontend run. See [model/token fix](docs/provider-model-token-fix.md).

Text CrewAI exchanges compact source claims with server-resolved original quotes. It sends source text/IDs and supported visual relationships, rather than duplicate font/layout metadata or the full nested review schema. Sources are batched (`GROQ_SOURCE_BATCH_CHARS=9000`, `GROQ_MAX_BATCHES=8`) under the existing overall crew deadline; all original text remains in the audit. Vision sends one image per request. Quota waiting is opt-in, bounded to one wait and one extra request, with a visible frontend wait stage. Malformed application requests are not blindly retried.

The [structured-output recovery follow-up](docs/structured-output-recovery.md) documents bounded quality/vision retries and retention of completed extraction when a later AI stage fails.

## Confirmed-profile job matching

Open **My matches** (`/matches`) and choose **Find Matching Jobs** after confirming profile facts. Matching uses only current confirmed fields, never an unconfirmed document draft. A genuine eight-stage CrewAI Flow computes a separately visible TF-IDF baseline, evidence-weighted hybrid ranking, grounded explanations and validation. Optional per-run Groq consent enables a real two-agent CrewAI explanation/review Crew. The shared three-key manager is reused with document processing.

Semantic embeddings are **off by default**. TF-IDF supplies the explicitly labelled text fallback. To use an already installed local Ollama embedding model, set `MATCHING_EMBEDDING_PROVIDER=ollama`, `MATCHING_EMBEDDING_MODEL` and the loopback `MATCHING_EMBEDDING_URL`, then restart. The application neither downloads models nor claims semantic processing when the service fails. All scoring weights, thresholds, penalties and provider metadata are stored per run.

Run `python -m app.db.migrate` for additive tables. No existing facts or verification receipts are rewritten. See [matching implementation and API contracts](docs/hybrid-job-matching.md) and [synthetic evaluation results](docs/matching-evaluation.json). Verify with `python -m pytest`, `python -m scripts.evaluate_matching --output docs/matching-evaluation.json`, and `python -m scripts.smoke_matching`. `python -m scripts.smoke_matching --live` makes real Groq calls using only the built-in synthetic candidate and mock listings.

## Connected canonical Resume Intelligence

The current resume pipeline produces separate raw evidence, a strict connected canonical graph, and a source-validated editable draft. Reprocess a saved resume to create a new version; existing profile facts remain unchanged. Open the audit for entity/field evidence, typed relationships, source conflicts, bounded repair iterations, original-file download and extraction-version comparisons. Additional original sections (research, leadership, languages, volunteering and others) are editable and confirmable; project tools/outcomes/repository/deployment fields retain their own sources.

See [architecture, API, validation and results](docs/connected-resume-intelligence.md) and [anonymized evaluation](docs/canonical-evaluation.json). `RESUME_MAX_REPAIR_CYCLES=3` (0–3) and `RESUME_REPAIR_TIMEOUT_SECONDS=45` bound targeted repair. Model processing still requires the existing per-run text/image consent. Configured working models are retained; unavailable Llama vision names do not silently disable the available Qwen path. Three-key rotation remains backend-only.

Run `python -m scripts.evaluate_canonical --output docs/canonical-evaluation.json` for the anonymized API evaluation. Do not add private resumes to fixtures. Optional private/live evaluation requires explicit `--source-db`, `--document-id`, `--private-output` and, for Groq export, `--live`; outputs must stay outside the repository. The new records reuse the existing audit table, so `python -m app.db.migrate` is additive and does not rewrite profiles.

## Full-corpus retrieval and calibrated display

The fixture contains exactly 10,000 jobs across 56 weighted categories. [Current architecture, diagnostics, comparison, performance and limits](docs/job-matching-10000.md) supersede the historical 1,000-job report. Run `python -m scripts.evaluate_retrieval` for full-corpus lexical/skill/hybrid comparisons. `MATCHING_RETRIEVAL_POOL_SIZE=1200` controls reranking after full-corpus retrieval; it can be changed per run. The raw threshold remains 30. Experimental synthetic-label calibration is a separate displayed percentage and does not change ranks or thresholds. Optional local word vectors and semantic embeddings are labeled unavailable unless configured and validated.

## Location, role and threshold audit

The [Bengaluru/AI audit](docs/ai-location-audit.md) documents the observed zero-result failure, frozen before evidence, corrected filter counts, anonymized R2 evaluation, 34 inspected ranked jobs and verification. The existing 10,000-job fixture was sufficient and was not reseeded. Numeric weights and penalties were retained.

Matching and browsing share filter logic and return per-stage counts. Owned matching audit responses include first-filter exclusion reasons; run summaries omit the full exclusion list. Small filtered pools are completely reranked within the configured pool limit, including zero-signal records with a warning. Large pools remain bounded after multi-method retrieval.

Raw scores and thresholds use **0–100 points**: `0.5` is half a point and `50` is fifty points. An explicit result request `minimum_score=0` overrides the saved run threshold; omitting it uses the saved threshold. Lowering this value reveals already scored rows, not jobs excluded by hard filters or outside the retrieval pool. Old saved runs retain their original request, snapshots and ranks; start a new run to apply new retrieval/filter logic.

Reproduce coverage and relevance checks without private resume exports:

```sh
python -m scripts.audit_job_search --output docs/ai-location-dataset-after.json
python -m scripts.validate_jobs
python -m scripts.evaluate_retrieval --output docs/ai-location-ranking-evaluation.json
python -m scripts.smoke_matching --pool-size 10000 --output docs/ai-location-performance.json
python -m pytest
```


## Cover Letter Agent

Open the frontend `/cover-letters` workspace or **Applications → Prepare cover letter**. This adds a genuine three-agent CrewAI workflow, source-backed content validation, the supplied A4/Source Sans Pro LaTeX design, user-approved image signatures, isolated XeLaTeX/LuaLaTeX compilation, PDF/preview QA, immutable versions and exact-document approval/attachment. Upload your own signature in the workspace, or explicitly opt for a typed name only. Groq generation requires its own evidence-export consent; otherwise the local evidence composer is clearly labelled.

See [implementation and operator guide](docs/cover-letter-agent.md), [verification](docs/cover-letter-verification.json), and [synthetic PDF example](docs/examples/cover-letter-synthetic/letter.pdf). Configure `COVER_LETTER_LATEX_PATH` or install a compiler on PATH; the current workspace also discovers its ignored portable `.tools/TinyTeX` installation. No LibreOffice or GUI automation is used. The two new tables are additive, and existing data is preserved.

Focused checks: `python -m pytest tests/test_cover_letters.py`; frontend browser checks: `tests/e2e/cover-letters.spec.ts`. Test fixtures are synthetic; no private resume or signature is sent to a model by tests.


## Resume Tailoring Agent

Open `/resume-tailoring` in the frontend or **Applications → Tailor resume**. The supplied `resume.cls` and navy template produce a separate one-page resume version from confirmed profile facts, job requirements and available matching/gap context. Three actual CrewAI agents can be enabled with explicit selected-evidence processing consent; local source selection and provider fallbacks are labelled. Dates, employers, roles, grades, technologies and links remain source-bound. Uploaded TeX is retained as a passive reference, never executed.

Review changes, preview/download the actual PDF, then explicitly approve and attach it to the same application. Original profiles/documents remain intact. Source, class, private compiler logs and version history are available. One additive table stores tailored versions. See [implementation guide](docs/resume-tailoring-agent.md), [verification](docs/resume-tailoring-verification.json), and [live synthetic PDF](docs/examples/tailored-resume-live-synthetic/resume.pdf). Focused checks: `python -m pytest tests/test_resume_tailoring.py`; browser checks: `tests/e2e/resume-tailoring.spec.ts`.

Resume tailoring now protects complete confirmed career history, projects and skills, computes local relevance when optional context is missing, and exposes preservation/omission evidence. See [the improvement report](docs/resume-tailoring-improvements.md). Focused regression: `python -m pytest tests/test_resume_tailoring.py tests/test_resume_tailoring_preservation.py`.
