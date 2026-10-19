# Parser quality, evidence, and multi-skill editing

19 October 2026. Local increment extending the existing application. No deployment, Git commit, or push. Original landing/dashboard styling, routes, private upload storage, processing/review workflow, confirmation, rejection, cancellation, retry, and profile revision checks are retained.

## Parser improvements

Text-first extraction remains the default for PDF, DOCX, and TXT. PDF source fragments retain stream order, line boundaries, page numbers, and available text coordinates/font hints. Coordinates are approximate, not reconstructed semantic layout. Possible columns receive uncertainty warnings and maintain column-local section state rather than being concatenated across columns. DOCX parsing preserves body order, paragraph styles, bold/run/tab/list hints, and table rows/cells. Blank/bullet boundaries remain available. Bold text or a short line alone never creates a new section.

Heading matching handles case, numbering, punctuation, ampersands, repeated spaces, and letter-spaced headings. Recognized section boundaries stop the prior section, including a Skills section following Projects. Known additional headings and top-level DOCX section styles can stop unrecognized sections without converting their contents into resume facts.

The dedicated statement extractor reads a complete Summary, Objective, or Profile statement to the next strong heading, preserves wording and line breaks, and records the original heading and `summary`, `objective`, or `profile_statement` type. Objective works when Summary is absent. Multiple statements produce a warning and select the first nonempty statement; absent statements remain empty with an explicit warning. No arbitrary first-page prose is repurposed as a statement.

Projects group title lines, following bullets/paragraphs, title-plus-description, pipe-separated technologies/dates, explicit role/contribution lines, and GitHub/demo links. Technologies attach only to the current project group; job responsibilities/courses do not become projects. An uncertain first bullet title is explicitly marked ambiguous for review. Empty markers such as None/N/A do not create entries.

Experience supports role/employer pairs in either pipe order, explicit `role at employer`, labeled employer/location fields, written date ranges/current markers, responsibilities, achievements, and explicit technologies. Repeated job titles are disambiguated by employer before model suggestions can be associated. Education retains degree/institution, labeled location/specialization/grade, written years/expected graduation, and header-labeled table rows. Institution names alone never create a degree. Unrecognized or incomplete entries remain absent/partial with warnings; dates are never filled from assumptions.

Skills are individual values from dedicated sections, explicit technology lists, clearly named technologies in project/work prose, and explicitly named certification/course technologies. Names retain source spelling while normalizing whitespace, obvious delimiters, and duplicates. A small explicit technology vocabulary recognizes prose mentions; unknown technologies are accepted when explicitly listed, not inferred from job titles or vague work descriptions. Leading dots such as `.NET` are preserved.

## Recognized heading synonyms

- **Profile Summary / Objective**: Summary, Professional Summary, Profile, Profile Summary, Career Summary, Career Objective, Objective, Professional Objective, Job Objective, About Me, Overview, Personal Statement, Executive Summary, Career Profile.
- **Skills**: Skills, Technical Skills, Core Skills, Key Skills, Relevant Skills, Areas of Expertise, Technologies, Tools and Technologies, Programming Skills, Technical Proficiencies, Competencies, Skills and Interests.
- **Education**: Education, Academic Background, Academic Qualifications, Educational Qualification, Qualifications, Degree, Academic History.
- **Experience**: Experience, Work Experience, Professional Experience, Employment History, Internship Experience, Internships, Industrial Training, Experience and Internships.
- **Projects**: Projects, Academic Projects, Personal Projects, Technical Projects, Major Projects, Project Work, Selected Projects, Relevant Projects.
- **Certifications**: Certifications, Certificates, Courses, Training, Professional Certifications, Online Courses.
- **Achievements**: Achievements, Awards, Honors, Accomplishments, Extra-Curricular Achievements.
- **Links**: Links, Profiles, Online Profiles, Portfolio, Social Links, Contact, Contact Information.
- **Preferred Roles**: Preferred Roles, Target Roles.

## Evidence and compatibility

Existing endpoints and `values`/`skills: string[]` outputs remain compatible. Extracted-data responses additionally expose a typed `structured` object with `personal_info`, `profile_statement`, `education`, `skills`, `experience`, `projects`, `certifications`, `achievements`, `links`, `warnings`, and `extraction_metadata`. Every fact has value, source anchor where available, heuristic confidence, method, classification, review status, edit marker, and confirmation state. Missing facts have empty values and explicit not-detected states. Evidence includes original heading/section, block/page/paragraph, line/position when available, and references for multi-line descriptions/statements.

