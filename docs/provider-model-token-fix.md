# Vision model and text request budget fix

19 October 2026. Follow-up to the multimodal resume milestone.

**Superseded response-budget details:** see [structured-output recovery](structured-output-recovery.md). The original small synthetic checks below missed a later failure on the saved resume; the quality allowance is now 1,600 tokens with a bounded retry, and completed extraction survives later task failure.

The frontend screenshot showed two independent provider failures: neither configured Llama 4 vision model was available, and the text model rejected an oversized request (`groq_input_limit`). Both are fixed for new processing runs. Previously saved failed audits retain their historical result; use **Reprocess with current parser**, select text/image consent for the new run, and start extraction.

## Root causes

The authenticated Groq model list includes `qwen/qwen3.8-27b` and `openai/gpt-oss-120b`, but not the configured Maverick/Scout models. The primary vision configuration now uses Qwen; the optional fallback is blank because no second available vision model was verified. Both model names remain backend environment settings and availability is checked at execution.

A failed run contained 105 source blocks and only 4,568 text characters, but block/font/position data plus duplicated local layout expanded the prepared input to 81,022 JSON characters. The old response schema repeated value/evidence/confidence/review metadata for every possible field, including absent fields, and the validation task inherited that verbose output. This consumed provider input/output budgets unnecessarily. The new prepared source payload for the same saved blocks is 8,701 characters, an 89.3% reduction; the complete original evidence remains in the private audit.

## Changes

- `services/crew_payload.py` sends source text/block IDs, section references and source-supported visual relationships. It excludes repeated native layout text, font data, boxes and unused metadata from text-model prompts.
- Real CrewAI extraction now uses a compact internal `ResumeClaims` contract: `path`, `value`, `block_id`, `confidence`. The server locates the original literal quote in the cited block, reconstructs the existing typed proposal, and applies the same source/section/entry association gates. Unsupported values still fail validation; model text never writes the verified profile. The external frontend/profile API is unchanged.
- The dependent real CrewAI validation task receives compact claims, with a smaller 600-token output reservation. GPT-OSS uses low reasoning effort; Qwen vision uses instruct mode (`reasoning_effort=none`). This avoids spending the structured response budget on unnecessary reasoning.
- Source text is split into bounded batches (default 9,000 characters including block overhead, up to eight initial batches) without dropping text or changing block IDs. Long paragraphs can span batches. Original section context is retained. Input-limit/invalid-structured-output failures allow one smaller-payload split, rather than replaying the same failing request across keys. The entire text workflow shares one hard deadline; partial AI completion is explicitly warned and the independent local draft/source audit is retained.
- Vision submits one page image per request, avoiding a three-image request consuming 6,144 image tokens before instructions/schema/output. Per-page results retain a common deadline and unique namespaced IDs, preserve completed pages if a later page fails, and expose completed page numbers in audit results.
- Compact tasks can wait once for credential cooldown/quota recovery, with at most one additional request. Waiting is bounded by the remaining task deadline and shown in the frontend. Invalid credentials and malformed application requests remain distinct; an HTTP input-limit error never blindly rotates keys. Organization quotas can be shared across keys, so key rotation does not increase the quota.
- Existing draft editing, confirmation, correction, rejection, cancellation, retry and profile protections are preserved. The main API was restarted with the working configuration.

Current backend configuration:

```dotenv
GROQ_TEXT_MODEL=openai/gpt-oss-120b
GROQ_VISION_MODEL_PRIMARY=qwen/qwen3.8-27b
GROQ_VISION_MODEL_FALLBACK=
GROQ_SOURCE_BATCH_CHARS=9000
GROQ_MAX_BATCHES=8
```

## Validation

- Real synthetic vision smoke: Qwen completed and returned eight layout blocks.
- Real synthetic text smoke: both CrewAI tasks completed, eight supported fields, two skills, zero withheld fields, 7.3 seconds.
- Larger synthetic resume: 4,590 source characters, three projects; both text tasks completed in 7.2 seconds with 33 supported fields, all three projects and zero withheld claims. One source batch, no warnings. No uploaded user document was sent during these smoke checks.
- Actual frontend at `http://localhost:5173/documents`, with both consent choices selected on a synthetic scanned resume: audit reached `draft_ready`; vision, extraction, validation and deterministic source validation all reported **completed**; zero browser errors. The synthetic source was deleted afterward and no profile was confirmed.
- Eleven new regression cases cover payload reduction, full source/ID preservation, sparse/invalid claim paths, real compact CrewAI execution, changed-payload recovery, bounded cooldown waiting, reasoning/output parameters, one-image requests, page ID namespaces, server-resolved original quotes and unsupported-value withholding. Existing provider, parser, confirmation and multimodal tests remain in the full suite.

Final checks: **204 backend tests passed**. Type checking, production build and frontend lint passed (ten preexisting Fast Refresh warnings). The real main-frontend synthetic test completed every agent stage with zero browser errors. Dependency compatibility (146 packages), Python compilation, whitespace checks and credential scans passed. No credentials appear in source, reports or public frontend assets; configuration remains in ignored backend `.env`.

References: [Groq vision models and image budgets](https://console.groq.com/docs/vision), [Qwen model/mode settings](https://console.groq.com/docs/model/qwen/qwen3.8-27b), and [organization rate limits](https://console.groq.com/docs/rate-limits). Provider availability and quotas can change; these checks establish current connectivity and regression behavior, not general resume extraction accuracy.
