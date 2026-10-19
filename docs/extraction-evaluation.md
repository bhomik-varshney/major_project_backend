# Resume extraction evaluation

15 synthetic fixtures; deterministic local extraction only. This is a regression evaluation, not a representative resume-accuracy benchmark. No personal data or provider calls.

| Metric | Count |
| --- | --- |
| resumes | 15 |
| fields evaluated | 104 |
| correct | 104 |
| missing | 0 |
| incorrect | 0 |
| extracted leaves checked | 178 |
| unsupported leaves | 0 |
| unsupported projects | 0 |
| unsupported technology associations | 0 |
| unexpected skills | 0 |

Fields evaluated are explicit oracle checks (including grouped project names/technology lists, skill sets, empty states, and scanned-file rejection); every nonempty extracted leaf is separately checked against real source quotes. Project existence and per-project technologies are checked against entry-specific expected sets. Lexical anchoring alone does not prove semantic correctness.

| Fixture | Correct | Missing | Incorrect |
| --- | --- | --- | --- |
| one_column.txt | 13 | 0 | 0 |
| objective_only.txt | 3 | 0 | 0 |
| profile_section.txt | 7 | 0 | 0 |
| multiple_projects.txt | 9 | 0 | 0 |
| project_technologies.txt | 10 | 0 | 0 |
| work_entries.txt | 11 | 0 | 0 |
| no_projects.txt | 5 | 0 | 0 |
| no_statement.txt | 5 | 0 | 0 |
| duplicate_skills.txt | 2 | 0 | 0 |
| unusual_headings.txt | 6 | 0 | 0 |
| missing_dates.txt | 10 | 0 | 0 |
| mixed_bullets.txt | 8 | 0 | 0 |
| table_education.docx | 8 | 0 | 0 |
| two_column.pdf | 6 | 0 | 0 |
| scanned.pdf | 1 | 0 | 0 |

Common limitations: unconventional unlabeled employer/institution layouts can be missed; unknown technologies in prose are deliberately omitted unless explicitly listed; ambiguous columns retain uncertainty; no OCR; arbitrary visual tables and nested project layouts need more evaluation. Title grouping is heuristic. Confidence is not calibrated. No perfect-accuracy claim is made.

Improvements exercised: heading aliases/numbering/punctuation/letter spacing; complete Objective/Profile statements; project bullet grouping and technology isolation; multiple work entries and explicit dates; table education; absence handling; skill deduplication; page/paragraph/position anchors.

Reproduce with `python -m scripts.evaluate_extraction`. Regenerate synthetic source fixtures with `python tests/fixtures/resumes/build_fixtures.py`.

scanned.pdf: no_readable_text