Classifications are directly found, normalized from directly found text, ambiguous/review required, and not found. User edits carry no fabricated source. Saving edits never confirms them. Explicit verification updates only selected sections, records confirmed evidence, and leaves unrelated saved facts intact. Removing/reordering unchanged entries preserves their correct source anchors rather than reusing the former array index's source.

`GET /profile` adds per-skill `skill_details`: name, original text, source, section, evidence, confidence, method, user-edit and user-confirmed state. Profile input accepts strings or objects such as `{"name":"Python","source":"resume","verified":true}`. Client origin/verification claims are ignored; server receipts determine both. Legacy string outputs are retained. Manual skill additions/renames do not acquire resume evidence; existing resume skills retain provenance when other skills are added. New non-text skill-origin markers survive source deletion while source quotes are scrubbed; older receipts retain only their available stored metadata. Dashboard document-derived counts exclude newly user-entered/corrected review skills.

Education/work/project members are additive optional JSON fields. No database column/table change or destructive migration is needed in this increment. Existing verified profiles are not rewritten on GET and no old document is automatically reparsed. Legacy review provenance is labeled as such, rather than claiming it used the current parser.

The explicit **Reprocess with current parser** action creates a new extraction version. It explains that unsaved review edits are not copied and leaves the saved profile unchanged. Groq consent resets for the new run. List replacement still requires the existing explicit replacement choice; reprocessing alone cannot overwrite verified facts.

## Profile and review UI

The existing Profile page now has a visible **Add Skill** control, skill-name input, Add action, Enter-key addition, individual edit/save/cancel and remove controls, duplicate/empty validation, limits, and per-skill manual/resume plus confirmation labels. The compatible bulk editor remains available. Saving/loading/errors use the existing profile workflow; input values survive a failed save and persistence is checked after reload.

Additional structured record details are editable in compact expandable panels. Documents display **Profile Summary / Objective**, original headings/types, source references, normalized/ambiguous/not-detected states, grouped projects and technology lists. Empty additional record fields are collapsed. Existing reduced-motion handling, responsive layout, panel elevation, and pointer behavior remain.

## Groq and OCR decision

The model remains `openai/gpt-oss-120b`; no model was added or substituted. All inputs are extracted text, source blocks, section boundaries, and local entry-group context. A compact strict JSON schema requires the requested top-level sections and fact evidence. The server checks both types and required properties, rejects malformed/incomplete JSON, ignores model confirmation claims, and validates literal source support plus section/entry association. A quote from another project/employer or a different line in the same source paragraph cannot support the current entry. Ambiguous repeated titles cannot freely exchange dates or technologies. Validated enhancement fills missing grouped facts without overwriting original local wording or combining entries.

The expanded schema initially exceeded the configured account's token budget during a synthetic check. The provider schema now omits redundant server-owned metadata and uses a bounded 3,000-token output budget. A subsequent live synthetic-only check accepted four source-supported values with zero withheld leaves. No actual uploaded resumes or personal design documents were sent. Missing keys, provider failures, 413 token-budget responses, invalid/incomplete JSON, timeouts, and rate limits retain the real local draft with clear warnings. Larger provider requests can still exceed service limits; no fabricated replacement is returned. Model uncertainty also produces review warnings.

No vision model/OCR was added. Image-only/scanned pages have no usable text and produce the existing OCR-unavailable explanation, retry/manual-entry alternatives, and no false extracted or verified state. The corpus includes a genuine image-only synthetic PDF. Normal text PDFs and DOCX are never sent to a vision model.

## Validation and evaluation

