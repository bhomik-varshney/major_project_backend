# Readable application preparation and MCP preview

This increment addresses the supplied application-workspace screenshots. It extends the existing application workflow only: resume parsing, the seeded job dataset, hybrid scoring and skill-gap analysis were not rebuilt or re-audited.

## Root causes and resulting behavior

| Observed issue | Cause | Change |
| --- | --- | --- |
| Education, experience and internships displayed as JSON | `text_value` serialized nested values with `json.dumps` | Dedicated readable transforms produce labelled text for employer text fields and structured reference cards for the preview. |
| Source paths appeared in the normal interface | Native and MCP renderers displayed audit provenance directly | Normal views use human-readable source/confidence labels; exact paths remain in Audit view. |
| Institution, degree and graduation year were blank | Scalar mapping required exactly one education entry | A unique highest-level qualification can supply these fields, requiring confirmation. All entries remain visible. Equal-level ambiguity requires user selection. Graduation years use explicit year/expected-graduation/dated-period evidence. |
| Links were missing or assigned to a generic portfolio | Every non-LinkedIn/GitHub URL was considered a portfolio; multiple matches were discarded | Classified platform links, repository/deployment context, conservative personal-site evidence and preserved additional links replace that rule. Multiple links remain visible, with a primary scalar suggestion where appropriate. |
| Application answers repeated objects or objective text | No question-specific preparation step | Deterministic evidence selection creates concise/detailed draft answers with evidence and explicit review. Unsupported questions and sensitive preferences stay manual. |
| The review app was a long flat form | No grouped presentation contract or view separation | Explicit sections, reference cards, compact optional inputs, Form/Preview/Audit/Tracking views and a compact sticky navigation/action bar. |
| There was no final MCP preview artifact | MCP discovery/prefill existed without a typed preview operation | A real local stdio `build_application_preview` tool generates the artifact; the host independently rebuilds and validates it before publication. |
| Tracking omitted useful stages and discovery controls | Existing status set and list controls were limited | Explicit recruiter-review/assessment updates, lifecycle aliases, search, deadline/follow-up sorting and an upcoming-task section. |

## Transformation design

`app/services/application_transform.py` separates presentation from persisted profile facts, field review and external-write packets. It provides URL normalization/classification, primary-education selection, readable date/scalar/repeated-entry formatting, question answering, legacy presentation and final preview generation. `app/services/application_preparation.py` uses those functions for typed employer fields instead of serializing objects.

Strict Pydantic contracts in `app/schemas/application_preview.py` define ApplicationPreview, ApplicationSection, PreviewField, RepeatedProfileEntry, LinkClassification, ApplicationQuestion, PreparedAnswer, MissingField and MCPFieldMapping. Existing application, status-history and audit persistence models remain canonical. The preview contains labelled sections and application-safe values, rather than exposing the raw profile object to a renderer.

Sections cover personal information, location, professional links, education, experience, internships, projects, skills, certifications/achievements and application questions. Repeated entries preserve confirmed institutions, roles, organizations, dates, locations, responsibilities, technologies, outcomes and links where available. Skills are grouped with a small controlled vocabulary; unmatched skills remain under Other skills. No skill is added by classification.

Reference cards are explicitly labelled as confirmed-profile context. **Only reviewed MCP form fields and selected approved files enter the prefill packet.** Editing a form answer does not silently rewrite an underlying education/project record or the confirmed profile. An unsaved edit disables approval and is flagged across views; the saved MCP preview updates when review is saved.

A full name does not establish culturally reliable first/middle/last-name parts. A country or university location does not establish the candidate's current city or street address. These cases now explain what explicit input is needed. Availability, notice periods, authorization, salary, consent and demographic answers remain manual unless explicitly supplied under the existing confirmation rules.

## Links

