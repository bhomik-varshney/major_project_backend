# Project analysis and backend implementation baseline

Reviewed: 19 October 2026. This is an analysis of the supplied documents and local source code, not a record of implemented backend features or approved configuration.

This records the pre-implementation review. The subsequent foundation milestone and its current status are documented in [the backend README](../README.md) and [milestone decisions](implementation-milestone.md).

## Sources and interpretation

Read the complete extracted text and tables of these supplied files, and visually inspected all 16 SDD figures and both SRS use-case diagrams:

| Source | Role in this review |
| --- | --- |
| `Agentic_AI_Career_Companion_Major_Project_Proposal-3.pdf` | Product motivation, agent responsibilities, and Phase 1 / Phase 2 vision. |
| `Agentic_AI_Career_Companion_Final_SRS.pdf` | Mandatory behavior, retained requirement identifiers, failure cases, acceptance conditions, and open decisions. |
| `Agentic_AI_Career_Companion_Final_Student_Level_SDD.docx` | Version 1.1, dated 19 October 2026; seven modules, selected providers/framework, proposed database, workflow diagrams, and screens. |

Working interpretation: use the SRS for required behavior, the newer SDD for the current design direction, and the proposal for wider context. Record differences explicitly rather than silently discarding requirements. The SDD is pending supervisor review. Its blank approval dates and illustrative values do not establish approval or measured results.

The attachments describe the application we are building. Their instructions about candidate review, tool permissions, and data processing are application requirements; they do not authorize this coding session to transmit resumes, submit applications, contact people, or operate external services.

The supplied SRS groups retained IDs; the detailed October 13 SRS referenced in its bibliography was not supplied. Its contents have not been assumed. Likewise, the referenced literature review, introduction, and presentation were not supplied.

## Product and scope

The shared **Career Intelligence Layer** is the authoritative candidate context: reviewed facts, preferences, supporting evidence, and a revision number. The **Career Orchestrator** selects relevant tasks, sequences their dependencies, validates results, and pauses for candidate decisions. These are logical responsibilities, not a requirement to deploy separate microservices or a graph database.

The core workflow is:

1. Identify the candidate and enter facts or upload a supported document.
2. Validate the file, extract readable text with source locations, and propose unverified structured facts.
3. Let the candidate correct and explicitly verify facts and preferences.
4. Retrieve and normalize jobs, preserve source metadata, and deduplicate listings.
5. Compare keyword, semantic, and hybrid ranking on consistent inputs.
6. Explain matches, uncertainty, eligibility limitations, and prioritized skill gaps.
7. Optionally generate a truthful job-specific resume or cover letter.
8. Review the exact version, action, and destination; record approval or rejection.
9. Prepare a package/manual handoff and maintain application history with evidence origins.
10. Evaluate matching, extraction, drafting, workflow completion, time, and human effort.

Saving and tracking a job does not require generating an application document first.

| Phase 1 mandatory | Conditional | Future / excluded from Phase 1 |
| --- | --- | --- |
| Profile and preferences; document processing; job retrieval; all three matching methods; explanations; skill gaps; resume/cover-letter drafts; human review; tracking; dashboard; controlled orchestration; evaluation. | Basic supported-form pre-fill, FR-APP-006, if D09 is selected. Live integrations require permitted access and configuration. | Proactive monitoring, learning paths, interview preparation, networking, portfolio analysis, career simulation, fuller MCP Apps, broader submission integrations, and autonomous recovery. Universal portal submission, real payments, and hiring guarantees are outside the prototype. |

The attached SRS states 53 distinct FR IDs: 52 mandatory and one conditional. It also states 24 high-priority NFRs. A labeled curated-data demonstration and one identified local user are permitted operating modes; they do not establish multi-user readiness.

## Technology decisions

| Component | Current documentary status | Implementation implication |
| --- | --- | --- |
| Frontend | React/Vite proposed; local draft actually uses React 19, TypeScript, TanStack Start/Router, Vite, Tailwind 4, and Radix/shadcn-style components. | Preserve existing routing and rendering conventions when connecting the API. |
| Backend | Python/FastAPI proposed by proposal, SRS, and SDD. | Suitable working direction; exact versions are not pinned yet. |
| Orchestrator | Earlier material allows CrewAI or custom; latest SDD selects CrewAI. | Treat CrewAI as the current direction. Keep authorization, persistence, and revision checks in backend services. |
| Language model | Latest SDD selects Groq Cloud with `openai/gpt-oss-120b`. | This is the documented selection, not verification of current availability, account access, or SDK compatibility. |
| Job data | Adzuna, Arbeitnow, labeled seeded/mock data selected in SDD. | Separate provider adapters; retain source status and distinguish live listings from fixtures. |
| Matching | TF-IDF/cosine baseline; embeddings; semantic similarity plus explicit verified skill overlap for hybrid. | The chat/generation model is not the embedding implementation. Select and record embedding model, dimensions, and preprocessing separately. |
| Storage | PostgreSQL proposed; pgvector optional. | Start with relational ownership and revision integrity; vector storage remains a configuration decision. |
| MCP | Defined tool access is planned; fuller interactive Apps are future work. | Do not describe a Python function or ordinary React form as a working MCP integration. Document any actual protocol implementation separately. |