- Backend: **91 passed**, including all existing 38 tests; two existing dependency deprecation warnings.
- Frontend: **17 browser tests passed** in the final run (41 seconds), retaining the original 15 and adding comprehensive parser/skill workflows.
- Compile, TypeScript, production build, lint, repository whitespace checks, and credential-pattern checks all passed. Lint retains the 10 existing Fast Refresh warnings, with no new errors. Desktop (1440px) and mobile (390px) skill/review screenshots were inspected. The restarted local backend reports a healthy persistent database; the original localhost frontend remains available. Isolated browser-test servers were stopped.
- Complete browser path: landing → upload → process → Objective/project evidence review → edit → confirm → Profile → multi-skill add/edit/remove → save/reload/confirm → Dashboard. It also verifies explicit reprocessing leaves the saved profile intact, failed-save recovery, mobile layout, and reduced motion.
- Provider checks use mocks in automated tests; the separate live smoke check used synthetic text only.

The [reproducible evaluation](extraction-evaluation.md) contains **15 synthetic resumes**, **104 oracle checks: 104 correct, 0 missing, 0 incorrect**, and **178 nonempty extracted-leaf source checks**. Unsupported/hallucinated source values, unsupported project creation, unsupported project-technology associations, and unexpected skills are all **zero in these cases**. These are narrow regression fixtures, not evidence of perfect or representative real-resume accuracy. Oracle units include grouped arrays and missing/empty states; source anchoring alone is not semantic validation.

Fixtures cover one-column students, Objective without Summary, Profile, multiple projects, project technologies/links, table education, multiple work experiences, absent projects, absent statements, duplicate skills, unusual headings, two columns, image-only PDF, missing dates, and mixed bullets/paragraphs. Additional adversarial tests cover cross-entry claims in one paragraph, repeated role/employer names, incomplete provider output, source-removal provenance, and legacy verified data.

Reproduce from the backend:

```sh
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q app scripts
.venv/bin/python -m scripts.evaluate_extraction
```

Frontend checks and isolated browser-test configuration are described in the frontend README. `.env.example` settings are unchanged by this increment; the optional secret stays in the ignored backend `.env`. No frontend provider key was introduced.

## Remaining limitations and next milestone

Parsing and source classification remain heuristic. Unlabeled employers/institutions, deeply nested or decorative tables, bullet-only project boundaries, unusual degree/job names, and nonstandard layouts can be missed or ambiguous. PDF coordinates and column detection are approximate; there is no visual layout reconstruction or OCR. Unknown technologies in ordinary prose are omitted unless explicitly listed. Source matching does not guarantee semantic correctness. The small synthetic corpus is not an accuracy estimate for real resumes or Groq output quality.

Confirmation is section-based; individual entries can be edited/deleted before choosing a section. The local-session identity, single-process worker, PostgreSQL deployment testing, production authentication, durable queue, complete data deletion, and general schema migrations remain unchanged limitations. Job retrieval, matching, application workflows, and CrewAI are not part of this increment.

Recommended next step: evaluate consented, anonymized real resume layouts against human-reviewed field/group labels, then build job retrieval and deterministic matching against explicitly confirmed profile revisions. Add OCR only as a separately scoped, evidence-bearing workflow if those examples justify it.

## Files changed in this increment

Backend:

```text
app/services/resume_text.py
app/services/resume_structure.py
app/services/resume_presenter.py             (new)
app/services/groq_extraction.py
app/services/document_processing.py
app/services/extraction_review.py
app/services/profiles.py
app/schemas/extraction.py
app/schemas/profile.py
app/api/extraction.py
app/api/dashboard.py
tests/test_parser_quality.py                 (new)
tests/fixtures/resumes/                      (new: 15 fixtures, oracle, generator)
scripts/evaluate_extraction.py               (new)
README.md
docs/resume-processing-milestone.md           (historical pointer)
docs/extraction-evaluation.md                (new, generated)
docs/parser-quality-milestone.md             (new)
```

Frontend:

```text
src/components/workspace/SkillEditor.tsx      (new)
src/components/workspace/ResumeDetails.tsx    (new)
src/components/workspace/ProfilePage.tsx
src/components/workspace/DocumentReview.tsx
src/services/api/types.ts
src/services/api/client.ts
tests/e2e/parser-skills.spec.ts                (new)
tests/e2e/document-processing.spec.ts          (configurable direct API test URL)
README.md
```

Primary implementation references: [pypdf text visitors and extraction limitations](https://pypdf.readthedocs.io/en/stable/user/extract-text.html), [Groq structured outputs](https://console.groq.com/docs/structured-outputs). Velion remains a UX reference; no unrelated domain logic was introduced.
