# Application workspace continuation

This extends the existing application milestone; the resume pipeline, job generator, ranking algorithms and unrelated modules were not re-audited or rewritten. The existing Velion integration review and reference paths are recorded in [the original implementation report](application-preparation-tracking.md). No LibreOffice GUI or document conversion was used for this continuation.

## Architecture and user control

The existing genuine CrewAI Flow still performs six deterministic preparation/validation stages. The optional six-agent CrewAI review remains metadata-only, explicitly consented, and unable to approve fields, select uploads, change candidate facts or submit anything. No new private-resume export or live Groq replay was performed for this continuation.

The host remains the sole mutation authority. An owned saved job or recommendation creates one application per candidate/job/source, including compatibility checks for older application identities. Duplicate creation returns HTTP 409 `duplicate_application`, rather than creating another tracker entry. Matching-run scores and missing-skill evidence are retained when supplied; saved jobs without a run show unavailable context rather than invented scores.

Five timestamped milestones are separate: prepared, external form opened, prefill completed, submission reported by the user, and tracking activated. A fresh preparation clears the current prepared/prefill milestone and requires new review; earlier actions and status history remain available. Prefill receipts always require `submitted=false`. There is no external submit tool. Submission controls only record an explicit user report, labelled simulated in mock mode.

## MCP lifecycle and errors

The official Python MCP SDK initializes stdio, discovers capabilities and reads the registered MCP App resource. The existing owned launcher handles only its adapter process tree. Session timeout, parent interruption, compare-and-swap cancellation and SDK cleanup remain bounded. Adapters receive a minimal environment without backend Groq keys, session secret or database settings. Commands remain operator configuration, never browser input.

Tools are `inspect_application`, `open_application`, `prefill_application` and `application_status`. Only inspection/discovery/resource/status reads may retry automatically, once. Write retries require user confirmation. The frontend allows the configured bounded two-attempt resource/discovery timeout; preparation and writes run in background with persistent progress. Structured tool errors survive the SDK's AnyIO exception-group wrapping without exposing adapter stderr or raw exception messages.

Before prefill, the host re-inspects the form and verifies job identity, schema ID/version and the complete normalized schema fingerprint. Changed schemas invalidate approval and require fresh preparation. Malformed/unsupported or unavailable adapters retain a native source-grounded preparation fallback, with a visible connection/error state and no automatic prefill eligibility.

States additionally include `partially_completed`, `waiting_for_user`, `external_site_unavailable`, `unsupported_form`, cancelled and failed. Authentication/expired-session steps remain user actions. The mock reconnect control explicitly simulates completing such a step; configured adapters never receive authentication credentials or a bypass instruction.

## Partial completion and reconciliation

Each prefill has a stable operation ID, packet digest, private hash-only request evidence and validated receipt. A partial receipt must account for every approved field and document exactly once as completed or skipped/failed, with bounded error codes and retryability. Unknown adapter prose/receipt properties are discarded. No arbitrary provider error messages are stored or displayed.

A user-confirmed partial retry contains only unfinished field values and upload bytes. Completed fields are referenced by value hashes and completed documents by document ID/SHA-256. The mock server validates these references against its private durable SQLite journal; it also deduplicates identical operations. Tests inspect actual retry packets for both completed uploads and upload failures.

The journal records expected action hashes before work and completion hashes after each safe action. It can reconstruct a partial receipt after interruption, even if the final reply was lost. An uncertain prefill disables editing/fresh preparation/write retry until read-only reconciliation. When no action was ever started, reconciliation returns to fresh review without claiming completion. When a real adapter cannot establish an outcome, the application remains waiting for the user; blind writes are unavailable. Pause/cancel prevents late worker publication and never claims to reverse an external action.

## Mapping and documents

Mappings now distinguish residential city/country, LinkedIn, GitHub, a unique portfolio, institution, degree, graduation year, explicit internship entries, certifications, structured name parts, and explicit availability/notice periods. Ambiguous links or absent structured facts require input. Full names are never split by assumption. The existing confirmed-profile schema lacks some structured fields; these are entered explicitly for an application rather than invented or silently added to the profile.

Experience/objective/single-achievement excerpts may be shown as source-grounded draft answers, with mandatory confirmation and an explanation of their limits. No employer-specific motivation, achievement ranking, relocation preference, salary, work authorization, consent or demographic claim is invented. Sensitive controls require explicit input/review; employer-provided opt-out options are preserved. Password, API-key, session-token and authentication-code controls are unsupported and cannot store/prefill credentials.

