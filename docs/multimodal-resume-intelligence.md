# Layout-aware Resume Intelligence milestone

Current model/token-budget follow-up: [working Qwen vision and compact text requests](provider-model-token-fix.md). The account-availability checks below describe the initial checkpoint before this follow-up.

19 October 2026. Local backend/frontend implementation. No deployment, push, or production authentication changes.

## Behavior and review boundary

Uploads remain private PDF, DOCX and UTF-8 TXT records. A processing run combines original parser text, optional page rendering/local OCR, optional visual CrewAI analysis, optional text CrewAI reasoning, deterministic source validation, and a human-reviewed draft. Agents never write profile tables. Existing revision-checked confirmation remains the only document-to-profile write path, with section selection, explicit replacement, atomic receipts, and provenance. Edit, correction, rejection, cancel, retry, multiple-skill editing and existing dashboard styling remain supported. Publications now use the same draft/edit/confirmation path as other optional list sections; old JSON profiles default to an empty list without rewriting stored facts.

## Architecture

`services/document_intelligence.py` prepares local evidence in a spawned, bounded process. `orchestration/vision.py` executes an actual CrewAI `Crew`, `Agent` and typed `Task` using a custom multimodal `BaseLLM`. `orchestration/resume.py` then runs the existing real two-agent sequential text crew with visual layout included in its prepared context. Separate visual/text executions let a vision failure retain independent text processing. This is one application workflow with three genuine agent tasks, rather than calling ordinary parser functions agents.

1. **Local document analysis / evidence preparation:** pypdf content-stream text, DOCX body paragraphs/table rows, or TXT lines. Original page/paragraph/order/block IDs, PDF positions/font size, DOCX styles/list/table metadata and exact parser text are preserved. "Exact" means parser-returned source text, not a byte-for-byte facsimile of file encoding. Quality heuristics indicate text-readable, image-only, mixed or layout-complex input; selectable characters, image coverage, possible columns, tables and OCR/vision recommendations are auditable. A PDF rectangle grid is only a table heuristic. Complex shapes/form XObjects and arbitrary nested layouts remain uncertain.
2. **Private rendering:** PDFium renders proportional, uncropped PNG pages, normally up to 180 DPI with longest dimension capped at 2,200 px. DOCX is converted by local headless LibreOffice using its own temporary profile, then rendered with PDFium. Original DOCX paragraphs retain their original paragraph evidence; a unique text match can additionally establish a converted-page location. Layout fidelity depends on available fonts and LibreOffice. No synthetic text screenshot is passed off as a faithful DOCX page.
3. **Conditional local OCR:** Tesseract TSV is used on missing/sparse selectable text or detected substantial raster coverage. Recognized lines carry page, normalized top-left box, engine confidence and warnings. OCR is separate from exact text; only novel OCR lines join the evidence stream, retaining their distinct origin. Recognized text is not automatically trusted. Missing/failed Tesseract, disabled OCR, incomplete rendering, timeouts and unreadable pages have explicit warnings.
4. **Document Layout Analyst:** real CrewAI visual task sends only consented rendered pages through the shared backend Groq transport. Its strict Pydantic result contains column type, section/block regions, raw visible text, reading order, relations, block types, confidence and warnings. Agent/task instructions treat documents as untrusted data; tools/delegation/code execution/memory/telemetry are disabled. Model source IDs are discarded and rederived from native/OCR evidence. Unknown pages, duplicate IDs and invalid boxes are rejected or withheld. Relations reference actual returned blocks only. A visual string containing just one known word cannot validate a larger invented claim.
5. **Resume Intelligence Agent:** actual CrewAI text task produces typed facts with literal block/quote evidence, section grouping and pending review state. Recognizes Introduction and Personal Statement in addition to previous heading variations; separates project title/description/role/technology/dates/links. Projects, employment and education still pass conservative section/entry association checks.
6. **Resume Quality Validator:** actual dependent CrewAI task audits the proposed result against original evidence and visual context. Deterministic checks always run after optional agent work: literal source anchors, duplicate entries, missing evidence, uncertain fields and reversed dates. Source validation remains authoritative over model assertions.
7. **Local review coordination:** merges supported suggestions conservatively, records intermediate typed task output as unverified, retains conflicting readings with their source, and prepares the existing editor. Contradictory contact readings or nearby visual/native differences mark affected evidence for review. Unrecognized labeled sections remain in the audit rather than being merged into skills/projects. Visual-only text cannot create a profile fact without native/OCR corroboration. Candidate edits lose extracted-source claims and remain explicitly candidate-entered.

