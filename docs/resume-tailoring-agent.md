# Resume Tailoring Agent

Implemented 21 October 2026 as an additive application/document workflow. Resume parsing, retrieval, hybrid scoring, skill gaps, cover letters and tracking retain their existing architecture.

## Use

Open **Resume tailoring** in the sidebar or **Applications → Tailor resume** on the intended application. Choose the supplied navy template and input mode. Start tailoring locally, or explicitly consent to the three-agent Groq/CrewAI workflow. Review the seven views: job requirements, tailoring plan, content review, change comparison, PDF preview, validation results and application attachment.

The workspace supports entry selection, order changes, source-backed summary variants, bullet editing, section order, full/selected-section regeneration and immutable version history. Unsupported edits produce a visible validation failure. Inspect the actual PDF and confirm the exact version before approval, then attach it to the same application. Approval or attachment never reports a submission.

## Inputs and factual authority

Every mode uses the existing `confirmed_fields()` profile source and an owned application/job snapshot:

| Mode | Behaviour |
| --- | --- |
| Confirmed structured profile | Generate from confirmed fields and the supplied template |
| Template/profile data | Same controlled template with explicitly confirmed structured data |
| Processed PDF | Require an owned PDF with an existing verification record for this profile; use current confirmed structured facts |
| LaTeX source | Store the UTF-8 source unchanged as an owned passive reference; generate clean source from confirmed facts using the supplied class/template |

Uploaded TeX is **never executed**, merged into the preamble or sent to the model. Source-only facts must first be confirmed through the existing profile workflow; this feature intentionally does not add another general resume parser or silently treat arbitrary TeX as verified facts. The upload checks the document envelope, not arbitrary TeX program correctness. A LaTeX reference therefore requires an existing confirmed profile. The original source/PDF and complete profile remain intact, even when content is omitted from the tailored view.

Job requirements, matching score/component context and missing/matched-skill lists come from the existing application snapshot/context. The existing skill-gap context helper is reused only for the same user, current profile hash/revision and selected job. When optional context is missing, a valid lightweight relevance plan is computed locally from the complete confirmed profile and selected job; the workspace shows its status and limitations. This does not recompute hybrid matching or skill-gap reports. Skill-gap requirements never become candidate skills.

## CrewAI workflow

`ResumeTailoringFlow` is a typed, genuine CrewAI Flow. The optional network path uses three actual `Agent`/`Task` instances in a sequential `Crew`, through the existing strict-schema Groq adapter and shared key manager:

1. **Resume Relevance Analysis Agent** selects/prioritises source entry IDs and gives reasons.
2. **Resume Content Tailoring Agent** proposes a supported summary, ordered entries, supported bullet IDs and section order.
3. **Resume Validation Agent** reviews that proposal for unsupported IDs, duplicate/missing sections and unsupported evidence.

The trusted rendering task runs under host control, outside model action authority. The agents have no tools, file access, code execution, document approval or submission authority. Telemetry/memory defaults remain disabled. Worker isolation, capacity, cancellation, deadlines and key leases reuse the established orchestration pattern. No private resume, uploaded source, contact details or generated document binary is exported during tests. Live checks use synthetic fixtures only. Real-user model processing requires the per-generation consent checkbox.

The host builds a catalogue from confirmed education, experience/internships, projects, skills, certifications, achievements and publications. Required/preferred/title token overlap guides ordering; matching and gap results remain advisory context. A unique highest qualification is prioritised using the existing education helper. The complete inventory is ledgered before selection. Every work/internship, project and skill is protected, along with primary education and relevant certifications/achievements; secondary schooling may be omitted with a specific reason. The full profile is never shortened.

Agent output references catalogue IDs rather than replacing factual fields. Names, contacts, organisations, roles, institutions, qualifications, periods, grades, project names, technologies and links must remain equal to their source values. Skills use the original confirmed labels; categorisation changes only presentation. Summary variants identify the target role as an application interest, never a previously held position. Bullet variants use complete source fragments or a conservative first-person-to-action-verb grammar change. No metrics, responsibilities, proficiency or company claims are invented.

