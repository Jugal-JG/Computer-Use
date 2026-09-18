# Local verification evidence

The newest matching-artifact evidence is in [submission-v4](submission-v4/README.md): a real Gemini discovery, a successful zero-model replay, and a zero-model not-found replay all pin the same artifact hash. [dynamic-v3](dynamic-v3/README.md) remains a separate real Gemini discovery/replay validation with different replay inputs. The sections below document the older catalog-guided implementation and its original evidence; they are retained as historical results, not current discovery validation.

Validated locally on Windows 11 with Python 3.12.14, Tesseract 5.4.0 (English), and Playwright 1.62.0 / Chromium 151. The dependency snapshot is `requirements.lock`. No Git commit, push, deployment, or email was performed.

## Genuine discovery

- Model: **Gemini 3.5 Flash-Lite**, as selected by the user.
- API: Gemini Interactions, schema-constrained JSON, low thinking effort, `store:false`.
- Result: **DISCOVERY_COMPLETE**.
- **7 model-selected UI actions; 8 model calls; no human assistance**.
- Input: synthetic member 10001, month 2026-08, postal delivery.
- Saved artifact: `capability/statement-request-v1.json`.
- Evidence: `discovery/events.jsonl`, `discovery/result.json`, and privacy-filtered `discovery/final.png`.
- SHA256: `503b6aeed02ad342eaf480d9caaf360515e859a679735a257a5433423669fac3`.

The successful run is genuine. The model chose the ordered flow from current screenshots and visible candidates. Targets/error handlers come from the reviewed profile; the compiler constructs field checkpoints. This is not arbitrary-app autonomous onboarding. Earlier development attempts were not substituted for this success.

## Replay of that exact artifact

Inputs changed to synthetic member **10002**, month **2026-07**, electronic delivery. Each case used **zero LLM calls**.

| Directory | Observed result |
|---|---|
| `replay/normal` | Success, REVIEW_READY; masked account ****0002 |
| `replay/not_found` | Business outcome, MEMBER_NOT_FOUND |
| `replay/validation` | Business outcome, VALIDATION_REJECTED |
| `replay/permission` | Business outcome, ACCESS_DENIED |
| `replay/slow` | Bounded wait followed by success |
| `replay/notice` | One dismissal followed by success |
| `replay/app_error` | Controlled failure, APP_UNAVAILABLE |

These are real local browser/OCR executions, not mocked surfaces. The harness configures each proxy independently; the runner sees only the UI.

## Actual human handoff

The user operated the local panel during the unknown-dialog scenario. Logs show control acquisition, the acknowledgement click, checkpoint verification, and successful continuation. Session ID remained `dc1138db9ee847149319324a463e829c` throughout. Result: **REVIEW_READY**, **zero LLM calls**.

This handoff used the separately included authored fixture `human_handoff/artifact.json`, not the discovered artifact. The distinction is deliberate and visible in provenance. `human_handoff/events.jsonl` is an actual human test; automated protocol tests separately simulate operator commands and label their ownership event `simulated_test_operator`.

The accompanying screen recording, [`Test_Run.mp4`](Test_Run.mp4), shows the real unknown-dialog handoff: automation pauses, the operator takes control of the existing live session, acknowledges the dialog, resumes automation, and reaches the review screen.

## Automated checks

Final full suite: **27 passed in 59.85 seconds**.

After tightening local-origin and synthetic-banner checks, all **16 focused contract/provider/vision/discovery tests** also passed. The final setup check launches and closes Chromium cleanly.

Command: `python -m pytest -q -p no:cacheprovider`.

Coverage includes real-browser scenario runs; same-session handoff; stale control and invalid resume rejection; endpoint authorization; browser-loss reporting; provider 429/503 retry budgets; current provider response parsing; stale proposal rejection before dispatch; artifact schemas; field-checkpoint compilation; visual ambiguity; low-confidence rejection; policy boundaries; and log redaction.

The final suite includes a deliberately delayed notice dismissal. Recovery waits for disappearance rather than retrying the click. Windows was executed locally; the included Linux CI workflow has not been run on a remote CI service.

## Evidence handling

All application records are synthetic. Export includes only reviewed JSON/JSONL and privacy-filtered images; no credentials, raw prompts/responses, SQLite files, or unredacted debug screenshots. Exported result paths are relocated for portability. `manifest.json` contains file hashes. Local exploratory runs remain under ignored `.runs/`.

Run `python scripts/export_evidence.py` after the documented local source runs to rebuild this bundle. The runner restricts targets to loopback HTTP origins, and discovery requires the synthetic-data banner. These checks do not constitute general-purpose PII detection; only fictional records belong in this demo.
