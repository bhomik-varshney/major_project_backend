# Cover Letter Agent implementation

Implemented 21 October 2026. This milestone extends the existing application workspace; it does not replace resume parsing, retrieval, matching, skill gaps or tracking.

## Operator workflow

1. Open **Applications**, select the intended job/application, then **Prepare cover letter**. The sidebar also has **Cover letters**.
2. Choose tone, length and letter date. Generation defaults to the explicitly labelled local evidence composer. Select the consent checkbox to use the three CrewAI agents through the existing Groq transport/key manager.
3. Upload your own signature as PNG, JPEG or WebP (2 MB maximum), supply the signer name and confirm ownership/local storage. Select it and approve use for this document. Alternatively explicitly choose a typed name only. A missing image signature puts the draft into `waiting_for_signature`; it is never substituted with a generated signature or a template placeholder.
4. Read the text and source evidence. Edit paragraphs or add a personal sentence with explicit accuracy confirmation. Save edits as a **new version**. Paragraph-only regeneration preserves the other paragraphs from the selected base version.
5. Inspect the actual rendered PDF preview, including signature placement. Download PDF/source as needed. The exact PDF requires its own review checkbox and approval before attachment.
6. Attach the approved version to the same application. This creates a document association and timeline event, invalidates any previous application prefill approval, and leaves submission under the user's control.

Existing approved versions remain immutable. Changing signature, tone, wording or content requires a new version. Replacing an attached letter records `replaced` on the previous association. Existing external prefill/submission work cannot be silently modified.

## Inputs and ownership

Generation starts from an owned application created from a saved or recommended job. It calls the existing `applications.source()` and `confirmed_fields()` services. Empty/unconfirmed drafts are not an input source. Candidate name and selected job title are mandatory. Missing contacts, company and optional links are omitted cleanly; missing company retains `Dear Hiring Manager,`.

Each version stores its confirmed profile revision/hash, candidate and selected-job snapshots, job hash, request preferences, generation date, signature reference, content, evidence, validation, private artifacts, approval and document history. No profile facts or match scores are written by the agent.

Matching context comes from the application's saved matching explanation/score components, only when its profile snapshot is current. A completed/partial skill-gap result is used only for the same owner, confirmed profile hash/revision and selected job. Gap labels are context, not proof of proficiency. Missing context is reported rather than recomputed or invented.

## CrewAI and grounding

`CoverLetterFlow` is a typed, genuine CrewAI Flow using the project's privacy-configured runtime. Network generation uses three actual `Agent`/`Task` instances in a sequential `Crew`:

| Agent | Structured output | Authority |
| --- | --- | --- |
| Research and Relevance | Evidence IDs and warnings | Recommends relevant confirmed facts |
| Writing | Ordered sentence IDs and warnings | Arranges source-backed prose, never authors new facts |
| Validation | Unsupported IDs and issues | Audits the proposed selection; never grants approval |

The host constructs a bounded sentence catalogue from relevant skills, education, internships, projects, objective and achievements. Selection uses job-title and required/preferred-skill overlap. Dates/numbers in source fragments are not rewritten. Whole complete source fragments are preferred; an overlong fragment without a complete sentence is omitted rather than truncated into a new claim. Missing skills may appear as job requirements or learning interests, never as acquired candidate skills.

All model outputs use strict Pydantic/JSON schemas. Unknown sentence/evidence IDs, duplicate selections, missing opening/closing and unsupported claims fail validation. Every generated factual sentence must equal a trusted catalogue sentence with valid source references. This constraint intentionally limits free-form paraphrasing in exchange for auditable grounding.

The model reviewer can itself make a mistake. One bounded independent host correction checks the exact selected sentences and evidence IDs against the immutable catalogue. If the critique is contradicted by that source check, the disagreement and correction remain visible (`provider.status=repaired`). Actual invalid selections fail; they are not accepted on the model's authority. A rejected/unavailable provider falls back once to the labelled local composer, with error code and correction recorded. A fallback is never represented as completed AI generation.