No live provider requests or dependency upgrades were needed for this requirements review.

## Frontend source review

The two repositories are separate Git repositories. The backend has no application source, dependency manifest, schema, migrations, or tests; it contains Git metadata and an existing `.DS_Store` only.

The frontend has two product routes:

- [`index.tsx`](../../major_project_frontend/src/routes/index.tsx): marketing/product demonstration assembled from the CareerOS components.
- [`workspace.tsx`](../../major_project_frontend/src/routes/workspace.tsx): explicitly labeled sample workspace with hardcoded agents, opportunity queue, next moves, and an animated run.

The source contains approximately 3,122 lines across route TSX files and `components/careeros/*.tsx`, plus global styles, UI primitives, hooks, and rendering/error infrastructure. The review covered those product components, route/root setup, styling, package/configuration files, and server wrapper. Generic UI primitives were inventoried rather than individually audited.

### Existing strengths to preserve

- Shared dark/rose design tokens, consistent typography, self-hosted fonts, reusable surfaces/buttons, and responsive layout classes.
- Reusable job cards, score presentation, gap visualization, dashboard metrics, and agent-state presentation.
- Explicit sample-data labels in several important areas, including the workspace, opportunities, and application preview.
- Existing TanStack Query provider, file-based routes, error and not-found boundaries, focus styling, and reduced-motion rules.
- Keyboard/focus support in several interactive component previews. This is source evidence, not a completed accessibility audit.

### What is currently simulated or absent

| Surface | Actual source behavior | Required integration or missing behavior |
| --- | --- | --- |
| Entry/navigation | Links directly to `/workspace`; no identity gate. | Identified session, data-use notice, and owner-scoped access. |
| Profile constellation | Static skills, projects, education, experience, and goals. | Editable facts, evidence, verification, revision conflict handling, and hard/soft preferences. |
| Resume upload | No upload flow or file input in product routes/components. | Validation policy, upload receipt, extraction state, source evidence, review, and failure recovery. |
| Opportunities | Static companies, jobs, scores, factors, and reasons. | Search/source controls, normalized listings, pagination, freshness, method/configuration, evidence, and eligibility. |
| Skill gaps | Hardcoded proficiency percentages and target band. | Matched/missing/uncertain evidence, priority rationale, profile correction, and rerun. |
| Application preview | Fields are display elements. Review/Approve buttons have no handlers. | Versioned resume/cover-letter editor, claim feedback, rejection, exact-action approval, and permitted handoff. |
| Agent run | `setTimeout` advances fixed steps. Selecting a goal replays the same results. | Persisted operation/task states and typed results from real execution. |
| Workspace next moves | `useState` toggles checkmarks; reload loses changes. | Distinguish actual approval, task completion, and reminders; persist intentional user actions. |
| Tracking | No application list, event timeline, deadline editor, or status controls. | Durable applications, stage/outcome/origin, filtering, sorting, date precision, and notes. |
| Dashboard | Fixed counts, health score, and momentum bars. | Counts derived from saved records and clear empty/failed/stale/review states. |
| Evaluation | No evaluator route or run/report UI. | Dataset/judgment configuration, comparable runs, metrics, rubric, and missing-input states. |
| Footer | Newsletter submit only prevents default; links point to `#top`. | Treat as placeholders, not backend requirements for the career workflow. |

No product API calls (`fetch`, Axios, Query hooks, or Mutation hooks) or browser persistence were found in the reviewed product code. TanStack Start's server entry renders the frontend; it does not implement the proposed career backend. Existing SSR CSRF middleware does not establish authentication or authorize a separate FastAPI service.

### Differences to resolve during integration

