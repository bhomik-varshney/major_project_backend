# Agentic AI Career Companion — backend

FastAPI foundation for the CareerOS student workspace. This milestone implements local, persistent profiles, explicit fact confirmation, document storage, and database-backed dashboard summaries. It makes no AI or job-provider calls.

## Local setup

Use Python 3.12 or 3.13 (verified with Homebrew Python 3.13.2). From this repository:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
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
| `MAX_DOCUMENTS_PER_USER` | `20`. |
| `AUTO_INIT_DB` | `true`; creates missing initial tables during startup. |

No Groq, Adzuna, email, or other external credentials are required. `.env`, `.data`, and `.venv` are ignored. `.env.example` contains configuration examples only.

## Database and identity

SQLAlchemy supports local SQLite and a PostgreSQL connection such as `postgresql+psycopg://user:password@localhost:5432/careeros`; the PostgreSQL driver is included. SQLite is tested; a live PostgreSQL instance has not been exercised in this milestone. Tables are initialized using `Base.metadata.create_all`. This creates the initial schema, not upgrades: add Alembic/versioned migrations before changing deployed schemas.

Implemented entities:

- `User`: UUID identity issued by the server for one local browser session.
- `CandidateProfile`: one per user; typed profile facts stored as JSON, revision, explicit verification state, timestamps.
- `Document`: owner, validated metadata, hash, stored bytes, upload/extraction states, timestamp.
- `Activity`: owner-scoped, dated workflow events without profile contents or filenames.

Education, experience, projects, skills, certifications, and achievements are typed nested profile facts in this first schema. Separate normalized skill/evidence/education tables from the SDD are deferred until extraction and matching need them. JSON works with both supported database dialects; relational ownership and uniqueness are enforced now.

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
| DELETE | `/documents/{id}` | Delete own document and bytes atomically. |
| GET | `/dashboard/summary` | Actual profile completion, document/manual-skill counts, and recent activity. |

Profile creation requires a name. Other fields are optional; invalid fields are rejected rather than invented. Profile verification confirms the whole current manual profile; field-level extraction evidence/review is a later extension. Relevant edits clear verification. Concurrent stale writes return 409 without overwriting saved facts. Profile deletion is deferred because full owner-data deletion/retention semantics need a dedicated workflow.

Profile completion is the percentage of ten non-empty fields/sections: name, email, location, objective, education, skills, projects, preferred roles, preferred locations, and experience level. It is not a candidate-quality or hiring score. Prior employment is not required for completion.

Documents support PDF, DOCX, and UTF-8 TXT. Type, declared MIME, nonempty content, size, and basic file integrity are checked. Encrypted/corrupt PDFs, invalid DOCX containers/XML, and invalid text are rejected. Scanned PDFs may be stored; OCR and extraction do not run. State is always `upload_status=stored`, `extraction_status=not_started`. Bytes and metadata commit together. PDF parsing validates integrity only; it does not extract facts. The count/size limits bound this local prototype, not a production upload service.

Dashboard `data_mode=live_local` identifies actual database data. Unimplemented job/matching/application/approval counts are `null`, with `feature_states=planned`; these are not mock successes. `extracted_skill_count` remains zero, while `profile_skill_count` counts manually entered skills.

## Errors, persistence, and deletion

Errors use `{ "error": { "code", "message", "operation_id", "fields" } }`. Field errors omit submitted values. Database/internal errors omit connection strings, provider secrets, and document content. Responses carry operation IDs and `Cache-Control: no-store`; structured logs record method/status/time without request bodies. Save/upload success is returned only after commit. Saved data survives backend restart, and duplicate uploads do not duplicate activity.

For now, profiles, documents, and activity remain until explicitly removed or the local database is reset. Document deletion removes bytes from the active database. Browser cookie expiry does not delete data. Full account deletion, retention schedules, backups, SQLite page reclamation, and recovery remain unimplemented; use development/consented fixtures for this local milestone. Stop the backend before deliberately removing a disposable `.data` directory; this removes all local records and the signing secret, not external backups.

## Tests

```sh
python -m pytest
```

The tests use temporary SQLite databases and isolated browser-session cookies. They cover health, profile CRUD/validation, verification invalidation and stale revisions, owner isolation, TXT/PDF/DOCX validation, size/count limits, duplicate upload/deletion, dashboard summaries, commit/database failure, origin/CORS controls, loopback restriction, and persistence across application restart. No network APIs or real personal data are used.

Frontend browser tests live in the frontend repository. Start both local services before running `npm run test:e2e` there.

## Implemented, partial, and planned

**Implemented:** local session, initial database schema, profile create/read/update, explicit profile confirmation, documents metadata/storage/removal, deterministic summary calculation, error handling, CORS, OpenAPI, and tests.

**Partial:** profile evidence is manual and verified per revision; initial schema initialization has no upgrade migrations; PostgreSQL configuration is ready but untested against a live server; identity is local-only.

**Planned:** resume field extraction/OCR policy, normalized evidence/skill tables, real account authentication, deletion/recovery controls, Adzuna/Arbeitnow adapters, keyword/semantic/hybrid matching, gaps/explanations, truthful versioned drafts, exact-action approval and tracking, CrewAI orchestration, actual MCP protocol tools, and evaluation. No fake endpoints claim these features work.

Future service contracts are in `app/services/contracts.py`. They specify owner/revision/snapshot inputs without importing AI SDKs or creating unused tables. See [project analysis](docs/project-analysis.md) and [milestone decisions](docs/implementation-milestone.md).