This source-constrained approach intentionally limits free-form paraphrasing. User edits must match supported source-backed summary/bullet variants; novel factual content belongs in the confirmed profile first. The editor offers variants, entry/order changes and clear rejection messages rather than silently accepting unsupported assertions.

Every protected ID must survive selection and PDF validation. A single host correction restores all protected entries omitted by an agent, and a supported bullet for each retained work/project. Agent ordering and other valid choices remain intact. Corrections are audited and a local fallback remains available. Invalid factual proposals still fail and fall back once to the visibly labelled local selector. Model review concerns and corrections remain auditable.

Selected-section regeneration preserves every other base-version section before rendering. The host independently verifies those retained sections and the requested edits. Later one-page fit reductions are separately recorded and require review of the final output.

## Supplied template

- `app/templates/resume_tailoring/original.tex`: byte-identical copy of the supplied `resume.tex` (called `resume(1).tex` in the task text).
- `app/templates/resume_tailoring/resume.cls`: byte-identical supplied class, also copied into each build/artifact directory.
- `app/templates/resume_tailoring/document.tex`: trusted dynamic adaptation of the supplied source.

The class's `rSection`, `rSubsection`, `rSubsectionNoBullet`, `personalInfo`, name styling, navy-blue headings/rules, compact sections and letter-size paper are retained. `tableEnv` and `entry` retain the supplied two-column technical-skill/project design, with wrapping project labels to accommodate real titles. The class itself is not patched. Moderate wrapper spacing overrides remove the original negative header gap and avoid collisions. Body text remains 11 pt, using file-addressed Latin Modern Roman fonts from TeX Live; paragraphs are left-aligned. The class suppresses page numbers, as in the original.

Header mapping uses candidate name, location, email, phone and at most the primary LinkedIn, GitHub and portfolio links. Sensitive birth, gender, religion, marital-status and photo fields are not mapped. Contacts/links have visible text; no icon is the only representation of a contact. Missing fields and empty sections are omitted. Fictional names, institutions, employers, humorous projects and placeholder contact values are not inserted anywhere in the dynamic source.

## Escaping, compiler and one-page strategy

Generation reuses the existing character-by-character LaTeX escaping and safe `\href` construction. A single-pass slot substitution inserts escaped text into the trusted template. User TeX commands cannot become executable template instructions. Repository/deployment URLs remain exact confirmed links; URL validation and encoding protect the target syntax. Only safe links are rendered.

The reusable compiler helper creates a private temporary directory per attempt, copies the trusted class/source, sanitises the environment and launches only an operator-owned XeLaTeX/LuaLaTeX executable:

```sh
xelatex -no-shell-escape -interaction=nonstopmode -halt-on-error -file-line-error document.tex
```

It captures stdout/stderr, exit status, timeout, diagnostic/log references and generated-file existence. It observes cancellation during short waits and kills only its own process group when necessary. An abnormal compilation retries once in a fresh directory. Original source files never change. The generated `.tex` and supplied class are retained privately, including on failure. Compiler logs can be downloaded through the owned audit endpoint. No LibreOffice, GUI application or macOS UI automation is used.

This workspace reuses its ignored portable `.tools/TinyTeX` installation. Additional supplied-class dependencies installed locally are `scalerel`, `bbding`, `changepage`, `parskip` (and the existing `psnfss`/pifont, graphics, array, ifthen, tabularx, geometry, xcolor, hyperref, fontspec and Latin Modern packages). Runtime generation never downloads packages. Compiler discovery/configuration and time budgets reuse the cover-letter document settings. Missing dependencies produce readable errors, retained source and a bounded retry.

Fit is limited to three attempts with moderate 6/4/3 pt section spacing. The body font stays 11 pt. All employment/internships, projects, skills and primary education are protected; relevant certifications and achievements are also protected. Long descriptions use complete source sentences or extractive clauses, duplicate OCR lines are suppressed, skills are grouped, project assessment/team qualifiers are condensed with an explicit audit entry, and technologies already in the skill inventory are not repeated. Overflow may reduce additional bullets, with every omission recorded. An unrelated PDF QA failure never triggers content deletion. Unresolved overflow produces `resume_insufficient_space`, preserving the original profile and draft; its invalid PDF cannot be approved or attached. Older sparse versions must be regenerated before approval under the new content policy.