1. **Target audience:** sample data emphasizes six years of experience and senior/staff roles in Europe. Documents emphasize students and early-career candidates. Use appropriate internship/graduate fixtures for the Phase 1 demonstration.
2. **Agent responsibilities:** the UI lists Planner, Researcher, Matcher, Skills Analyst, Application Agent, and Tracker. The SDD describes a coordinating orchestrator plus Resume Intelligence, Job Search, Match/Ranking, Skill Gap, Application Preparation, and Tracking agents. Map display names explicitly and include resume processing; the planner should not replace it.
3. **Scoring:** the opportunity preview displays skills, experience, goals, location, and compensation factors. The SDD hybrid is semantic similarity plus verified skill overlap. Render the actual method/components; do not fabricate five numeric dimensions to fill the existing layout.
4. **Skill meaning:** a resume does not justify precise proficiency values such as React 94% or Docker 46%. Prefer evidence states and supported requirement counts. A learning suggestion does not prove qualification or guarantee additional opportunities.
5. **Phase boundaries:** `Roadmap.tsx` puts application drafting/tracking under Phase 2, although both are mandatory Phase 1. Its 100%/45%/12% progress figures are hardcoded and unsupported by the implementation.
6. **Future behavior in current copy:** continuous scans, hidden opportunities, agents working overnight, learning plans, and recruiter-thread reminders imply future capabilities. Keep future/demo labels explicit when building the functional workspace.
7. **Consistency:** sample top-match references differ across components: the opportunity/workspace cards show Stripe Senior Frontend Engineer while the agent run names AI Platform Engineer and then recommends a Stripe platform role. Production views need shared record IDs and data.
8. **Operational feedback:** initial, empty, failed, partial, awaiting-review, and stale states need explicit product handling. Animation progress is not evidence that a backend operation completed.

The practical approach is to reuse the design system and component patterns while adding functional candidate screens. Merely replacing static arrays with API responses will not implement onboarding, verification, document review, approval, or tracking.

## Backend design implications

Use one modular backend initially, aligned with the seven SDD modules. Services should be callable directly from the API and through permitted agent tools, so a model does not become the authority for database writes or candidate decisions.

| Module | Requirement groups | Backend responsibility |
| --- | --- | --- |
| User/profile/preferences | FR-PROFILE-001–005, FR-PREF-001–002, FR-DASH-001–002 | Ownership, authoritative profile, verification, revisions, preference constraints, and dashboard aggregation. |
| Resume/document processing | FR-RESUME-001–004, FR-SKILL-001 | File validation, extraction, source locations, unverified proposals, and supported recovery. |
| Job retrieval | FR-JOB-001–006 | Source adapters, normalization, duplicate policy, source status, and provenance. |
| Matching/gaps/explanations | FR-MATCH-001–006, FR-SKILL-001–003, FR-EXPLAIN-001–003 | Three methods, evidence/uncertainty, eligibility, recorded configuration, and stable ranking. |
| Application documents | FR-APP-001–003; conditional FR-APP-006 | Verified-fact drafting, factual checks, version lineage, and optional supported fields. |
| Approval/tracking | FR-APP-004–005, FR-TRACK-001–004, FR-AGENT-007 | Explicit decisions bound to exact content/action/target/version, permitted handoff, and origin-bearing history. |
| Orchestration/evaluation | FR-AGENT-001–007, FR-EVAL-001–005 | Typed tasks/results, tool constraints, review pauses, reproducible manifests, and reports. |

These are mappings of the grouped supplied requirements, not reconstructed individual definitions from the unavailable detailed SRS.

### Data integrity to establish first

- One `CandidateProfile` per identified user. Derive ownership from trusted session context, not a client-supplied user ID.
- Preserve per-fact verification and evidence. Include certifications and achievements, even though the simplified SDD table list does not give them separate tables.
- Use profile revisions and conflict checks for edits; invalidate dependent matches, gaps, drafts, and tasks when relevant inputs change.
- Retain job snapshots/revisions as well as profile revisions, so retrieval updates cannot silently alter a draft, explanation, or evaluation input.
- Keep raw uploads private and separate from parsed text, normalized facts, and generated drafts. Retain document page/paragraph references where supported.
- Keep normalized skills separate from candidate evidence; preserve unknown or uncertain job requirements, dates, salaries, and deadlines.
- Store matching method, model/configuration identifiers, component scores, limitations, input references, and finite normalized outputs. An LLM should not invent the ranking score.
- Version generated documents. Bind approval to payload identity, input revision, document version, proposed action, and destination; any relevant edit requires a new decision.
- Separate application stage, action outcome, and event origin. `prepared`, `approved`, `handed_off`, `user_confirmed`, `external_receipt`, `simulated`, and `unknown` must remain distinguishable even if represented in separate fields.
- Use transactions and unique operation/idempotency keys to prevent duplicate logical application creation or approval-linked actions. Return success only after commit.
- Keep operation/task records with safe errors and recovery guidance. Partial outcomes must be expressible even though the SDD's short task-state constant list does not include `partial`.

