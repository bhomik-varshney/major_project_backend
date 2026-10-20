# Connected, layout-aware Resume Intelligence

This increment preserves upload, local OCR, native text, source-linked extraction, consent, retry/cancel/reject, profile confirmation, multi-skill editing, and job matching. It adds an evidence-connected canonical representation and an explicit verification boundary before creating the editable profile draft. No processing step confirms profile facts automatically.

## Architecture and actual CrewAI execution

There are three separate persisted outputs:

1. **Raw evidence**: original native/parser and OCR fragments, source IDs, page or paragraph, reading order, normalized coordinates when known, table cells/metadata, method and confidence basis.
2. **Canonical resume graph**: strict Pydantic entities, sections, typed values, evidence links, source relationships and competing readings.
3. **Review draft**: only source-supported canonical values are projected to the existing editable candidate draft; unsupported proposals stay in the audit and are withheld from this projection.

The existing preparation worker renders documents and applies page-specific OCR decisions. Consented image analysis uses an actual CrewAI visual Agent/Task; consented text extraction uses the actual two-agent Resume Intelligence Crew. A new genuine `ResumeCanonicalFlow(Flow[CanonicalState])` executes seven decorated, sequential tasks: document quality/layout, exact evidence indexing, OCR/vision reconciliation, canonicalization, validation, bounded repair, and review preparation. These Flow tasks perform deterministic work. They are not presented as seven LLM agents. A separate, actual one-agent/one-task `Canonical Resume Validation and Conflict Resolution Agent` executes narrowly scoped model repairs only when permitted and needed.