## PDF and visual validation

Checks require a readable, unencrypted, exactly one-page PDF; selectable text; every selected immutable field and bullet present; no fictional sample/placeholder text; embedded fonts; valid link annotations; no graphics; text within margins; expected section headings in reading order; no overfull/missing-glyph compiler warnings; and a successful PNG preview.

Glyph-intersection checks detect possible non-neighbour text collisions while excluding adjacent ligature/italic kerning. An initial check incorrectly treated summary text containing “Education” as the section heading; heading-order verification now checks whole extracted heading lines. Programmatic checks are heuristic, not a guarantee for every ATS or visual collision. Representative synthetic PDFs and desktop/mobile previews were inspected. The user must still review the final PDF and content before approval.

## Storage, API and application integration

Only one additive table, `tailored_resume_versions`, is introduced. It stores application/user ownership, version/revision, profile/job snapshots and hashes, request/template, structured draft, source references, changes/evidence/validation, artifact reference, document reference, approvals, tasks, state and timestamps. The existing `Document`, `ApplicationDocumentRecord`, `UserApproval` and sequenced `ApplicationEvent` models are reused.

Endpoints under `/api/v1/resume-tailoring` provide readiness/sources, passive source upload, application-specific start/list, owned version status/data, compile/approve/attach/cancel actions and authenticated PDF/preview/source/class/compiler-log artifacts. The version payload includes plan, draft, changes and validation; no duplicate independent APIs are needed for each sub-view. Edits and regeneration create new versions using `base_version_id`, `regenerate_section` and constrained `edits`.

States/history distinguish preparing, generated, rendering, validated, needs_review, edited, approved, attached, replaced, validation_failed, failed and cancelled. Interrupted active runs recover with an explicit failure and preserve their evidence/history. Stale profile/job hashes and revision conflicts fail closed. Approved versions are immutable.

Approval binds the exact draft, profile/job hashes, source template, version and PDF SHA-256. Approval and attachment both recheck file/document integrity. A tailored resume can be selected only for its own application/job/profile, using the resume role. It cannot be reused across unrelated applications or selected after replacement. The original confirmed resume stays in the document library; attaching replaces only the application's selected resume association. Prior application prefill approval is invalidated and must be reviewed again.

Approved derived resumes enter the existing MCP document packet, with its existing integrity, review and upload rules. No submit tool or approval bypass is added. Existing external work/submission locks prevent silently changing uploaded attachments. Timeline events record generation, review/approval, attachment and failure without reporting submission.

## Verification and limits

See `resume-tailoring-verification.json`, `resume-tailoring-live-api-smoke.json` and `resume-tailoring-data-preservation.json` for actual results. The synthetic example directory contains the genuine live three-agent output, structured draft, LaTeX, exact class, compiled PDF and rendered preview. It is a QA example, not the real user's personal application document.

Focused cases cover valid and sparse profiles, missing requirements/links/work, summary and skill ordering, immutable organisations/dates/grades/technologies, unsupported claims/metrics, fictional-content rejection, escaping/URLs, long names/skills, real overflow correction, retries, corrupt PDFs, one-page/selectable text/font checks, source/PDF input modes, editing/regeneration/history, ownership/revision/stale-source rejection, tampered PDFs, wrong-application selection, cancellation/recovery and original profile/document preservation. Related application approval/upload/recovery and cover-letter attachment cases were run; unrelated repository audits/suites were not rerun.

Limits: tailoring uses controlled wording rather than unrestricted LLM prose; source-only TeX facts require profile confirmation; lexical relevance and broad categories are not calibrated suitability scores; first-class coursework/leadership fields are not invented from free-form additional sections; table extraction and ATS vendor behaviour vary; sparse evidence may leave whitespace; excessively long mandatory content can fail the one-page limit. Future improvements can add source-reviewed wording variants, labelled relevance evaluation, template registries and a verified single-column ATS variant while retaining this supplied design as the default.

## Complete-profile improvements

See [the scoped regression report](resume-tailoring-improvements.md) for the missing-internship diagnosis, preservation ledger, computed relevance plan, one-column layout, verified before/after counts and focused test results. Historical verification files above describe the earlier milestone.