The SDD's main relational entities are User, CandidateProfile, Skill, ProfileSkill, Education, Experience, Project, Document, JobListing, MatchResult, SkillGap, Application, ApplicationStatus, GeneratedDocument, ApprovalRecord, and EvaluationRecord. Agent task/result/error structures can share operation storage; they need not each become a separate table.

### Matching contract

Compute and compare all three methods against the same reviewed profile, frozen job set, filters, and judgments:

- Keyword baseline: TF-IDF and cosine similarity.
- Semantic: compatible embeddings under a recorded model and preprocessing configuration.
- Hybrid: the SDD proposes `H = w * S + (1 - w) * K`, with semantic similarity `S`, verified skill overlap `K`, and configurable weight `w`.

Document score normalization, overlap denominator, unknown-skill treatment, hard eligibility checks, soft preferences, tie ordering, thresholds, and method fallback labels before measuring quality. No hybrid weights are selected by this analysis. Missing job skill requirements must not silently become zero overlap, and high semantic similarity must not hide a known unmet mandatory condition. A similarity score is not a hiring probability.

### Proposed API boundary

The following is a starting contract outline, not an implemented or finalized OpenAPI specification. Use an `/api/v1` prefix and validate typed requests/responses.

| Area | Proposed operations | Essential response/context |
| --- | --- | --- |
| Health/access | Health/readiness; resolve session and capabilities | Storage health separate from optional provider availability; identified access scope. |
| Profile | Get/update current profile; explicitly verify selected facts/revision | Evidence, verification state, expected/current revision, and conflict details. |
| Documents | Upload; inspect extraction; review proposed facts | Document and operation IDs, validation/extraction state, source locations, and safe errors. |
| Jobs | Start search; inspect source results; list/get normalized jobs | Source states, dataset/live label, unsupported filters, retrieved time, and input context. |
| Matching | Start run; retrieve ranked matches, explanations, and gaps | Method/configuration, profile/job versions, components, eligibility, evidence, and limitations. |
| Drafts | Generate/get/edit resume or cover letter | Job ID, profile revision, document version, factual-check/review state. |
| Approval | Approve/reject exact draft/action/target | Version/payload identity, reviewer, decision time, and permitted next action. |
| Applications | Create/list/get/update; append status/notes | Idempotency, origin-bearing events, deadlines with precision, and committed state. |
| Operations | Start supported goal; inspect operation/tasks | Queued/running/review/completed/partial/failed/stale states, dependencies, and next actions. |
| Dashboard | Read saved summaries and pending decisions | Record links, current/stale state, and counts derived from committed data. |
| Evaluation | Configure/start/read/export runs | Fixture/judgment versions, consent scope, configuration, timings, metrics, and blocked inputs. |

Use structured validation/authorization/conflict/dependency errors with operation IDs. Persist operations that span external calls; polling is a sufficient initial UI transport. Specific HTTP codes, schemas, session transport, and cancellation/restart behavior belong in the implementation contract.

### Reliability, privacy, and human control

All 24 NFRs must remain traceable: performance measurement/timeouts/load; owner access, HTTPS, minimized disclosed provider data, retention, untrusted-content handling, and credential protection; transactional writes, idempotency, and independent stored views; usable labels/focus/error recovery; modular contracts, setup docs, and redacted logs; workload limits; compatibility/hardware verification; and evidence-grounded explanations, exact approval, and truthful outcomes.

The SDD records at most 30 seconds per external attempt, one additional Groq timeout retry or two rate-limit retries, and one source retry. Treat these as the current design limits and record the actual configuration. The attached abbreviated SRS does not itself print these exact counts. Rate-limit backoff and retries must be bounded across the complete operation; an uncertain consequential action must not be blindly repeated.

Provider failure must leave saved profile and tracking reads usable. Validate tool names, arguments, owner scope, profile revision, and required approval outside model-generated text. Uploaded documents, job descriptions, and model responses are data, not tool authorization.

## Decisions still open

These do not prevent this review or all implementation planning. Resolve each before its dependent capability is treated as complete; working defaults must be labeled as defaults.

