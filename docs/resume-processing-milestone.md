# Resume and document processing milestone

19 October 2026. Local implementation extending the working foundation; no deployment, commit, or push.

Historical first extraction checkpoint. For the subsequent parsing/evidence/multi-skill improvements and current checks, see [parser quality milestone](parser-quality-milestone.md).

## Delivered behavior

The student can upload a readable PDF, DOCX, or UTF-8 TXT file, explicitly start processing, inspect source-linked suggestions, edit the draft, select sections, and confirm them into the profile. The Profile page shows saved facts, editable skills, reviewed sources, and the last confirmation time. Dashboard summaries and activity reflect committed records. No extracted suggestion becomes a verified profile fact automatically.

The existing landing page, workspace routes, design system, local session, profile contract, document storage, and tests are preserved. Upload remains a separate action from processing. Original `upload_status=stored` is retained for compatibility; new processing/verification metadata supplies the richer workflow.

### Implemented

- Private validated upload storage, deduplication, ownership checks, size/count limits, safe UUID parser filenames, and temporary-file cleanup.
- PDF text by page with pypdf; DOCX paragraphs/table-cell paragraphs in body order with ZIP/XML parsing; TXT lines. Source blocks/text are private SQL data, not public files.
- Conservative contact/URL/heading/section extraction without credentials. Typed drafts include name, email, phone, location, summary, education, skills, experience, projects, certifications, achievements, links, and explicitly stated preferred roles.
- Optional Groq schema-constrained extraction using `openai/gpt-oss-120b`. Per-run external-processing consent is stored. Each accepted nonempty AI leaf requires a matching value/quote in a real source block; inferred/unsupported leaves are withheld. Input document text is treated as data, not instructions.
- Dot-path field evidence: value in the typed draft, quote/block/page/paragraph where available, heuristic confidence, extraction method, uncertainty warning, and user-edit marker. Corrections do not acquire fabricated source support.
- Versioned extraction runs, durable stage/status, bounded one-worker queue, cancellation, safe errors, retry, and restart recovery. Repeated start requests reuse the active/pending run; explicit API `restart: true` creates another completed-run version.
- Saved draft editing, request correction, rejection, explicit confirmation of selected top-level sections, merge of unique list entries, explicit replacement choices, and stale draft/profile revision checks.
- Atomic profile/extraction/receipt/activity confirmation. Database commit failure leaves no verified extraction or partial profile. Receipt snapshots preserve the distinction between selected document values and merged existing facts.
- Profile/source metadata, partial section confirmation, and genuine document-derived skill counts. Deleting a source removes extraction text and receipt source quotes while preserving approved profile facts and marking the historical source unavailable.
- Responsive review sections, actual stage feedback without an invented ETA, error/retry/manual-entry alternatives, reduced-motion support, and existing panel lighting. Confirmation controls follow the fields to avoid covering them.

### Partial capabilities

Local structured parsing is intentionally limited. Basic name detection can be uncertain; summary parsing captures the first recognized summary line. Education and experience work best with `qualification | institution | period` and `role | organization | period`. Complex columns, multi-line records, unusual headings, or ambiguous dates can be missed. No complete resume intelligence or extraction-accuracy benchmark is claimed.

Confidence labels are heuristic categories, not calibrated probabilities. Source/value matching blocks many unsupported suggestions but cannot establish semantic correctness. Students must review the selected sections. Review selection/replacement operates by top-level section; individual entries can be edited/deleted before confirming that section.

The worker is an in-process, single-application-process prototype. Cancellation prevents later draft publication but cannot retract an external request already sent. Parser limits are not operating-system resource isolation. If database failure prevents a worker from persisting its failure, restart after database recovery marks the interrupted run retryable. There is no distributed queue, runtime stale-job watchdog, OCR, account authentication, or comprehensive retention/account-deletion workflow. PostgreSQL configuration is ready but only SQLite has been tested.

### Fallback and mocks

Missing Groq credentials or provider timeout/rate-limit/unavailable/invalid-JSON responses retain the real deterministic draft with a visible limited-mode warning. Combined suggestions that exceed profile limits also retain local results. There are no fabricated profile facts or successful provider results in the live fallback.

Provider failures are simulated in automated tests. A separate successful live Groq check used synthetic student text only and returned source-supported fields. No personal project documents/resumes were sent for that check. The key is held only in the ignored backend `.env`, with owner-only file permissions; the frontend receives configuration availability and the model name, never the credential.

