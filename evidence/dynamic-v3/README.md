# Dynamic discovery validation

Real Gemini run on the local synthetic application, followed by replay of that exact artifact with different inputs. These files were copied unchanged from `.runs/dynamic-validation-3`; `evidence_dir` in the results retains the original runtime location.

- Artifact: `capability.json`, schema 3.0; 7 verified steps.
- Discovery: `DISCOVERY_COMPLETE`, 10 model calls, no human intervention.
- Replay: `REVIEW_READY`, zero model calls; synthetic member 10002, month 2026-07, electronic delivery, masked account ****0002.
- Both results pin artifact hash `c7fa2484ae86a3ae71323bfa3b318c1291e5f500d589e7ca1e9fc1d3c0037e66`.

The model received screenshots, OCR observations and pixel-detected field rectangles. It received no authored button catalog, ordered steps or list of supported headings. It proposed action and output locators; the executor grounded each in current pixels and enforced confidence thresholds. Click checkpoints came from subsequent observations.

The scripted renamed-UI integration test is separate: it verifies dynamic compilation and replay with unfamiliar control/heading labels, but is not evidence of model reasoning. Older evidence directories retain the earlier implementation's results and must not be presented as validation of dynamic discovery.

## Automated checks

All 78 collected test cases were verified across the broad suite and targeted follow-ups. The broad run passed 51 cases before the headed native-window resize case returned MEMBER_NOT_FOUND once. That case passed in isolation together with the 25 remaining cases (26 passed); its intermittent failure is retained here rather than described as a clean full-suite pass. Final compiler/provider/renamed-UI checks passed 15 cases, and both stale-proposal and new post-navigation handoff tests passed. The field/value reader tests exercise all four directions using real OCR.

Ruff checks passed. `scripts/check_export.py` found no configured API key in exported files and verified matching artifact hashes for discovery and replay. The checked-in JSON schema matches the runtime artifact schema.

Reproduce with a new output directory:

```powershell
.\.venv\Scripts\python.exe scripts/validate_live_dynamic.py --output .runs/new-dynamic-validation
```