User edits/personal sentences are separately attributed and require explicit attestation. They do **not** become independently verified profile facts. Retained paragraphs from a prior version are identified separately from newly entered text. Approval still requires review of every claim. The host reconstructs permitted user edits/base paragraphs independently of the worker to prevent a model from labelling invented prose as user input.

Generation reuses the isolated multiprocessing worker, deadline, cancellation checks, shared Groq key leases and thread capacity limits. Telemetry/memory defaults remain disabled. Only selected evidence and job context are exported after the per-generation consent checkbox. Contact details, full resumes, signature images and document binaries are not exported to Groq.

## Template and LaTeX

- Original provided file retained unchanged at `app/templates/cover_letter/original.tex`.
- Trusted dynamic template: `app/templates/cover_letter/letter.tex`.
- All four supplied Source Sans Pro OTF assets are bundled in `app/templates/cover_letter/fonts/`.
- The supplied A4 design, blue colour, three-part header, divider, heading, body, signature alignment and footer are preserved. Unused preamble packages were removed in the dynamic template; the original remains untouched. Side-contact size and paragraph spacing adapt to real content; body text remains 11 pt and left-aligned.
- A single-pass `@@SLOT@@` substitution inserts escaped values. A substituted user value is never evaluated as another template instruction.
- Character-by-character escaping handles backslash, ampersand, percent, dollar, hash, underscore, braces, tilde and caret. Literal user LaTeX cannot execute.
- Links reuse the existing safe URL/classification rules, are encoded/escaped and inserted with `\href`. Only primary LinkedIn and a supported personal portfolio/website are used in the header. Other links remain in the confirmed profile/application audit.

Compilation command in a fresh private directory for each attempt:

```sh
xelatex -no-shell-escape -interaction=nonstopmode -halt-on-error -file-line-error letter.tex
```

LuaLaTeX is also supported. An operator-owned executable path is accepted in `COVER_LETTER_LATEX_PATH`; browsers and agents cannot supply a command. The worker sanitises its environment, enables paranoid TeX input/output settings, copies only trusted fonts/source/signature into the compile directory and owns the subprocess group. stdout, stderr, exit status, timeout and output existence are captured. Compiler timeouts terminate only that owned process group. No LibreOffice, GUI application or macOS UI automation is used.

The source is retained on dependency, compilation or quality failure. An abnormal compile retries once in a fresh directory. Layout QA uses at most three different spacing configurations (12/10/8 pt) without shrinking body text or fabricating content. Remaining overflow fails visibly; it cannot be approved or attached. Use Shorten/edit and generate a new version if content still exceeds the page.

## Local compiler setup