Every field includes value, exact source path, confidence, auto-filled flag, suggested-answer flag, confirmation requirement and edit/review controls. Unsupported controls are visible as manual steps. All populated fields, including explicit checkbox false, require approval. Document selection/removal, role, MIME type, byte count, digest and an authenticated download for private preview are available. Selected originals are rechecked for ownership, confirmation and actual content SHA-256 before transfer; no document is attached automatically. Existing tailored/cover-letter uploads may be chosen explicitly; generating tailored documents remains outside this milestone.

## Persistence, surfaces and UI

Ten additive tables extend the existing `applications` / `application_events` records: application workspaces, fields, documents, status history, notes, interviews, follow-ups, external sessions, MCP actions and user approvals. Domain aliases expose Application and ApplicationAuditEvent without duplicate data. No existing columns or rows are rewritten by initialization. Sensitive application data uses the project's existing private local storage and session/ownership controls; SQL content is not encrypted by this prototype. External sessions deliberately have no credential/cookie/token columns.

Typed notes, interviews, deadline/follow-up tasks and completion states accompany the append-only timeline. Outcome details can record offer, rejection or withdrawal information. A profile change blocks new source-based writes but does not prevent reporting a submission already performed manually. Non-retryable partial controls offer manual continuation rather than an invalid write retry. `/applications/schedule` returns owned scheduled tasks; `/{id}/tasks/{task_id}/complete` uses the current revision. `/{id}/audit` returns field/document projections, source paths, tool/action status, receipts, approval history, status history, session metadata and tracking information under the same ownership checks.

The existing A2UI **interaction subset** adds summary/status/missing-input/document/approval/timeline descriptions to its allowlisted form surfaces. The native workspace implements document and confirmation controls; it does not claim complete A2UI standard conformance. Official MCP Apps SDK bridging remains real, with an opaque sandbox, no network/native form submission, text-only DOM rendering and host-only approval authority. The mock session-open control reveals the SDK review app; real session links use validated HTTPS receipts and remain direct user navigation.

AG-UI-style durable SSE now includes connection, discovery, field progress, upload progress, retry and user-approval events. Only phase/count/status information is streamed, never field values or private prompts. The UI polls active operations independently of their lifecycle status, provides confirmation dialogs, pause/reconnect/reconcile/retry/manual controls, per-stage counts, expandable audit, separate milestones, and actionable follow-ups. Existing responsive layout and reduced-motion design are retained.

## Demonstration

Open `http://localhost:5173/applications`, confirm a profile and select a saved/recommended synthetic job. Choose a mock scenario **before creating the application**:

- `standard`: ordinary reviewed prefill.
- `graduate`: scalar education/link fields and source-grounded question drafts.
- `partial`: one control fails once; completed work survives retry.
- `upload_failure`: first approved upload fails; field completion survives retry.
- `authentication` / `unavailable`: native fallback, explicit manual-step confirmation and reconnect.
- `unsupported`: required custom employer verification; continue manually or cancel.
- `schema_change`: changed form fails closed; prepare and approve the new version.

Review fields and selected attachments, save, approve the exact snapshot in the dialog, then explicitly start prefill. Inspect progress and receipts. After reviewing the form, separately report your own completion and activate tracking. All current listings and the built-in adapter are synthetic; no employer is contacted.

## Verification and limits

Verification passed: 146 backend/application/profile/job regressions, 65 focused application checks, 15 final protocol/recovery checks, and three final manual-recovery/privacy checks (overlapping passes, not additive); eight Chromium application tests; TypeScript checks; lint with zero errors and ten existing warnings; production build; and a real spawned-flow/stdio API smoke. Both database snapshots verified unchanged existing rows. The main application page returned HTTP 200 and API health reported a connected database.

Current verification results are recorded in [application-recovery-verification.json](application-recovery-verification.json); the initial additive migration preservation check is [application-recovery-database-preservation.json](application-recovery-database-preservation.json). Earlier milestone reports retain their historical results. This continuation runs focused application/profile/job checks and application browser tests, not unrelated full repository audits.

No real job-board adapter or authenticated employer session was available. Real-site uploads/navigation, independently verified submission, production login/recovery, encrypted storage, durable multi-worker queues and arbitrary employer custom widgets remain future work. Read-only reconciliation requires a real adapter to implement trustworthy operation receipts; an unknown outcome stays manual. Next: implement and evaluate one explicitly scoped real-site MCP adapter against a controlled test employer form, preserving these approval and reconciliation contracts.
