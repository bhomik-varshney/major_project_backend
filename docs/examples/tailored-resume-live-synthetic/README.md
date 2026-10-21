# Live synthetic tailored-resume example

These files use a synthetic student and seeded job. They are QA artifacts, not a real user's application-ready resume.

The actual three-agent Groq/CrewAI workflow selected evidence and content; host checks validated immutable facts and recorded any bounded correction. XeLaTeX compiled the supplied `resume.cls` design to exactly one page. The PDF was reviewed/approved/attached in an isolated test workspace, with the original profile and original document preserved and the application left unsubmitted.

- `resume.pdf`: validated one-page PDF.
- `preview.png`: rendered PDF preview.
- `structured-draft.json`: exact draft used for rendering.
- `resume.tex` and `resume.cls`: generated source and supplied class.

Run the documented XeLaTeX command against `resume.tex` with the listed dependencies installed to reproduce the source layout. See `../../resume-tailoring-live-api-smoke.json` for evidence, validation, agent outputs and any correction.
