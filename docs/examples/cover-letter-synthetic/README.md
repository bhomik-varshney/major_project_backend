# Synthetic cover-letter validation sample

These outputs use `Student Example`, a synthetic job and a deliberately synthetic test-ink pattern. They are development/QA artifacts, not a real person's signature or an application-ready letter.

- `letter.pdf`: compiled one-page output.
- `preview.png`: rendered PDF page inspected during implementation.
- `letter.tex`: retained source populated with synthetic evidence.
- `structured-content.json`: typed content and claim references.
- `validation.json`: PDF quality checks and compilation summary.
- `signature.png`: synthetic QA ink, not an operator's signature.

For local reproduction, copy `app/templates/cover_letter/fonts/` from the backend root into this directory as `fonts/`, then run the documented XeLaTeX command. Production generation uses an isolated private temporary directory and the operator's approved asset instead of this test image.
