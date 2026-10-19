# Agentic AI Career Companion — backend

FastAPI backend for the CareerOS student workspace. Implements persistent profiles, private document storage, PDF/DOCX/TXT text extraction, source-linked review drafts, explicit confirmation, and database-backed dashboard summaries. A genuine two-task CrewAI Resume Intelligence Crew uses Groq only when configured and explicitly selected for that document. Ten clearly labeled seeded jobs support browsing and filtering; live job retrieval and matching remain planned. See the current [CrewAI and seeded-jobs implementation report](docs/crewai-jobs-milestone.md).

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
| `GROQ_MAX_OUTPUT_TOKENS` | `2500`; bounded output reservation (512–8000). Larger reservations may exceed your provider tier. |
| `GROQ_KEY_COOLDOWN_SECONDS` | `5`; temporary failures cool down exponentially, capped at 120 seconds. |
| `CREW_TIMEOUT_SECONDS` | `75`; total child-process time budget, including framework startup and requests (maximum 120). |
| `AUTO_INIT_DB` | `true`; add missing tables at startup. |

No external credentials are required for local extraction. Keep any optional Groq key in the ignored backend `.env`; never use a `VITE_*` variable. `.env`, `.data`, and `.venv` are ignored. `.env.example` contains placeholders only.

## Database and identity

SQLAlchemy supports local SQLite and a PostgreSQL connection such as `postgresql+psycopg://user:password@localhost:5432/careeros`; the PostgreSQL driver is included. SQLite is tested; a live PostgreSQL instance has not been exercised. `python -m app.db.migrate` uses additive `Base.metadata.create_all`, also available at startup with `AUTO_INIT_DB=true`. The resume-intelligence increment adds only `resume_agent_runs`, bringing the schema to seven tables; existing columns and saved facts remain intact. Stop the application and back up an existing database before upgrading. This command does not alter existing tables; future column changes require versioned migrations. See [resume processing milestone](docs/resume-processing-milestone.md).

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
| GET | `/jobs` | Filter/paginate read-only seeded demonstration jobs; no provider network requests, matching, ranking or applications. |
| GET | `/dashboard/summary` | Actual profile completion, total/reviewed document-derived skill counts, document count, verification, and recent activity. |

Profile creation requires a name. Other fields are optional; invalid fields are rejected rather than invented. Manual verification confirms the whole saved profile. Document confirmation applies selected top-level sections only; lists append unique entries unless replacement is explicitly selected. A different existing scalar requires explicit replacement. Incomplete selected education/work records must be corrected before confirmation. Relevant manual edits clear profile verification. Concurrent stale writes return 409 without overwriting saved facts. Profile deletion is deferred because full owner-data deletion/retention semantics need a dedicated workflow.

Profile completion is the percentage of ten non-empty fields/sections: name, email, location, objective, education, skills, projects, preferred roles, preferred locations, and experience level. It is not a candidate-quality or hiring score. Prior employment is not required for completion.

Documents support PDF, DOCX, and UTF-8 TXT. Extension, declared MIME, nonempty content, size, and integrity are checked. Encrypted/corrupt PDFs, invalid DOCX containers/XML, and invalid text are rejected. Scanned PDFs can be stored but fail processing clearly: OCR is unavailable. Bytes and metadata commit together. Existing `upload_status=stored` and legacy `extraction_status` remain compatible; additive `processing_status` and `verification_status` expose the workflow. The count/size/parser limits bound this local prototype.

Processing runs through queued, text preparation, local identification, optional CrewAI extraction and dependent quality validation, deterministic source/quality checks, and review preparation. Extraction completion produces `awaiting_review`, never a verified profile. Corrections/rejection/cancellation are explicit states. Confirmation atomically writes the profile, receipt, extraction verification, and activity. A single bounded in-process worker is intended for one local application process; interrupted runs become failed/retryable at startup, rather than silently resuming or calling Groq again.

Local parsing recognizes expanded heading synonyms and complete Summary/Objective/Profile statements. It groups project headings/bullets and explicit technologies, work entries, and labeled/table education. PDF block positions and DOCX paragraph styles, lists, tabs, and table order are retained where available; possible columns trigger uncertainty warnings. Complex layouts can still be missed. CrewAI uses real agents/tasks, sequential dependencies and a custom Groq LLM with compact native structured output; accepted leaves must match real quotes and the correct section/entry group. Unsupported/inferred leaves are withheld. Confidence labels are heuristic and source matching is not a semantic correctness guarantee: the student must review every selected section. Missing-key/provider/JSON errors retain the local draft and show a limited-mode warning.

Dashboard `data_mode=live_local` identifies actual database data. Unimplemented recommendation/matching/application/approval counts are `null`. Jobs are `feature_states.jobs=seeded_demo`; orchestration is `resume_only`, and matching/applications remain `planned`. Seeded vacancies never count as recommendations. `profile_skill_count` counts current profile skills; `extracted_skill_count` counts distinct current resume-derived skills in confirmation receipts, excluding user-entered review additions/corrections. Unreviewed suggestions never count as confirmed skills.

## Errors, persistence, and deletion

Errors use `{ "error": { "code", "message", "operation_id", "fields" } }`. Field errors omit submitted values. Database/internal errors omit connection strings, provider secrets, and document content. Responses carry operation IDs and `Cache-Control: no-store`; structured logs record method/status/time without request bodies. Save/upload success is returned only after commit. Saved data survives backend restart, and duplicate uploads do not duplicate activity.