This reuses the existing killable preparation/vision/text workers, shared three-key Groq transport, source-claim grounding and strict provider outputs. CrewAI memory, telemetry, tracing, tools, code execution and delegation remain disabled. The installed/pinned CrewAI version is 1.15.28. [CrewAI Flow documentation](https://docs.crewai.com/en/concepts/flows) describes the decorated workflow mechanism; execution is tested against the installed runtime.

Local processing does not call a model. Its canonical tasks still execute through the CrewAI Flow runtime, recorded separately from the legacy local-parser/LLM metadata. Previously saved audits are readable without being silently reprocessed or rewritten.

## Quality, layout and evidence

PDFium character geometry and native link annotations preserve spelling and original destinations; DOCX paragraphs/table rows retain paragraph/table-cell boundaries; TXT has paragraphs with page unknown. Original text is never replaced by a visual paraphrase. OCR runs per page when native text is absent/sparse or image content warrants it. Every page reports not-required/completed/disabled/unavailable/failed outcomes with reasons. Readable native PDFs correctly show zero OCR blocks.

Quality output includes document type, page count, text availability, suspected scanned pages, layout/column indications, tables, per-page OCR recommendations and outcomes, vision candidates, warnings and recommended processing path. Column count remains null when geometry does not establish it. Cards/icons/sidebars/tables are structured visual block types; counts record model observations, not claims of geometrically perfect detection. Native geometry and page previews remain available when visual processing fails.

Existing layout validation corroborates visual text against original blocks and checks regions against source geometry. Unsupported visual readings stay visible with their source locations and warnings; they cannot establish exact field values. Model-supplied block IDs do not acquire authority without corroboration.

Non-standard headings can receive a semantic bucket only from a high-confidence (≥0.85), corroborated visual heading with geometry and an exact source span, within an otherwise uncertain section. Reliable existing section boundaries, entry titles and the first identity line are protected. Such bucket assignments remain **needs_review**. Real top-level DOCX heading styles and unusually large bold typography can establish an unknown boundary; an unknown heading stays unclassified. Arbitrary short lines are never universally treated as headings.

`ev_00001` identifies the corresponding original source block within its processing run. These IDs are scoped by the run ID. Evidence has `source_text`, `source_block_id`, page/paragraph, order, coordinates, extraction method, confidence and metadata. Native confidence scores are null rather than invented probabilities; OCR scores describe recognition, not factual correctness.

## Canonical schema

`app/schemas/canonical.py` defines strict, extra-forbidden schemas:

- `RawEvidence`
- `CanonicalValue`
- `CanonicalEntity`
- `CanonicalSection`
- `CanonicalRelation`
- `SourceAlternative` / `ResumeConflict`
- `CanonicalValidation` / `CanonicalIssue`
- `RepairIteration` / `CanonicalRepairInput`

Each canonical value has a fact ID, optional profile path, value, normalized value, original source wording, page, evidence IDs, source-block IDs, extraction methods, confidence/basis, status, warnings and related entity IDs. Unsupported values use `value=null`; the rejected proposal is retained as `proposed_value`. The initial canonical graph is also retained before repairs.

The graph has an explicit `resume` root. It connects to sections, sections to entities, and entities to their fields. Every entity and field must be reachable. Duplicate identifiers, dangling relations and unknown source references are rejected. Shared source evidence supports explicit link-to-project associations; spatial proximity alone does not establish a project technology or link. Project/education/work fields are checked against their established entry groups. Certification issuers are separated only from explicit issuer wording.

Buckets cover identity, summary/objective, education, work/internships, projects, skills/tools/languages/frameworks/databases/cloud, certifications/courses, achievements/awards, publications/research, volunteering, leadership, extracurricular activities, human languages, interests, links, references, other and unclassified content. Heading aliases extend the existing parser rather than replacing it. Exact source wording remains available even when no safe semantic classification is possible.

The compatible profile schema adds `additional_sections: [{heading, content}]` with empty defaults, preserving research, volunteer, leadership, languages and other original sections as confirmable content. Canonical buckets distinguish their meaning. Projects add explicit tools, outcomes, repository and deployment URL fields. Missing dates, roles and outcomes remain empty. Those labels are metadata inside a project, not new project titles. Existing profile records are not rewritten merely to add defaults.

## Validation and conflict resolution

Checks include schema/graph integrity, evidence membership, every nonempty field's literal source spans, exact-source spelling reconciliation, source-method confidence, project membership, duplicate skills/projects, dates/order, URLs, phone format, missing important fields, uncertain bucket assignments and source disagreements. Existing strict email/profile validators remain in force.

Supported fields and confirmed fields are different concepts. Heuristic confidence is explicitly labelled uncalibrated. A schema-valid graph can still contain uncertain or rejected claims; it is not labelled factually verified.

Native/parser, OCR, visual and agent alternatives are retained separately with method, page, block IDs, evidence IDs and confidence where known. Unresolved readings get an explicit conflict and require review. Existing local values are not silently replaced by a differing model value. Visual-only spelling corrections are forbidden. Exact spelling can be restored only from a real native/OCR source span; the prior proposed spelling and change are recorded.

## Bounded repair

`RESUME_MAX_REPAIR_CYCLES=3` allows 0–3 model cycles. Cycle 0 performs local spelling/source reconciliation and validation. A separate shared `RESUME_REPAIR_TIMEOUT_SECONDS=45` bounds all model repair work. Each model cycle receives only affected field paths and bounded relevant source/entry-group context. Unrelated supported fields cannot be patched. Candidate profile writes, invented values and cross-entry moves are not available actions.

Each cycle records iteration, detected issues, affected fields, action, post-validation result, model/provider, elapsed time, changes where applicable, outcome and safe error code. After a patch, both schema and source checks run again. The loop stops when no repairable problems remain, no progress is made, the provider fails, consent is absent, the deadline expires or the cycle cap is reached. Missing evidence and genuine conflicts do not trigger speculative completion. If issues remain, the graph and review draft carry warnings; unsupported facts are withheld.

The repair Agent is executed in the existing private spawned worker. Cancellation propagates to the owner and stops publication; provider failure retains deterministic results. The same shared key manager rotates across the configured three slots on retryable failures, distinguishes invalid keys and applies bounded cooldown/retry handling. No keys or prompts are stored in the canonical audit.

## API, history and database

Existing processing/status/edit/confirm/cancel/reject endpoints remain compatible. Legacy `queued`/`processing`/`awaiting_review` states remain usable by existing clients. More precise `analysing`, `canonicalizing`, `validating`, `repairing`, and `review_preparation` stages are visible during execution. Audit metadata separately exposes `canonical_status=draft_ready/needs_review` and all actual task outcomes.

Under `/api/v1/documents/{document_id}`:

| Endpoint | Purpose |
| --- | --- |
| GET `/audit` | Adds raw evidence, initial/final canonical graph, validation, iterations, canonical Flow tasks/status; optional `run_id` selects a saved owned version |
| GET `/canonical` | Typed strict canonical graph; optional `run_id`; 409 when a legacy run has no graph |
| GET `/evidence` | Typed raw evidence records; optional `run_id` |
| GET `/runs` | Latest 30 owned extraction versions and statuses |
| GET `/compare?left_run_id=...&right_run_id=...` | Field-level added/removed/changed draft values, version IDs and `profile_updated=false`; active runs cannot be compared |
| GET `/original` | Owned original file, attachment-only, correct media type, no-store and nosniff |

All endpoints use the existing signed local workspace session and ownership checks. Historical snapshots are read-only. The editable draft remains the latest extraction. Original files and preview blobs have no public filesystem URL. Existing page retention still keeps images for the latest two runs; very old previews may have expired even when their audit metadata remains.

Canonical records reuse the existing versioned `ResumeProcessingAudit` JSON table. No SQL column changes are required. Original uploads, old runs, profile facts, receipts and matching tables remain intact. `python -m app.db.migrate` remains idempotent and additive. Draft corrections preserve source provenance for unchanged items and label new user changes separately. Additional sections can be edited and explicitly selected for confirmation in the same transactional verification flow.

## Frontend

The existing audit/review layout is retained. Added foldable views expose:

- the original file download;
- extraction-version selection with a historical-view warning;
- connected entities and individual fields, original wording, evidence, confidence, related entities and correction links;
- sections, typed relationships and source evidence details;
- canonical validation checks;
- local/model repair iterations;
- competing source readings;
- actual canonical Flow task status;
- comparison between previous and latest draft versions.

Existing page overlays, native text, separate OCR outcomes, structured vision task output, model details, draft editing, warnings and final confirmation remain. Green marks support, amber uncertainty, red conflicts/rejected claims, purple OCR and blue text/layout evidence. Internal prompts and credentials are never rendered. History/version inspection does not edit or confirm the profile. Profile editing also exposes the additional original resume sections.

## Evaluation and verification

`python -m scripts.evaluate_canonical --output docs/canonical-evaluation.json` processes 13 anonymized styles through the actual upload/processing/audit APIs with external calls disabled: ordinary/objective/nonstandard/missing-section TXT, multiple projects, technology relationships, dated work, duplicates, missing dates, two-column digital PDF, DOCX tables, scanned PDF, dense two-page PDF and extended sections. Existing hand-authored field and project-technology expectations plus the new extended-section fixture provide **90 checks; 90 passed**. All final fields were source-supported, all graph schemas were valid, and no profile was created/changed by extraction. This is a small regression corpus, not a claim to handle every resume or a general accuracy benchmark.

The user's previously uploaded two-page resume was also evaluated in a separate private database. Both rendered pages and the native text were inspected. Checks covered the four project titles/groups and their exact technology sets, summary, contact fields, all three education entries/grades, two work entries/date ranges, skills, links and achievements. Native-only processing returned **133 source-supported fields, zero unsupported fields**, and correctly skipped OCR. The live, previously consented image/text path completed visual CrewAI with **16 corroborated and four review-required visual blocks**. Text extraction/quality was partially rate-limited by Groq, while final deterministic source validation retained **136 supported fields and zero unsupported final fields**. One visual-source conflict remains explicitly reviewable; it is not silently resolved.

The final graph was revalidated locally against those saved live outputs and the original PDF, with no additional export. Private resumes, page images, field values and credentials are kept outside the repository; only anonymized fixtures and aggregate results are committed-ready. Optional private evaluation requires explicit source DB/document ID/private output parameters; it never verifies a profile. The script's `--live` flag is an explicit export switch.

Model availability was checked against the actual Groq model-list endpoint on 2026-10-20. Requested `meta-llama/llama-4-maverick-17b-128e-instruct` and `meta-llama/llama-4-scout-17b-16e-instruct` were unavailable on this account; configured `qwen/qwen3.8-27b` and `openai/gpt-oss-120b` were available. The working configuration is preserved rather than replacing it with unavailable models. Both vision slots remain configurable and model availability/fallback is audited. [Groq's models documentation](https://console.groq.com/docs/models) documents the authoritative account/model-list mechanism; availability can change.

Final verification:

| Check | Actual result |
| --- | --- |
| Full backend suite | **296 passed**; existing dependency deprecation warnings remain |
| Canonical-focused coverage | **33 passing tests**, including genuine CrewAI task execution, exact source/relationship checks, longest technology names, malformed/unsupported claims, conflicts, no-progress/provider failure, zero/three-cycle limits, cancellation and confirmation/history |
| Full Chromium browser suite | **32 passed**, including graph/evidence inspection, repairs/conflicts, private original download, historical comparison, mobile overflow and existing matching/profile/document regressions |
| Frontend type checking | Passed |
| Frontend lint | Passed: 0 errors, 10 preexisting Fast Refresh warnings |
| Production build | Passed; no deployment performed |
| Python compilation / both repositories' diff checks | Passed |
| Desktop/mobile visual inspection and API/UI smoke | Passed at 1440px and 390px; no horizontal overflow |
| Anonymized API evaluation | 13 styles; **90/90 independent checks**, zero unsupported final fields, all profiles unchanged |
| Private real-document check | Native and live outputs reviewed against both pages; final graph/grades/dates/project-group checks passed; no unsupported final fields |
| Migration / preservation | Existing audit table reused; initialization passed; all **7 profiles and 8 verification receipts byte-for-byte unchanged**; private backup retained |

An additional source-precision check caught the old local parser shortening React Native to React inside a work description. That proposed parent skill was withheld in an intermediate run; the parser was corrected to retain the longest written technology name. Regression tests cover React Native and React Three Fiber. The normal application's `R1.pdf` now has a fresh **version 3 local review draft**, with **133 supported source fields and zero unsupported canonical fields**. Older versions remain inspectable. No confirmation endpoint was called.

The latest normal review version is deliberately local. The separate live test completed visual processing but recorded partial Groq text/quality stages due to provider rate limits; the source-valid deterministic draft remained available. These outcomes are not presented as universal AI success.

## Limits and next milestone

No parser can guarantee correctness for almost any layout. Overlapping columns, poor scans, handwriting, decorative fonts, complex nested tables, long documents and unusual unlabeled content may remain uncertain. Existing file/page/render/character limits remain explicit. Unknown source fragments remain available for review. Native/PDF link destinations and OCR text can disagree with visible labels; neither is silently rewritten. Groq quotas across keys on the same account can still interrupt enrichment. A deterministic draft may be more complete than a partially finished AI response; the audit shows both outcomes.

The initial canonical graph persists extraction interpretation; user corrections are separately revisioned, not falsely presented as original source text. Additional sections preserve original headed content rather than inventing detailed research/volunteer achievements. Education subject equivalence and factual truth still require candidate/employer review. This remains a local development session architecture, not production authentication.

Next: broaden human-labelled layout/relationship evaluation and add precise region/field correction interactions. If better scan quality is needed, evaluate another local OCR engine on a held-out corpus before changing confidence policies. Job skill-gap planning can consume confirmed fields after review; it must continue to exclude unconfirmed canonical drafts.