## Models and availability

Backend configuration:

```dotenv
GROQ_API_KEY_1=
GROQ_API_KEY_2=
GROQ_API_KEY_3=
GROQ_TEXT_MODEL=openai/gpt-oss-120b
GROQ_VISION_MODEL_PRIMARY=meta-llama/llama-4-maverick-17b-128e-instruct
GROQ_VISION_MODEL_FALLBACK=meta-llama/llama-4-scout-17b-16e-instruct
```

Nonempty `GROQ_TEXT_MODEL` takes precedence over the legacy `GROQ_MODEL`. The primary/fallback configuration is preserved exactly as requested. Both tasks check Groq's live `/models` list before use. Vision tries configured available models in order; unavailable/retryable/invalid-output failures can fall back to the secondary model within the same hard deadline. Malformed application requests and input-limit failures do not blindly repeat across models. Vision uses JSON mode with the explicit compact schema in the prompt, followed by server Pydantic validation; the text model uses native strict JSON-schema output. A second unbounded repair/conversion loop is prohibited.

**Live account check on 19 October:** text model available; neither configured Llama 4 vision model listed. The synthetic live vision smoke therefore returned `groq_vision_models_unavailable` and a truthful local fallback. No successful live vision accuracy claim is made. Updating model configuration to an available vision model and restarting enables the same visual task; this milestone does not silently substitute an unrequested model. Real CrewAI visual execution, multimodal request construction, primary/fallback behavior and output validation are tested using scripted transport.

The existing process/thread-shared `GroqKeyManager` is reused by model-list, vision and text requests. It cycles unique nonempty slots 1 → 2 → 3 → 1, skips busy/invalid/cooling keys, distinguishes authentication failures, and cools/rotates on retryable HTTP/network/quota failures. Requests have bounded attempts/timeouts and parent cleanup releases terminated-child leases. Separate application replicas still need an external shared quota store. Multiple keys may share account limits. No keys or raw provider errors reach frontend responses, logs or committed files.

## Runtime bounds, cancellation and retention

The one-worker/four-slot queue remains. Local preparation defaults to 50 seconds; each converter/OCR subprocess is bounded. On POSIX systems the local worker starts a process group; cancellation terminates the worker plus converter/OCR descendants and removes its private scratch directory. Recovered evidence is retained with an incomplete-analysis warning if optional page analysis times out or exits after text extraction. Cancellation never publishes a final draft or profile change. On restart, interrupted runs become failed/retryable without automatic external reprocessing.

Default intake remains 5 MiB, 50 pages and 60,000 source characters. Rendering/OCR covers at most 12 pages; visual AI covers at most 3 rendered pages. Each retained preview is at most 3 MiB, with at most 18 MiB for one run. Additional text remains available and omitted preview/OCR/vision pages are explicitly flagged. Page blobs are retained for the two most recent processing versions of each source. JSON audits and source text remain until source deletion; page metadata for older runs may outlive its blob. Originals are never duplicated into a public directory. Temporary files use private directories and server UUID names; no arbitrary shell commands or network document converters are used.

## Data contracts, database and API