For now, profiles, documents, and activity remain until explicitly removed or the local database is reset. Document deletion removes bytes and extraction text from the active database and scrubs receipt source quotes. Approved profile facts/confirmation snapshots and historical source name/ID remain; the profile marks that source unavailable. Browser cookie expiry does not delete data. Full account deletion, retention schedules, backups, SQLite page reclamation, and recovery remain unimplemented. Stop the backend before deliberately removing a disposable `.data` directory; this removes all local records and the signing secret, not external backups.

## Tests

```sh
python -m pytest
```

The 139 automated backend tests use temporary SQLite databases and isolated sessions. They cover the original foundation plus PDF/DOCX/TXT extraction, evidence, edits/confirmation/merging/conflicts, correction/rejection/cancellation, source deletion, additive initialization, restart recovery, atomic confirmation rollback, missing-key/provider fallback, invalid AI JSON, combined profile limits, bounded retry, and hallucinated-source withholding. These tests are offline; provider responses are mocked. CrewAI tests execute the actual framework with scripted outputs; process tests exercise real child termination and shared key state. A separate live two-task CrewAI/Groq check used synthetic student text only; it is not part of pytest. See the report for results and commands.

Frontend browser tests live in the frontend repository. Start both local services before running `npm run test:e2e` there.

## Implemented, partial, and planned

**Implemented:** local workspace foundation, private document lifecycle, text extraction, review/edit/select/confirm/reject/cancel, provenance receipts, optional consented CrewAI/Groq extraction and quality tasks, rotating backend keys, labeled seeded job browsing/filtering, profile/dashboard integration, additive initialization, and tests.

**Partial:** deterministic resume structure recognition and source-anchor validation; single-process worker/restart recovery; section-level selection/merging rather than per-leaf profile merging; additive initialization without general schema upgrades; PostgreSQL configuration is untested against a live server; identity is local-only.

**Fallback/mock:** limited deterministic extraction is real local parsing, not fabricated data. Missing or failing Groq shows warnings and retains it. Automated provider failures are mocked only in tests. Only `/jobs` uses the explicitly labeled seeded dataset; profile/document/dashboard data remains real local data.

**Planned:** OCR, extraction-quality evaluation on consented varied layouts, production authentication/queue/deletion/migrations, normalized evidence where needed, Adzuna/Arbeitnow adapters, keyword/semantic/hybrid matching, gaps/explanations, truthful versioned application drafts, explicit submission approval/tracking, the broader career multi-agent orchestrator, and MCP protocol tools.

The implemented typed job-provider interface is `app/providers/jobs.py`. Future service sketches are in `app/services/contracts.py`; they are not implemented agents. See [project analysis](docs/project-analysis.md), [foundation decisions](docs/implementation-milestone.md), the earlier [resume processing report](docs/resume-processing-milestone.md), and the current [CrewAI report](docs/crewai-jobs-milestone.md).

## Parser quality and skill editing

The existing `values` and `skills: string[]` response contracts remain. Extracted-data responses additionally include typed `structured` sections, explicit not-detected fact states, classification (directly found, normalized, ambiguous, not found), original heading/type, heuristic confidence, source anchors, and server-owned review/confirmation metadata. Project/education/work records have additive optional details. Profile responses add `skill_details` with per-skill source and confirmation status. Inputs accept either skill strings or `{name, source, verified}` objects; client source/verification claims are discarded. Confirmation is established only by the explicit verification endpoints.

The parser-quality increment did not need a column migration. The newer CrewAI increment adds a separate metadata table. Existing JSON and verified profiles are not rewritten on read or automatically reprocessed. New optional members have empty defaults. Existing uploads can explicitly **Reprocess with current parser** to create a separate review version; profile changes still require confirmation.

Run the synthetic regression evaluation with `python -m scripts.evaluate_extraction`. It covers 15 resumes and 104 oracle checks plus 178 source-leaf checks, including zero unsupported projects/technology associations in this fixture set. This does not establish general extraction accuracy. See [evaluation](docs/extraction-evaluation.md) and [parser quality report](docs/parser-quality-milestone.md) for all aliases, changed files, limitations, and checks. Groq remains optional with the same model, a configurable 2,500-token output budget by default, and local fallback for provider token limits. No vision/OCR model was added.

## CrewAI execution and seeded jobs

Select the optional **Groq enhancement with CrewAI** checkbox for each document run. An unchecked run stays local. A checked, configured run invokes two actual CrewAI agents/tasks; schema, provider, timeout or source failures retain a labeled local review draft. `agent_run` on status/extracted-data responses identifies the actual engine and outcome. The legacy `method=groq_enhanced` remains for compatibility. Confirmation endpoints remain the only route from extracted drafts into verified profile data.

`GET /api/v1/jobs?keyword=backend&skill=Python&skill=SQL&location=Noida&remote=false&experience=student&page=1&page_size=6` returns `{items,total,page,page_size,source,dataset_version,warnings,filters}`. All filters combine with AND; repeated skill filters require all listed skills. Keyword tokens match title/company/description/skills, location is a substring, skills are exact names ignoring case. Page size is 1–50. Records carry `source=seeded_mock` and `metadata.is_demo=true`; fixed dates and salaries are demonstration values. `example.invalid` application URLs are inactive placeholders. There is no job database table or search-history write in this milestone.

For an explicit live check using only a built-in synthetic resume:

```sh
python -m scripts.smoke_resume_crew --live
```

Keys stay in backend configuration and are never printed. This command makes real Groq requests. Provider quotas remain applicable across keys, especially keys belonging to the same account. Larger resumes or tier limits can still trigger a transparent local fallback.
