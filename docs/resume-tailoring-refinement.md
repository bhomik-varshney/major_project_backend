# Resume Tailoring refinement — 21 October 2026

The supplied `tailored-resume-v1-2.pdf` was matched by SHA-256 to the saved **LLM Application Developer Intern** version. Its confirmed profile and job snapshot were reused. Resume parsing, retrieval, matching, skill-gap analysis, cover letters and tracking were not rebuilt. Private resume content stayed local.

## Findings and corrections

| Finding | Correction |
| --- | --- |
| Fixed “bringing technologies to … work” summary | Two source-backed sentences: academic identity, selected technical domain, two relevant capabilities, and an actual project action. Unsupported summary edits remain rejected. |
| Fit could discard the 96.6% outcome | Measurable results are required entry evidence. A complete implementation clause and its outcome can be combined from the same description. Cross-employer facts remain prohibited. |
| Adobe bullet emphasised a certificate | Implementation responsibilities outrank certificate wording. The React Native LMS contribution is retained with organisation, role, period and remote location. |
| Generic project/header link labels | Actual domains/paths and classified platform labels are visible and hyperlinked. Long repository paths use the real project name and host; the full exact target remains in the annotation and review. |
| Crowded links and duplicate aliases | Repository/demo receive priority. One extra video link stays available in review and the ledger, with an explicit PDF omission reason. React/version and MCP/expanded-name aliases are consolidated for display. |
| Date/location wrapping and collision risk | Fixed right-aligned date/location cells, wrapping organisation cells, consistent bullet indentation, navy headings and rules. |
| Unexpected full-width row wrapping | The supplied class loads `parskip[parfill]`, which imposes a 30pt minimum paragraph-ending skip. The dynamic template neutralises that minimum. The supplied class and original template archive remain unchanged. |
| Ligatures and kerning damaged extraction | Common ligatures and kerning disabled in the generated template. PDF QA rejects presentation ligature characters and checks readable labels, exact hyperlink targets and date alignment. |

Projects show concise factual actions, a small associated technology emphasis and their correct destinations. The complete technology mapping remains in review. Skills are grouped into programming, agentic AI/LLMs, AI/ML/NLP, backend/APIs, frontend/mobile, cloud/DevOps and tools; Langfuse is no longer grouped as an ML framework. Missing job requirements never become candidate skills.

## Before/after on the supplied document

| Check | Supplied PDF | Corrected version |
| --- | --- | --- |
| Confirmed experience/internships | 2 | 2, including Adobe |
| Major projects | 4 | 4 |
| Skill inventory | 36 labels, with repeated aliases | All 36 represented by 34 distinct display labels |
| Microsoft/SAP/AICTE result | 96.6% present, vulnerable to fit selection | 96.6% explicitly required and retained with implementation context |
| Summary | Mechanical template wording | Job-specific identity and supported project action |
| Header/project links | Generic labels | Readable destinations and exact hyperlinks |
| Text extraction | `traﬀic`, plus artificial word spacing | Normal `traffic`; no common presentation ligatures |
| Output | One page | One page, 11pt body, embedded fonts, selectable text |

The primary education entry and one relevant achievement are retained. Two earlier schooling entries, three optional achievements and additional source detail remain in the ledger with omission reasons. The extra video destination is available in review; the PDF prioritises repository and demo. No internships, employment entries or major projects were deleted.

## Review, versions and preservation

The review workspace now exposes changed bullets, corrected/omitted PDF links, skill consolidation, preserved entries and PDF extraction/alignment checks. Technical evidence stays in the expandable audit. Content policy version 3 requires older unapproved drafts to be regenerated before approval.

A separate **version 2** for the supplied document's application is saved as `needs_review`; it is neither approved nor attached. Original profile fields, stored document bytes, the supplied downloaded PDF and all previous resume versions were preserved. A private database backup preceded generation. Only the selected application's revision/timestamp, one new tailored version and six audit events changed. See [preservation evidence](resume-tailoring-refinement-preservation.json) and [anonymised real-document regression](resume-tailoring-refinement-regression.json).

The existing approval digest binds the exact PDF, candidate/job snapshots and version. Explicit approval is still required before attachment to the same application. Replacement, ownership, stale-profile and tamper checks remain active.

## Verification

- **53 distinct backend tailoring cases passed**: original suite 28; preservation/refinement suites 24; an additional cross-field provenance regression 1. These cover source grounding, metrics, Adobe/all-history preservation, skill inventory, classified URLs, escaping, ligatures, one-page rendering, overflow failure, editing and source preservation.
- **4 existing application/cover-letter integration cases passed**, including the reviewed MCP packet, job/hash binding, profile immutability, cross-application rejection and tampered PDF rejection.
- **8 Chromium browser cases passed**, including comparison links/bullets, PDF preview/download, source-only TeX handling, edits/regeneration, unsupported summaries, fallback, cancellation, mobile layout, approval and correct attachment.
- Type checking and production build passed. Lint passed with **0 errors and 10 pre-existing Fast Refresh warnings**.
- The real confirmed profile was rendered locally through the existing tailoring worker. Its corrected PDF passed on the second bounded compile attempt: exactly one page, 11pt body, embedded fonts, selectable text, all protected entries, expected headings/order, safe exact links, readable labels, three right-aligned date fields, no graphics, no ligature characters, no clipping/overfull warnings and no detected text overlap. Its page preview was visually inspected, including a high-resolution header/experience crop. An additional render check found no empty ink boxes across 2,027 alphabetic glyphs.
- The running main API and frontend received health/page smoke checks after refresh.

Commands: `python -m pytest tests/test_resume_tailoring.py tests/test_resume_tailoring_preservation.py tests/test_resume_tailoring_refinement.py`; scoped application integration selection; `npm run test:e2e -- tests/e2e/resume-tailoring.spec.ts`; `npm run typecheck`; `npm run lint`; `npm run build`.

## Limits

ATS checks use PDF text extraction, hyperlink inspection, compiler warnings and glyph/margin heuristics; they do not certify every commercial ATS. Date columns use small text tables, while the body remains a single logical flow. Oversized confirmed profiles fail clearly after three attempts rather than removing protected history or shrinking the body font. Novel user wording must be supported by the existing source-variant policy. A very long URL may display its project name and real host while retaining the complete hyperlink. The real resume was not sent to Groq; CrewAI schema/host correction and fallback were tested with synthetic data. No LibreOffice application or GUI conversion was used.