The classifier recognizes LinkedIn, GitHub, GitLab, Bitbucket, Kaggle, LeetCode, HackerRank, Stack Overflow, Behance, Dribbble, Google Scholar, ORCID and X/Twitter. Original URLs are preserved alongside validated display URLs. Domain/scheme casing is normalized; only known tracking parameters are removed. Destination-relevant path/query/fragment content is retained. Missing protocols are not invented. Credential-bearing or credential-query URLs, unsafe schemes and malformed URLs are excluded from automatic suggestions.

Repository paths and explicit project repository/deployment fields remain separate from a personal professional profile. Hosted deployments and explicit project evidence are considered before portfolio suggestions. Unknown URLs remain additional links; personal-portfolio suggestions require supporting URL/name/context evidence. A platform with multiple links keeps its full array and identifies a primary suggestion requiring review. Link validation does not contact sites or prove account ownership; site-specific URL patterns beyond the supported heuristics remain a future refinement.

## Application answers

Question types include motivation, relevant experience, project explanation, education and greatest achievement. Relevant confirmed experience/projects are selected against the selected role/required skills. Short answers are bounded to 600 characters, with a detailed alternative up to 2,400 characters and source evidence. These are deterministic templates/excerpts, labelled generated drafts rather than model-authored statements or automatically approved facts.

Motivation drafts describe the candidate's stated objective and supported job alignment, with a prompt to supply personal employer-specific reasons. They do not invent enthusiasm about a company's culture or mission. Multiple achievements are not silently ranked as the candidate's “greatest.” Unknown questions and unsupported personal/legal claims request input. Confidence badges are rule-based evidence heuristics, not calibrated probabilities.

## MCP preview and approval flow

```text
Selected owned job
  -> existing CrewAI Flow / MCP schema discovery
  -> grounded field mapping and readable transformation
  -> document selection metadata
  -> local MCP build_application_preview
  -> independent host schema/content validation
  -> saved application preview
  -> user edits and field/document review
  -> explicit snapshot approval
  -> MCP prefill and validated receipt
  -> direct user-performed external submission
  -> explicit user report and internal tracking
```

The existing six deterministic preparation stages now have an additional preview stage. The optional genuine six-agent CrewAI metadata review is unchanged. No new live Groq invocation or private-resume export was needed for this increment.

`build_application_preview` is a real tool on the owned local MCP stdio server. It accepts the typed draft, confirmed snapshot and selected job, returns the strict presentation schema, and never prefills or submits. Even when an operator configures an external adapter, unapproved preview facts go **only to the owned local preview process**, not that external adapter. The existing sanitized environment, bounded transport, read-only retries and process cleanup remain in use.

The host independently reconstructs the expected preview and checks equality. A malformed/unavailable preview process produces a labelled local fallback with the same schema. External-form unavailability/malformed schemas retain native preparation and require the existing manual recovery path. The UI distinguishes an actual local-MCP artifact, a fallback and a legacy read-only presentation.

Presentation metadata is excluded from the external-write approval digest; form/schema, field values, selected documents, job/provider and confirmed-source identity remain bound. Regenerating the same presentation cannot authorize a changed transfer. Preview refreshes run as tracked operations and preserve field review. Prefill progress/receipts update the displayed field statuses to prefilling, completed or failed; none marks the application submitted.

Existing drafts receive read-only readable presentation. Newly proposed or changed values are not considered reviewed. Older drafts must be saved/reviewed under the new presentation version before new prefill approval; old approvals cannot bypass that review. Previously completed/uncertain external actions retain the existing reconciliation safeguards. No saved draft or profile is rewritten merely by opening Preview view.

## A2UI, AG-UI and frontend

The existing A2UI interaction subset now carries grouped section descriptions and the typed presentation payload. React reference-card/preview components and the SDK resource render the same schema. This remains an explicit subset/custom renderer integration, not a claim of complete A2UI catalog conformance.

AG-UI-style durable events add PREVIEW_GENERATING and PREVIEW_READY alongside connection/discovery, mapping/validation, field/upload progress, approval, retry, failure and tracking events. Events contain operational metadata, not raw private prompts or field values. Existing authenticated SSE replay and active-operation polling are retained.

