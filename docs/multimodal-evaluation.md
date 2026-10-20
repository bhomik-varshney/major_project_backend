# Multimodal evidence evaluation

18 synthetic documents: TXT, readable PDF, two-column PDF, dense two-page native PDF with fragmented font runs and a hyperlink annotation, DOCX table, blank/unreadable PDF, image-only PDF with real Tesseract OCR, and valid DOCX with LibreOffice rendering. No private resumes or live model calls.

The full API/worker/audit path requests text AI with deliberately empty credentials. Fallback frequency measures this controlled provider-unavailable experiment, **not** a live-provider reliability estimate. Vision task behavior is evaluated separately with actual CrewAI execution and scripted transport in the test suite. These results describe the conservative local evidence pipeline, not model accuracy on arbitrary resumes.

| Metric | Result |
| --- | --- |
| field accuracy | 1.0 |
| project accuracy | 1.0 |
| skill accuracy | 1.0 |
| technology association accuracy | 1.0 |
| statement accuracy | 1.0 |
| evidence support rate | 1.0 |
| unsupported value rate | 0.0 |
| fallback frequency | 1.0 |
| mean processing seconds | 1.006 |

Field accuracy uses literal oracle checks. Project and skill accuracy use exact per-document names/sets, including empty expectations. Technology associations are checked within the named project, including expected absence. Evidence support checks every emitted value against its actual native/OCR block and quote. Confidence is uncalibrated.

| Fixture | Time (s) | Status | Oracle differences |
| --- | --- | --- | --- |
| one_column.txt | 0.572 | awaiting_review | none |
| objective_only.txt | 0.521 | awaiting_review | none |
| profile_section.txt | 0.525 | awaiting_review | none |
| multiple_projects.txt | 0.525 | awaiting_review | none |
| project_technologies.txt | 0.528 | awaiting_review | none |
| work_entries.txt | 0.524 | awaiting_review | none |
| no_projects.txt | 0.612 | awaiting_review | none |
| no_statement.txt | 0.523 | awaiting_review | none |
| duplicate_skills.txt | 0.52 | awaiting_review | none |
| unusual_headings.txt | 0.519 | awaiting_review | none |
| missing_dates.txt | 0.529 | awaiting_review | none |
| mixed_bullets.txt | 0.528 | awaiting_review | none |
| table_education.docx | 4.802 | awaiting_review | none |
| two_column.pdf | 0.934 | awaiting_review | none |
| scanned.pdf | 0.809 | failed | none |
| image_resume.pdf | 1.005 | awaiting_review | none |
| visual_table.docx | 3.38 | awaiting_review | none |
| dense_native.pdf | 0.756 | awaiting_review | none |

Raw counts:

```json
{
  "documents": 18,
  "field_correct": 67,
  "field_total": 67,
  "project_correct": 17,
  "project_total": 17,
  "skills_correct": 17,
  "skills_total": 17,
  "technology_associations_correct": 23,
  "technology_associations_total": 23,
  "statement_correct": 9,
  "statement_total": 9,
  "supported_values": 265,
  "extracted_values": 265,
  "unsupported_values": 0,
  "provider_fallbacks": 17,
  "ai_requested_runs": 17,
  "expected_unreadable": 1,
  "unexpected_failures": 0
}
```

Reproduce: `.venv/bin/python -m scripts.evaluate_multimodal`. Tesseract and LibreOffice are optional runtime integrations but required to reproduce their successful fixture results. Tests simulate their absence/failure. More real-world layout varieties and human-labeled evidence are needed before general accuracy claims.
