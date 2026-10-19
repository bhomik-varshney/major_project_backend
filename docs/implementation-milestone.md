# Foundation milestone decisions

19 October 2026. Local implementation, not a deployment or complete backend.

Historical foundation report. Resume extraction and review were added afterward; see [the current milestone report](resume-processing-milestone.md) and README for present behavior. Results below describe the foundation checkpoint.

## Reference inspection

Inspected the existing major-project frontend and backend before editing, and read the public Velion reference at commit `bddc8ec0256d512496088e349900262255037d8b`. Velion has a TanStack Start/React/Vite frontend, shared panel shells, a status rail, API/error modules, responsive workspace layouts, pointer-driven highlights, and a FastAPI application factory with provider contracts and deterministic test isolation. Its session store is process-local; this project's profiles/documents instead require durable SQL storage. Velion deployment uses Docker/Render and provider/MCP-specific setup that is outside this milestone.

| Velion pattern | Career project equivalent |
| --- | --- |
| Vehicle preferences | Typed candidate profile and soft career preferences |
| Vehicle marketplace | Future job-source adapters |
| Vehicle ranking | Future candidate/job matching contracts |
| Recommendation explanation | Future evidence-linked match explanations |
| Booking workflow | Future application preparation and explicit approval |
| Transaction status | Future origin-bearing application history |
| Agent workflow | Future Career Orchestrator operation states |
| Vehicle dashboard | Student dashboard, profile, and document library |

Adapted engineering/UX principles, not domain code: shared workspace shell, reusable elevated panels, restrained rose pointer highlight, small-screen navigation, backend status/retry, typed transport, explicit errors/empty/loading states, application factory, isolated fixtures, and no-credential operation. Vehicle, booking, payment, MCP implementation, brand colors, logos, and reference secrets were not copied. CareerOS keeps its existing type, rose palette, and landing-page components.

## Decisions made for this increment

- One modular FastAPI application with versioned routes, SQLAlchemy repositories/services, and Pydantic request/response schemas.
- SQLite for local operation, PostgreSQL dialect/driver configuration ready. No silent database fallback: a failed configured database returns an error.
- Signed HttpOnly browser identity and explicit loopback/origin checks. This is an identified local workspace, not account authentication or public hosting.
- Typed nested JSON for initial candidate facts rather than premature normalized tables; all facts originate in manual entry in this increment.
- Explicit confirmation of the saved profile revision; edits clear verification and compare expected revision atomically.
- Validated PDF/DOCX/TXT document storage in the database so metadata and bytes share transaction/deletion behavior. No extraction/generation is represented as completed.
- Actual dashboard calculations and activity. Unsupported future counts are null/planned, never seeded as live results.
- `/dashboard`, `/profile`, `/documents`, and explanatory `/settings` are functional. `/jobs`, `/matches`, `/skill-gaps`, and `/applications` are explicitly planned. Existing `/workspace` remains the labeled sample demonstration. Landing entry buttons now open the real dashboard.
- Pointer highlight uses one delegated passive listener, a bounded animation frame, CSS custom properties, fine-pointer gating, reduced-motion handling, and cleanup. There is no continuous animation loop.

## Verification record

See final recorded results below. Checks use local disposable databases and generated student fixtures. No production data, external AI, job credentials, portal submissions, publication, Git commits, or pushes are involved.

The pre-existing frontend dependency installation stalled reading files. A clean temporary installation first verified identical source; the actual checkout was then refreshed successfully with `npm ci --offline --ignore-scripts` using the populated temporary npm cache. All final frontend checks ran from the actual project checkout. The initial Conda Python executable crashed importing its native readline module under pytest; a fresh Homebrew Python 3.13 virtual environment is used instead. Neither incident was treated as a passing check or hidden as an application fallback.

### Final results

| Check | Result |
| --- | --- |
| Backend `python -m pytest` | 14 passed, 2 dependency deprecation warnings. |
| Backend `python -m compileall -q app` | Passed. |
| Frontend `VITE_API_BASE_URL=http://localhost:8000/api/v1 npm run build` | Client, SSR, and Nitro production builds passed. Existing toolchain notices remain. |
| Frontend `npm run typecheck` | Passed. |
| Frontend `npm run lint` | Passed with 0 errors and 10 existing Fast Refresh warnings in original landing/UI helpers. Two existing Constellation formatting errors and one Nav formatting error were corrected without behavior changes. |
| Frontend `npm run test:e2e` | 10 passed in 15.8 seconds against the actual checkout and real local API. |
| Local API startup/database | Uvicorn started and initialized a disposable SQLite database; browser tests exercised real health, profile, document, session, and summary routes. |
| `git diff --check` | Passed in both repositories. |
| Changed/new text credential-pattern scan | No credential patterns found; `.env`, signing secret, database, virtual environment, browser output, and node_modules remain ignored. |
| Visual inspection | Desktop (1440px) and mobile (390px) dashboard screenshots inspected; browser checks confirmed forms/documents fit the mobile viewport. |

Test environment: macOS, Homebrew Python 3.13.2, SQLite, Node 23.7.0, Chromium through Playwright 1.58.0. Node 23 produced a dependency engine warning; `.nvmrc` specifies Node 24 for subsequent setup. No Node version manager was modified. PostgreSQL and public hosting were not tested or declared complete. Local preview servers were stopped after verification.

Browser coverage includes touch-disabled highlights; cross-tab stale-write rejection and explicit reload; preserved landing entry; profile create/read/update/confirmation and persistence; document type/size validation, storage, refresh, summary activity, and removal; API outage/recovery; loading skeletons; mobile navigation/layout; mouse/reduced-motion behavior; and honest future-feature/sample labels.

### Main files added or changed

Backend: `app/main.py`, `app/core/`, `app/db/`, `app/api/`, `app/schemas/`, `app/repositories/`, `app/services/`, `tests/`, requirements files, configuration example, ignores, README, and these design notes.

Frontend: `src/components/workspace/`, `src/services/api/`, `src/hooks/use-panel-light.ts`, `src/lib/workspace-date.ts`, `src/workspace.css`, pathless student routes and generated route tree, landing entry links, package/lock/test configuration, `.env.example`, `.nvmrc`, ignores, and README. Original landing layout and rendering conventions are preserved.

No user input or external credential is required to run this milestone. The next increment is source-linked resume extraction and field review, followed by retrieval/matching; production identity, normalized evidence, migration upgrades, complete deletion/recovery, and a tested PostgreSQL deployment remain future work.