The native workspace defaults to Preview view. Form view provides grouped editable fields, source/confidence/review badges, draft alternatives and collapsed optional inputs. Reference cards preserve readable education, experience, internship, project and skill context. Audit view retains exact sources, field mappings, tool receipts, approvals and validation metadata. Tracking view contains notes, manual outcomes, interviews and actionable follow-ups. The compact sticky bar switches views and navigates to the approval section without obscuring the preview on mobile.

The official MCP Apps SDK resource now has organized Preview/Edit modes and the same readable sections/cards. Its opaque sandbox, DOM text rendering, no network/native-form submission and host-only approve/prefill/submission controls remain. Configured-provider resources receive masked unapproved presentation data. The resource was rebuilt from `src/mcp-apps/application.ts`.

## Tracking and data preservation

The persisted states remain compatible with existing applications. A `lifecycle_status` projection exposes saved, preparation_started, user_review, ready_to_prefill and awaiting_submission equivalents without rewriting historical states. Recruiter review and assessment are explicit user-updated states, with persisted status history and timeline events; activating internal tracking does not claim a recruiter has reviewed anything.

Application listing supports owned company/title search, status aliases and deadline/follow-up sorting. The upcoming-task panel uses owned scheduled tasks. Notes can record explicitly provided recruiter/outcome information, interviews retain their schedule/stage, and follow-ups retain completion state. Duplicate prevention and revision-bound mutations remain in place.

This increment adds **no SQL tables or column changes**. Existing JSON contracts gain optional preview/presentation metadata. Private backups were taken before refresh. Before/after comparisons verified that all existing rows across 27 tables were unchanged; see [data-preservation evidence](application-readable-preview-data-preservation.json). No user application was automatically re-prepared, prefilled or submitted. LibreOffice was not launched.

## Verification

See [machine-readable results](application-readable-preview-verification.json) and [anonymised local-profile evaluation](application-readable-preview-local-evaluation.json). Test runs overlap and must not be summed:

- Application/recovery/presentation regressions: 95 passed.
- Final presentation-specific checks: 27 passed, including real stdio, fallback/malformed schemas, answers, links, legacy behavior, tracking, dates and grouped data.
- Additional address-granularity guard: passed.
- Browser application suite: nine passed; final native-flow/SDK/graduate-layout recheck: three passed; added legacy-review regression: passed.
- TypeScript checks, production build and bundled MCP resource: passed.
- Lint: zero errors, ten pre-existing shared-component warnings.
- Desktop/mobile previews were inspected; mobile overflow and approval-flow checks passed. The oversized sticky approval panel found during visual inspection was replaced with a compact bar.

The screenshot-like synthetic profile exercises multiple education records, internship/training experience, projects, platform/additional links and confirmed achievements. A separate read-only local MCP check on the existing application found three education entries, two experience entries, four projects, populated scalar education fields and no internal paths/raw JSON values in the preview. The report contains counts/booleans only; names, contact details, URLs and resume text were not written into it. No external provider export occurred.

## Use and limitations

Open `http://localhost:5173/applications` and refresh. Select an existing application, then Preview view. **Generate MCP application preview** refreshes its presentation. Use Form view to edit and explicitly review values, save, approve the snapshot and then start prefill. For an older draft, save the reviewed application or prepare a fresh draft before a new transfer. Mock graduate forms exercise the additional scalar fields and project questions. Audit and Tracking remain available as separate views.

The built-in adapter and current job listings remain synthetic. No real employer/browser adapter or independent submission verification was added. Answer templates are bounded, deterministic drafts; highly employer-specific questions still require the user. Some profile fields have no structured source and remain manual. The local worker/authentication/storage and partial-reconciliation production limitations described in [the continuation report](application-recovery-continuation.md) still apply.

Recommended next milestone: integrate one controlled real employer test form with explicit adapter-specific field constraints and trustworthy operation receipts; evaluate URL patterns and answer quality on consented labelled examples while preserving the current approval boundaries.
