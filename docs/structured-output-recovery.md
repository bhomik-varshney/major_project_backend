# Structured output recovery and completed-stage preservation

19 October 2026. Follow-up to [the model and input-budget fix](provider-model-token-fix.md).

The reported run had completed vision and text extraction, then failed the dependent AI quality task. The implementation discarded the completed extraction when any later task failed, and the audit labeled the entire text stage failed. The quality task had a 600-token allowance despite accepting up to 200 issues. A subsequent replay also exposed intermittent extraction output failures. The earlier small synthetic checks did not cover this resume's behavior.

The user explicitly approved sending the saved failed resume text and layout to Groq for diagnosis. No original page images were resent during this diagnostic. Only stage names, counts, provider codes, validation paths and token counters were recorded. One successful extraction consumed 3,254 completion tokens, including 1,098 reasoning tokens; the old 2,500-token allowance could not cover that response. Hiding reasoning does not remove it from the completion allowance. A schema-validation error alone does not prove truncation, so the transport no longer makes that claim.

## Changes

- Default/configured extraction allowance is 3,500 tokens. Source-preserving adaptive splitting now uses each failed batch's actual size, with two split levels, a shared deadline and a total attempt limit. It handles input-limit, invalid-output and confirmed truncation failures. This changes the failing payload instead of replaying an oversized request across credentials.
- The compact AI quality contract permits at most eight priority issues and three warnings. It starts with 1,600 tokens and permits one changed, shorter quality request with 3,200 tokens after invalid/truncated output. This never replays completed extraction. The deterministic validator still checks all draft fields.
- Completed typed extraction is checkpointed from the real CrewAI task into the parent process. A later quality exception, process exit or hard timeout returns that extraction with an explicit quality failure. Source/section/entry grounding still runs before any suggestion enters the draft. Cancellation still stops publication.
- Groq responses with `finish_reason=length` raise `groq_output_truncated`. Other structured/schema failures retain `groq_invalid_output` without an unsupported token-budget diagnosis. Provider bodies are not exposed.
- The audit and run metadata distinguish completed extraction from failed/partial AI quality. Incomplete source batches are reported as partial. The frontend explains that completed extraction is retained and requires manual review.
- A dense two-page synthetic PDF exposed a separate vision completion cap. Vision now requests at most eight short structural groups with a 3,000-token allowance, and retries a truncated page once with at most six shorter groups and 3,500 tokens. The original shared deadline still applies. Requested vision failure/partial completion makes the aggregate run partial rather than reporting the entire run ready.

## Verification

The previously failing saved resume was reprocessed through the real frontend using text consent only. The new run reached `awaiting_review` / `draft_ready`, with extraction, AI quality and deterministic source validation completed, and zero browser errors. The earlier failed run remains historical. Source checks can withhold AI suggestions; completion does not imply all model claims are accepted or that extraction is universally accurate.

The dense synthetic PDF contained 74 lines across two pages. After the vision adjustment, its real frontend run completed vision, extraction, AI quality and deterministic source validation with zero browser errors. Its test upload was deleted afterward. No profile was confirmed during either live check.

Regression coverage includes checkpoint preservation after quality errors and hard timeouts, bounded quality-only retry, source grounding of retained claims, truthful API partial statuses, distinction between truncation and schema failure, bounded changed-payload vision retry, and prevention of a failed requested vision stage reporting overall success.

References: [Groq reasoning](https://console.groq.com/docs/reasoning), [chat completion parameters](https://console.groq.com/docs/api-reference), [structured outputs](https://console.groq.com/docs/structured-outputs).

Final automated checks: 212 backend tests passed in the full suite, and the subsequently added real-CrewAI checkpoint regression passed separately. All 26 Chromium browser cases passed (two direct-API cases were rerun after correcting the test API base to the isolated server). Type checking, production build and lint passed; lint retains ten existing Fast Refresh warnings. An existing subprocess test now allows three seconds for macOS interpreter startup instead of one; its production timeout is unchanged. Confirmed profile facts, profile revisions and verification receipt IDs matched their pre-test fingerprint. The temporary test servers and diagnostic signed session were removed; the main frontend/API remain on ports 5173/8000.