This workspace has a portable TeX Live/TinyTeX distribution under ignored `.tools/TinyTeX`, with no global PATH changes or TeX user-profile changes. Its package manager was used to install the required template packages. The official [TinyTeX installation documentation](https://yihui.org/tinytex/) describes the portable distribution.

For another machine, install XeLaTeX or LuaLaTeX and the packages `preprint` (fullpage), `titlesec`, `fancyhdr`, `ragged2e`, `fontspec`, `geometry`, `xcolor`, `graphics`, `hyperref`. Discover from PATH, `/Library/TeX/texbin`, or `.tools/*/bin/*/xelatex`, or configure the absolute executable path. Runtime generation never downloads TeX packages automatically. Missing engine/fonts produce an explicit error with the draft/source retained.

## Signature and PDF checks

PNG/JPEG/WebP files are decoded, size/dimension/frame/visible-ink checked, normalised to PNG, stripped of metadata, cropped to ink and resized without changing aspect ratio. Transparency is preserved where present. Blank, corrupt, oversized, animated or unsafe formats are rejected. SVG is intentionally not accepted; export to PNG first. Signer name must match the confirmed candidate. Every replacement is a new immutable asset; no signature is invented.

Signature inclusion stores its digest, signer, requested width and user approval. The image appears above the typed name, left-aligned, width 20–50 mm and maximum height 14 mm with `keepaspectratio`. The actual image bounds are checked against text and page bounds. The signature remains private and local; this is an image signature, **not a cryptographic/certificate-backed PDF signature**.

Post-compile checks verify:

- readable, unencrypted, exactly one-page PDF;
- absence of template placeholders/Lorem ipsum/John Snow/fake template contact links;
- candidate name, selected job and company (when available), and every complete paragraph present in extracted selectable text;
- embedded fonts, valid PDF link URIs and no missing-glyph/overfull-box diagnostics;
- text inside page bounds, signature image presence/bounds and no signature/text intersection;
- successful PNG preview render.

The PDF/preview/source are authenticated artifacts. Failed or cancelled PDFs cannot be downloaded as validated output. Private compilation logs are available in the expandable audit. Logs may contain the document's own source text and stay behind the same ownership checks; no private content goes to server logs. Programmatic QA is heuristic, not a complete proof of visual or ATS behaviour. Human preview/claim review is mandatory before approval. Representative signed desktop/PDF and mobile layouts were also inspected during implementation.

## Application, MCP and UI integration

Only two additive tables are introduced: `signature_assets`, `cover_letter_versions`. Approved PDF bytes reuse the existing `Document` library; attachment reuses `ApplicationDocumentRecord`, `UserApproval`, canonical application revisions and `ApplicationEvent`.

Approval binds content, profile hash, job hash, signature metadata, version and PDF SHA-256. The bytes are rechecked before approval and attachment. Stale confirmed profiles/jobs fail closed. Job-specific generated letters cannot be selected for another application's document packet or used as a resume. Replaced versions cannot re-enter a fresh prefill packet. Attaching the letter never marks an application submitted.

The existing MCP attachment packet can upload the approved PDF only after the application itself is reviewed and approved for prefill. No new submit tool or approval bypass is introduced. AG-UI-style stage, input-request, error and completion events enter the existing replayable application timeline. The cover workspace polls its owned version while work is active and presents existing application state separately from letter state.

Letter states include preparing, draft_ready, validation_failed, waiting_for_signature, rendering, preview_ready (review projection: needs_user_review), approved, attached, failed, cancelled and replaced. Document history records generated, validated, reviewed, edited, approved, attached and replaced transitions. The workspace has job/profile summary, tone/length/date, text editor, full/selected regeneration, personal sentence, signature management, word count, model/fallback metadata, warnings, source/compile/PDF status, private preview/download, approval, attachment and version history. Evidence/claims/compiler details are expandable; raw internal JSON is not the normal interface.

## Validation and limitations

Machine-readable results: `cover-letter-verification.json`, `cover-letter-live-synthetic.json`, `cover-letter-data-preservation.json`. The signed sample under `docs/examples/cover-letter-synthetic/` uses a synthetic test-ink pattern and synthetic candidate; it is **not an application-ready document for the real user**.

Tests exercise normal students, internships/projects without full-time work, absent portfolio/contacts/company, long projects/skills, many requirements, escaping and unsafe URLs, transparent/opaque/invalid/missing/replaced signatures, compilation and clean retries, one-page overflow correction/failure, selectable text and preview, edits/partial regeneration, version history/replacement, cancellation/recovery, ownership, stale source, PDF tampering and wrong-application attachment. Related existing preview/conversion and application approval/upload/recovery/lifecycle checks are included. Unrelated repository audits/suites were not rerun.

Known limits: Source-constrained prose is less flexible than unrestricted LLM writing. Sparse profiles can produce fewer than 300 words, with a visible warning; evidence is preferred over padding. Semantic truth of newly attested user edits is not independently established. ATS vendors, all languages and every visual collision are not exhaustively tested. Source downloads require the bundled font assets and approved signature asset to reproduce the PDF. Existing mock jobs remain synthetic, and external submission remains manual. No personal signed cover letter was generated during testing because the user's actual signature has not been provided in this workspace feature; the UI requests it before rendering unless explicitly omitted.