### Planned

OCR, varied-layout extraction-quality evaluation, production identity and deletion controls, versioned column migrations, a persistent production worker, job-source adapters, matching, gap analysis, application drafting/approval, CrewAI orchestration, and actual MCP tools remain future work. The existing legacy `/workspace` and public product previews remain labeled sample UI.

## Database initialization and configuration

From the backend directory with its virtual environment activated:

```sh
pip install -r requirements-dev.txt
# For a new environment only; preserve an existing .env and its secrets.
cp .env.example .env
python -m app.db.migrate
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --no-access-log
```

For an existing workspace, stop the application and back up the database first, add the new optional settings to the existing `.env`, then run the initialization command. Do not replace the database or signing secret. The command is idempotent and adds `document_extractions` and `verification_records`; it does not alter the existing users/profiles/documents/activity tables. Startup also creates missing tables when `AUTO_INIT_DB=true`. Interrupted-job recovery runs even when automatic schema initialization is disabled. Future existing-column changes require a versioned migration tool; this command is not a general migration engine.

`DOCUMENT_STORAGE_PATH` controls the private temporary parser workspace; it does not move original uploads out of SQL storage. Use a private path outside the frontend. `MAX_DOCUMENT_SIZE_MB` takes precedence over the preserved `MAX_DOCUMENT_BYTES` setting. Defaults: 5 MiB, 20 documents per workspace, 50 PDF pages, and 60,000 extracted characters. Text truncation warns; page-limit violations fail safely. DOCX upload integrity/decompression checks remain in the existing validation service.

Optional backend settings: `GROQ_API_KEY`, `GROQ_MODEL=openai/gpt-oss-120b`, `GROQ_TIMEOUT_SECONDS=30`. Leave the key empty to run entirely locally. Setting a key does not itself send documents: each run still requires the optional Groq choice and explicit external-processing consent. Existing `DATABASE_URL`, `CORS_ORIGINS`, `DATA_DIR`, and session conventions remain. No provider credentials belong in `VITE_*`.

The frontend runs with `VITE_API_BASE_URL=http://localhost:8000/api/v1` and `npm run dev -- --host localhost --port 5173`. Use matching hostnames on both sides. This milestone remains bound to loopback with local signed-cookie identity.

## API additions and extensions

Paths below start with `/api/v1` and require the existing owner-scoped session and workspace-client header. `/docs` contains the exact typed payloads.

| Method | Path | Change |
| --- | --- | --- |
| GET | `/documents/policy` | Extraction availability, Groq configured/model metadata, OCR availability. |
| POST | `/documents/upload` | Preserves original upload semantics; adds processing ID/state/version and verification metadata, including on duplicate uploads. |
| GET | `/documents`, `/documents/{id}` | Latest processing and verification metadata. |
| DELETE | `/documents/{id}` | Refuses active runs; purges derivative text/quotes with source bytes; preserves confirmed profile facts. |
| POST | `/documents/{id}/process` | 202; optional `use_groq`, `allow_external_processing`, `restart`. |
| GET | `/documents/{id}/status` | Current state, stage, run/revision, warnings/errors, completion/verification timestamps. |
| GET | `/documents/{id}/extracted-data` | Typed review values/evidence, current profile revision/values, applied fields. |
| PUT | `/documents/{id}/extracted-data` | `processing_id`, `expected_revision`, typed `values`. |
| POST | `/documents/{id}/verify` | `processing_id`, `expected_revision`, `expected_profile_revision`, `fields`, optional `replace_fields`, `confirmed: true`. |
| POST | `/documents/{id}/request-correction` | Draft ID/revision plus `action: correction`, `reject`, or `cancel`. |
| GET/POST/PUT | `/profile` | Optional phone/links; responses include reviewed sources, verified sections, last confirmation time. |
| POST | `/profile/verify` | Preserves manual whole-profile confirmation and adds a confirmation receipt. |
| GET | `/dashboard/summary` | Current confirmed document-derived skill count; profile/document/activity summaries remain real database data. |

State model: before processing the document is `uploaded` in `processing_status`; runs progress through `queued`/`processing` with stages `extracting_text`, `identifying_fields`, optional `enhancing_fields`, and `preparing_review`. `stage=completed` plus `status=awaiting_review` means extraction finished and user verification is pending. Review can become `needs_correction`, `rejected`, or `verified`; execution can become `failed` or `cancelled`. Completion alone never verifies the profile.