| Decision | Current position / remaining choice |
| --- | --- |
| D01 | Preserve actual frontend framework; FastAPI/PostgreSQL proposed; CrewAI selected in latest SDD. Pin runtime/package versions and decide whether pgvector is needed initially. |
| D02 | Selected job sources are known; credentials, provider limits, allowed filters/fields, country coverage, duplicate rule, and curated-data provenance remain to be checked. |
| D03 | Groq generation model selected; identity/session mode, authorization, embedding model, permitted provider data, and processing disclosure/consent remain open. |
| D04 | Choose supported input/export formats, byte/page limits, parsers, and explicit scanned-file/OCR policy. |
| D05 | Select normalization, semantic preprocessing, hybrid weights, eligibility/ties, gap priorities, and final metric settings. |
| D06 | SDD proposes five profiles × 100 distinct jobs, K=5/10, three runs, one concurrent task, and reconciled two-reviewer judgments. Prepare permitted fixtures, manual baseline, rubric, hardware record, and acceptance thresholds; none are measured results. |
| D07 | Define deletion/retention for uploads, facts, drafts, logs, evaluation snapshots, and backups/provider handling. |
| D08 | Select deployment, browser/runtime matrix, workload limits, restart policy, and concrete timeout/backoff configuration. |
| D09 | Basic form pre-fill remains optional. Manual document/package handoff is the normal Phase 1 boundary. |
| D10 | Supervisor name is present in the supplied documents; required institutional formatting/designation and review status remain documentary matters. |

Some numeric pilot settings appear in the newer SDD while the attached SRS leaves their decisions open. Preserve that distinction in implementation and evaluation records.

## Implementation order

| Increment | Concrete completion condition |
| --- | --- |
| 1. Foundation and reviewed profile | FastAPI project/configuration, migrations/storage, identified owner context, typed errors, health/readiness, profile/preferences/evidence, revision checks, and explicit verification. A saved profile survives restart. |
| 2. Document-to-profile workflow | Supported upload validation, source-linked extraction, unverified proposals, correction/confirmation, and unusable-file recovery. Existing verified data is preserved until intentional changes commit. |
| 3. Discovery and matching | Labeled fixture source plus live adapters, normalization/deduplication, all three methods, eligibility, explanations/gaps, and stale-result handling. Same-input comparisons can be reproduced. |
| 4. Drafts, approval, and tracking | Truthful versioned documents, explicit approve/reject, stale-approval rejection, manual package/handoff, durable tracking/history, and origin-aware outcomes. |
| 5. Coordinated workflow | CrewAI uses tested service/tool contracts, scoped revisions, validated typed results, persisted task progress, review checkpoints, and bounded failures. UI reflects real operation state. |
| 6. Evaluation and integration acceptance | Frozen permitted fixtures/judgments, matching/task/document metrics, manual baseline, frontend workflow checks, failure-path evidence, and setup/demo instructions. |

Define operation and evaluation metadata in the earlier increments; do not postpone observability and reproducibility until the final evaluation step. Connect each completed increment to functional workspace screens as it becomes available.

The first useful backend milestone is **an identified candidate can save, review, and verify a revisioned profile in durable storage**. Resume extraction and every downstream agent then have a reliable shared context.

## Verification and review limits

- This pass analyzed local source and supplied documents. It did not modify frontend behavior, implement backend services, send personal data to providers, or perform external application actions.
- Frontend baseline checks used the existing local dependencies and Node `v23.7.0`: `npm run build`, `./node_modules/.bin/tsc --noEmit`, and `npm run lint`. All remained running for several minutes without a final result and were interrupted. The build emitted a plugin timing warning but no completed build result; TypeScript and lint produced no diagnostics before interruption. These checks are **inconclusive**, not passed or diagnosed as source failures. No dependency changes were made to force a result.
- No browser rendering or end-to-end interaction has been verified in this pass. Responsive/accessibility observations above are limited to source inspection.
- Acceptance needs requirement-linked evidence, including ownership denial, invalid upload, unverified-fact rejection, stale-input behavior, source outage/fallback, finite/method-correct scores, unsupported-claim handling, edit-after-approval rejection, idempotent writes, persistence failure, and truthful manual/simulated/external origins.
- Matching evaluation must compare identical inputs and preserve dataset/judgment/configuration versions. Record precision/recall, Precision@K, the selected ranking measure, and other chosen metrics with explicit undefined-denominator handling. The SDD additionally proposes F1, NDCG, appropriate MRR, extraction measures, latency, failure, and human-effort reports. Required approvals must be counted separately from corrective interventions.