`schemas/intelligence.py` defines `ProcessingRun`, `PageArtifact`, `LayoutBlock`, `ExtractedTextBlock`, `OCRBlock`, `ResumeDraft`, `EvidenceItem`, `ConfidenceRecord`, `ValidationWarning`, `AgentRun`, and `ReviewDecision`. Native/OCR/visual and text-reasoning methods remain distinguishable. OCR recognition scores and model/layout confidence are uncalibrated; they are not probability-of-truth scores. Current draft evidence is generated from the current edited values, and review decisions come from the existing extraction state/receipt rather than a stale AI snapshot.

Two additive tables are introduced:

- `resume_processing_audits`: one JSON audit per existing extraction, owned by a user, cascading with its extraction.
- `resume_page_artifacts`: one private PNG blob per rendered page/extraction, UUID, owner, unique page, cascading with extraction deletion.

Nine tables total; no existing columns are altered. Existing `document_extractions`, `resume_agent_runs`, text JSON, profile JSON and confirmation receipts are reused. `python -m app.db.migrate` stays idempotent additive creation. Existing profile facts/revisions/verification flags are preserved. Old extractions expose real legacy text and a reprocess notice, without inventing unavailable artifacts.

API additions:

| Endpoint/field | Behavior |
| --- | --- |
| `POST /documents/{id}/process` | Optional `use_vision`, `allow_image_processing`; vision requires both image and existing text consent. Legacy clients remain text-only. |
| `GET /documents/{id}/audit` | Typed current processing run, quality, exact text, OCR, page metadata, layout, task results, warnings, current draft/evidence and review decision. Available for failed/rejected/cancelled runs too. |
| `GET /documents/{id}/audit/pages/{artifact_id}` | Owned PNG bytes, session + local-client header required, `no-store` and `nosniff`; no public/static path. |
| `GET /documents/policy` | Actual local OCR availability, audit support, text model and configured vision names. |
| Field evidence | Additive `extraction_methods`, `layout_block_ids`, `ocr_confidence`. |
| Profile/draft | Additive optional `publications`; confirmation fields limit increases to 16. |

Existing outer statuses stay backward compatible (`processing`, `awaiting_review`, `verified`, etc.). Stage names add rendering/OCR/vision work; audit maps review states to `draft_ready`, `needs_review`, `confirmed`. Nonretryable malformed stored audit data returns a controlled `audit_invalid` message with a reprocess route. Profile reads remain independently available.

Deleting a document removes its extraction/audit/page blobs and receipt source quotes, retaining the already approved profile facts as before. Original extraction/profile revision checks, merge/replacement semantics and atomic verification transaction are preserved.

## Frontend

`ResumeAudit.tsx` adds eight accessible native accordion steps inside the existing review panel: quality, page-filtered exact text, private page previews/layout, separate OCR, real task status/models/output, current draft evidence, warnings/conflicting readings, and confirmation status/link. Page images are fetched using the existing authenticated transport and displayed as revocable blob URLs; request cancellation and image URL cleanup happen on page change/unmount. Overlays are optional and normalized to page size; the accompanying text list remains readable without overlays.

The existing editor and confirmation controls follow the audit. No new approval barrier is invented. Existing per-run text consent remains unchecked by default; page images require a second unchecked option. Retry/reprocess clears both. Blue identifies layout, purple OCR, green source-supported evidence, amber manual review, red processing failures. Source support is explicitly distinguished from user verification. Native controls support keyboard navigation and reduced-motion behavior; 390px mobile overflow/preview checks are included. Existing Ascendra branding and Velion-inspired panels remain.

## Validation and evaluation

See [multimodal evaluation](multimodal-evaluation.md) for the 17-document synthetic API/worker evaluation and exact metric definitions. It covers selected text, tables, columns, alternate headings, multiple projects/technologies, missing sections, real OCR and DOCX rendering. It checks every emitted source leaf and evaluates fallback using deliberately unavailable external credentials. Small regression fixtures do not establish arbitrary-resume or live model accuracy.

Final checks are recorded below. Reproduction:

```sh
.venv/bin/python -m pytest
.venv/bin/python -m scripts.evaluate_multimodal
.venv/bin/python -m scripts.evaluate_extraction
.venv/bin/python -m compileall -q app scripts tests
uv pip check --python .venv/bin/python
.venv/bin/python -m app.db.migrate
.venv/bin/python -m scripts.smoke_multimodal --live
.venv/bin/python -m scripts.smoke_resume_crew --live
```