## Validation record

| Check | Result |
| --- | --- |
| Backend `python -m pytest -q` | 38 passed; original 14 retained, with 2 existing dependency deprecation warnings. |
| Backend `python -m compileall -q app` | Passed. |
| Frontend `npm run typecheck` | Passed. |
| Frontend production `npm run build` | Client, SSR, and Nitro builds passed; existing toolchain notices remain. |
| Frontend `npm run lint` | Passed: 0 errors, 10 existing Fast Refresh warnings. |
| Frontend `npm run test:e2e` | 15 passed, including all original 10 tests. |
| API startup | Real Uvicorn service started successfully with a disposable SQLite browser-test database. |
| Additive initialization | Repeated initialization preserves a pre-existing verified profile in a legacy four-table schema. |
| Live provider | Synthetic-only successful Groq structured extraction smoke check; offline failure tests do not require credentials. |
| Repository checks | `git diff --check` passed in both repositories; changed/new source credential-pattern scan found no matches. Backend `.env` is ignored and owner-only. |

Backend coverage includes validated TXT/PDF/DOCX uploads/extraction; scanned-PDF failure/retry; typed values/evidence; no automatic profile creation; draft edits; confirmation/provenance/counts; preservation/replacement; stale revisions; correction/rejection; cancellation and active-delete guard; duplicate state preservation; owner isolation; consent/missing key; invalid JSON/timeouts/rate limits/provider failure; source anchoring; source deletion; additive initialization; confirmation commit rollback; combined enhancement limits; and interrupted-job recovery with automatic initialization disabled.

Browser coverage performs the real landing → dashboard → upload → processing → evidence review → edit → save draft → explicit confirmation → profile → dashboard sequence, then reuploads the same resume to confirm that duplicate feedback preserves its verified state. It also exercises scanned-PDF failure/retry/manual entry, correction/rejection without profile mutation, fresh confirmation after profile conflict, mobile review, existing validation/storage/removal, API outage/loading, pointer lighting, reduced motion/touch, and future-feature labels. Browser provider processing remains local. Desktop 1440px and mobile 390px review screenshots were inspected; layout tests check horizontal overflow.

Environment: macOS, Homebrew Python 3.13.2, SQLite, Node 23.7.0, Playwright Chromium 1.58.0. `.nvmrc` recommends Node 24. No PostgreSQL server or public deployment was tested.

## Changed files

Backend paths relative to `major_project_backend`:

```text
.env.example
README.md
requirements.txt
app/main.py
app/core/config.py
app/db/models.py
app/db/migrate.py                         (new)
app/api/dashboard.py
app/api/documents.py
app/api/extraction.py                     (new)
app/api/profiles.py
app/schemas/extraction.py                 (new)
app/schemas/profile.py
app/schemas/workspace.py
app/services/document_processing.py       (new)
app/services/extraction_review.py         (new)
app/services/groq_extraction.py           (new)
app/services/profiles.py
app/services/resume_structure.py          (new)
app/services/resume_text.py               (new)
tests/conftest.py
tests/test_document_processing.py        (new)
docs/implementation-milestone.md          (historical report pointer)
docs/resume-processing-milestone.md       (new)
```

Frontend paths relative to `major_project_frontend`:

```text
README.md
src/components/workspace/Dashboard.tsx
src/components/workspace/DocumentReview.tsx   (new)
src/components/workspace/DocumentsPage.tsx
src/components/workspace/ProfilePage.tsx
src/components/workspace/SettingsPage.tsx
src/services/api/client.ts
src/services/api/queries.ts
src/services/api/types.ts
tests/e2e/document-processing.spec.ts          (new)
tests/e2e/workspace.spec.ts                    (updated activity copy assertion)
tests/fixtures/scanned.pdf                    (new synthetic blank PDF)
```

The ignored local backend `.env` was configured privately and is not part of source changes.

## Recommended next step

Evaluate extraction and review on varied, consented student resume layouts, recording missed fields and source accuracy before widening parser rules. Then build job retrieval adapters and deterministic matching against the explicitly confirmed profile revision. Production authentication, migration strategy, and a durable worker are prerequisites for public multi-user deployment.

Primary references used for implementation: [Groq structured outputs](https://console.groq.com/docs/structured-outputs) and [pypdf text extraction](https://pypdf.readthedocs.io/en/stable/user/extract-text.html). Velion remains a UX reference only; no vehicle/booking/payment logic was introduced.