Frontend: `npm run typecheck`, `npm run lint`, `npm run build`, and `npm run test:e2e`; set `E2E_BROWSER=webkit` for Safari-engine coverage and point both `E2E_BASE_URL` and `E2E_API_BASE_URL` at isolated test servers. Live smoke commands transmit built-in synthetic fixtures only, never stored user documents.

## Limits and next milestone

Configured vision models are unavailable in the checked account. OCR/LibreOffice are optional local system integrations; absence is explicit and existing source text remains usable. OCR can misread characters; layout/source associations are conservative, heuristic confidence is uncalibrated, rendering can vary with fonts, and complex nested tables/form objects need a broader labeled evaluation. PDF table detection and multi-column inference are deliberately labeled uncertain. The prototype is local, uses one queue and browser-session identity, and live PostgreSQL remains untested. macOS occasionally emits a multiprocessing resource-tracker semaphore notice during server shutdown; observed worker processes and private scratch directories were removed, and the runtime/evaluation checks pass.

Next work should validate available model configuration and expand human-labeled layout/conflict/OCR evaluation before relaxing conservative source gates. Future matching should consume confirmed profile snapshots. No live job providers, job matching, application automation, cover letters or production auth are introduced here.

References: [Groq vision](https://console.groq.com/docs/vision), [model availability](https://console.groq.com/docs/models), [structured outputs](https://console.groq.com/docs/structured-outputs), [PDFium rendering API](https://pypdfium2.readthedocs.io/en/stable/python_api.html), [LibreOffice command-line conversion](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html), and [Tesseract output formats](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html).

## Final delivery checks

| Check | Result |
| --- | --- |
| Backend | 193 tests passed; framework/parser dependency deprecation warnings remain. |
| Chromium | All 26 browser tests passed. |
| Safari/WebKit | All 26 browser tests passed. |
| Type checking | Passed. |
| Production build | Passed; existing Vite/Nitro notices remain. |
| Frontend lint | Passed, zero errors; ten existing Fast Refresh warnings. |
| Python compilation / installed dependencies | Passed; 146 installed packages compatible. |
| Multimodal evaluation | 17 synthetic documents, 58/58 field oracles, 16/16 project/skill set checks, 19/19 technology-association checks, 8/8 statement checks, 191/191 source leaves supported, zero unsupported values. Mean processing time 1.248s in this run. Controlled no-key fallback: 16/16 requested AI runs; one intentionally unreadable source. |
| Legacy parser evaluation | 104/104 oracles, 178/178 supported leaves, zero unsupported project/technology associations. |
| Database | Private SQLite backup created; initialization run twice; nine tables; all original rows in all seven preexisting tables retained identical fingerprints. |
| Live text CrewAI | Both tasks completed on built-in synthetic text in 10.4s; six source-supported fields, one withheld claim. This checks connectivity, not general accuracy; application merging retains its independent local draft. |
| Live visual CrewAI | Controlled fallback: neither requested Llama 4 model is listed in the account. Actual visual CrewAI execution and multimodal prompts tested with scripted transport. |
| Main application | API health, OCR/audit policy, requested model configuration and 1,000 seeded jobs passed. Actual frontend at port 5173 processed a synthetic scanned resume and displayed its audit on desktop/390px mobile with zero browser errors/overflow; the synthetic source was removed afterward. |
| Credentials | No Groq credential/private-key patterns in changed source, tracked HEADs, fixtures, reports or generated public assets; ignored `.env` remains mode 0600. |
| Diff | Whitespace checks passed; existing independent user changes retained; no history rewrite or deployment. |

The main application remains at `http://localhost:5173/documents`. Previously saved extraction versions require explicit reprocessing to produce new page/OCR audit artifacts; reprocessing does not alter saved profile facts.
