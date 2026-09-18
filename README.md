# Visual capability: discover once, replay without an LLM

A local, visual-only computer-use implementation for a fictional legacy member-services application. The target uses table layouts and an iframe. The runner reads screenshots with Tesseract and OpenCV, sends mouse/keyboard input with Playwright, and never reads the target DOM, accessibility tree, or application database.

The workflow searches for a member, opens savings, prepares a statement request, and verifies the review screen. It never submits the request.

## Setup (Windows PowerShell)

Python 3.11+ (tested with 3.12), Tesseract 5 with English language data, and Chromium are required. Tesseract is a separate executable; `pytesseract` is its Python wrapper.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item .env.example .env  # only if .env does not already exist
.\.venv\Scripts\python.exe -m visual_agent.cli doctor
```

Set `GEMINI_API_KEY` in `.env` for discovery. Never put the key in a command argument or commit `.env`. `GEMINI_MODEL` defaults to `gemini-3.5-flash-lite`. `TESSERACT_CMD` can override the executable location. Existing standard Windows installation paths are detected automatically. The runner uses the Gemini Interactions REST API through httpx with `store:false`, schema-constrained responses, and low thinking effort; no agent framework is required. This disables interaction storage, not the provider's general data-use terms.

On Linux/macOS, use `.venv/bin/python`, install Tesseract via the OS package manager, and install Playwright's required OS libraries. The automated CI workflow targets Linux. Local validation results and their actual environment are recorded in `evidence/README.md`.

## Exact demo: genuine discovery, replay, and a failure case

The following are single-line Windows Command Prompt (`cmd.exe`) commands. Run the two terminals from the repository root. Discovery requires `GEMINI_API_KEY` in `.env`; replay does not. `--headed --window-following` opens a visible browser that follows manual window resizing; `--keep-open` leaves its final screen open until you press Enter in terminal 2.

### 1. Start the normal synthetic application (terminal 1)

```cmd
.venv\Scripts\python.exe -m visual_agent.proxy --scenario normal
```

Leave this terminal running.

### 2. Discover an artifact (terminal 2)

```cmd
.venv\Scripts\python.exe -m visual_agent.cli discover --headed --window-following --viewport-width 1280 --viewport-height 800 --keep-open --goal "Find the specified member, prepare a savings statement request using the supplied inputs, and stop at the review page without submitting." --inputs "{\"member_id\":\"10001\",\"statement_month\":\"2026-08\",\"delivery_method\":\"postal\"}" --output .runs\discovery-artifact-v3.json --evidence .runs\discovery
```

The command prints `DISCOVERY_COMPLETE` and writes `.runs/discovery-artifact-v3.json`. Do not reuse an existing output path; discovery intentionally refuses to overwrite artifacts.

### 3. Replay the saved artifact with different inputs (terminal 2)

```cmd
.venv\Scripts\python.exe -m visual_agent.cli replay --headed --window-following --viewport-width 1280 --viewport-height 800 --keep-open --artifact .runs\discovery-artifact-v3.json --inputs "{\"member_id\":\"10002\",\"statement_month\":\"2026-07\",\"delivery_method\":\"electronic\"}" --evidence .runs\replay
```

Expected result: `success / REVIEW_READY`. Replay loads the artifact and makes no model calls.

### 4. Demonstrate a recognized failure/business outcome

In terminal 1, stop the normal proxy with `Ctrl+C`, then start the not-found scenario:

```cmd
.venv\Scripts\python.exe -m visual_agent.proxy --scenario not_found
```

In terminal 2, replay the same artifact:

```cmd
.venv\Scripts\python.exe -m visual_agent.cli replay --headed --window-following --viewport-width 1280 --viewport-height 800 --keep-open --artifact .runs\discovery-artifact-v3.json --inputs "{\"member_id\":\"10001\",\"statement_month\":\"2026-08\",\"delivery_method\":\"postal\"}" --evidence .runs\replay-not-found
```

Expected result: `business_outcome / MEMBER_NOT_FOUND`. This is an expected caller-visible result, not an automation crash.

Add `--headed` to see Chromium directly. `--viewport-width`, `--viewport-height`, and `--device-scale-factor` select the browser geometry. Targets use normalized regions and current-screen relationships, so each action is resolved again after a resize. By default, a local operator panel displays the same live page through screenshots. Each run prints its panel URL containing a short-lived access token in the URL fragment. Open that exact URL; the token is not saved in evidence. `--operator-port 0` chooses a free port. Do not run two commands against the same fixed operator port.

Discovery will not overwrite an existing artifact: choose a new output path/version for another recording. Each new run begins in a new isolated browser context; **handoff within a run preserves that context and page**.

For mouse-driven resizing, add `--headed --window-following`. The page viewport then follows the native browser window; `--viewport-width` and `--viewport-height` set its initial outer size, whose visible page area is smaller because of the browser toolbar and frame. This mode uses native window scaling and requires the default `--device-scale-factor 1`. Without `--window-following`, the viewport remains fixed for reproducible tests.

Use `--step-delay-ms 1000` to allow time to watch or resize between actions, and `--keep-open` to leave the browser visible at the end until you press Enter in the terminal. Both discovery and replay accept these flags. The delay defaults to zero and is not saved in the artifact.

Screenshots use CSS pixels, leave animations and the text cursor untouched, and serialize captures. Identical captures reuse OCR results; recent verified postconditions can satisfy the next precondition, while a fresh screenshot still grounds each action. The operator panel reuses captures up to 750 ms old and loads replacement images before displaying them. Observation events record capture/perception timing and `ocr_reused` so performance can be measured.

The compiler builds field-value checkpoints from a model action and input reference. A click's next checkpoint is taken from the model's following observation and verified against that screenshot, rather than guessed before navigation.

Discovery starts with an empty action-target catalog and no steps or screen vocabulary. Gemini sees the current screenshot and OCR observations, proposes a visible label/control relationship and optional normalized geometry, and chooses the action and input parameter. The executor validates the proposal against fresh pixels before acting. Ambiguous targets return feedback to the model. The final observation also supplies output locators. Saved schema-v3 artifacts contain these learned locators, observed checkpoints, and verified steps; replay loads only the artifact and makes no model calls. The business input/output contract and known error handlers remain reviewed configuration for this synthetic statement task. The authored fixture is used only for offline testing, never to seed discovery.

Input verification and output extraction share `View.read_value(target)` and `read_box(box)`. They resolve the field or displayed value on the current screenshot, then OCR that box. Relationships can describe right, left, above, or below; there are no direction-specific value-reading methods. Input contents are read inside the field border, while displayed values use text regions identified through OCR and pixel contours. Stored geometry is a hint, not a stale click coordinate.

For an isolated real-model discovery and replay check, run `python scripts/validate_live_dynamic.py --output .runs/dynamic-validation`. It starts and stops its own synthetic application, refuses an existing output directory, and replays with different inputs. It uses the configured Gemini key. `tests/test_dynamic_integration.py` separately exercises renamed controls and headings using an explicitly scripted test provider; that test is not model-discovery evidence.

## Run with no model services

Replay has no model client and does not require a key. A clearly labelled, human-authored fixture is available to test the engine before model access works:

```cmd
.venv\Scripts\python.exe -m visual_agent.cli fixture --output .runs\authored-fixture.json
.venv\Scripts\python.exe -m visual_agent.cli replay --headed --artifact .runs\authored-fixture.json --inputs "{\"member_id\":\"10002\",\"statement_month\":\"2026-07\",\"delivery_method\":\"electronic\"}"
```

This fixture is **not evidence of LLM discovery**. Inspect the artifact's `provenance` to distinguish it from a genuine recording. Provider mocks are likewise only tests.

## Failure scenarios and human handoff

Stop the proxy with Ctrl+C and restart with `--scenario` set to:

| Scenario             | Expected response                                                   |
| -------------------- | ------------------------------------------------------------------- |
| `normal`           | `success / REVIEW_READY`                                          |
| `not_found`        | `business_outcome / MEMBER_NOT_FOUND`                             |
| `validation`       | `business_outcome / VALIDATION_REJECTED`                          |
| `permission`       | `business_outcome / ACCESS_DENIED`                                |
| `slow`             | Bounded visual wait, then success                                   |
| `notice`           | One policy-checked dismissal, then success                          |
| `app_error`        | `failure / APP_UNAVAILABLE`                                       |
| `session_expired`  | Same-session human restore, checkpoint verification, continuation   |
| `unknown_dialog`   | Generic visual modal detection, human acknowledgement, continuation |
| `oversized_dialog` | Near-full-screen modal detection and the same safe handoff          |

For unknown dialogs, no handler contains their message text. OpenCV identifies a scale-independent foreground panel over a dimmed viewport and restricts OCR to that layer. Suspicious darkening without a trustworthy panel becomes `OCCLUDED_OR_UNKNOWN_LAYER`; background text cannot satisfy handlers or checkpoints. Unknown non-modal states fail their checkpoint and request intervention after a bounded wait.

To exercise handoff:

1. Run replay against `unknown_dialog` or `session_expired`.
2. Open the printed operator URL.
3. Select **Take control**. Automation stops dispatching input.
4. Click the displayed acknowledgement/restore button in the live image.
5. Select **Resume automation**. The expected checkpoint must be present and the modal absent.

The panel forwards actions to the existing target page and records sanitized human actions. It does not automate the target DOM. The operator can replace a visually verified field, scroll, resume, or abort. Risky submission remains blocked even during handoff. If the panel disconnects, execution stays paused; default intervention expiry is 300 seconds. Use `--intervention-timeout` to change it.

Browser crashes return `SESSION_LOST`; SQLite checkpoints are not a claim of browser crash recovery.

Run all seven automatic scenarios against any saved artifact without an API key:

```powershell
.\.venv\Scripts\python.exe -m visual_agent.cli demo-suite --artifact .runs/discovered-v2.json --evidence .runs/my-suite
```

The suite starts its own isolated local proxy instances and prints a structured pass/fail summary. It never tells the automation which scenario is active. Handoff is exercised separately so it can remain a real manual action rather than silently simulating a person.

## Policy and privacy

`--policy config/policy.json` loads the explicit allowlist. The policy restricts origin, port, paths, HTTP method, action types, targets, and operator keys. Final submission and external navigation are blocked. Playwright is confined to one module; it is used for lifecycle, screenshots, network restrictions, and input only.

This demo enforces a loopback HTTP origin with an explicit port. Discovery also checks the synthetic-data banner before sending any screenshot to the model. These checks constrain the supported target; they do not replace data classification for a production deployment.

Use only the synthetic local application. Discovery transmits screenshots to the configured provider. The demo does not implement regulated-data classification for arbitrary real banking screens and must not be used with real records. Model free-tier availability depends on the provider account and can change; no paid/provider fallback is automatic.

Persistent screenshots are masked by default and expose only allowlisted static UI text. Raw prompts, provider response bodies, input values, and credentials are not written to logs. Output JSON contains the explicitly declared, masked business outputs. Synthetic debug screenshots under ignored `.runs/tests/` may be generated by failing integration tests; they are not submission evidence.

## Artifacts, results, and tests

`schema_version` versions the artifact language. Version 3 adds explicit output targets and left/above relationships to the normalized regions, spatial relationships, geometry hints, and confidence/margin thresholds of version 2. New discovery emits v3; existing v2 artifacts and their hashes remain readable. Legacy v2 output bindings retain their declared right-of-label semantics through the shared reader. `capability_version` versions the recorded workflow. Runs pin a SHA256 content hash. The executor rejects v1 artifacts instead of guessing how old pixels should be interpreted. Convert one explicitly with `visual_agent.cli migrate-v1 OLD.json NEW.json`.

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m ruff format --check src tests
```

Integration tests start isolated proxy servers and real Chromium instances. They use screenshots and OCR for the complete workflow. Handoff protocol tests simulate operator commands and are labelled as such; they do not pretend that a person performed those test actions. Provider tests exercise retry limits without spending API quota.

Exit status is 0 for successful execution or a recognized business outcome, and 1 for failure. Each run writes `events.jsonl`, `result.json`, privacy-filtered screenshots, and `state.sqlite3` into its evidence directory. Local runtime artifacts live in ignored `.runs/`. Curated submission evidence belongs in `evidence/`.

## Project map

| Module                              | Responsibility                                                    |
| ----------------------------------- | ----------------------------------------------------------------- |
| `contracts.py`, `capability.py` | Typed artifact language, task contract, reviewed error policy     |
| `fixtures.py`                     | Authored offline test flow; not used to seed discovery            |
| `proxy.py`                        | Standalone synthetic legacy application                           |
| `vision.py`                       | OCR, dark-control crops, geometric field targeting, output OCR    |
| `surface.py`                      | Sole browser/input boundary                                       |
| `discovery.py`                    | Gemini requests, retries, proposal validation, recording/compiler |
| `engine.py`                       | Replay, outcome handling, ownership, resume verification          |
| `operator.py`                     | Authenticated local screenshot/control panel                      |
| `policy.py`, `evidence.py`      | Guardrails, redaction, events, checkpoints                        |
| `cli.py`                          | Setup checks and invocation                                       |

See `REPORT.md` for trade-offs, scope limits, desktop extension, and multi-tenant design. Nothing in the local workflow commits, pushes, or sends a submission email.

# Automatic target search

When an action target is absent from the visible screenshot, the shared executor
searches using overlapping vertical scrolls (up to eight down and eight up).
Each scroll takes a fresh screenshot, checks for overlays, and resolves the target
again. An unchanged OCR layout ends that direction early. Ambiguous or low-confidence
matches still require human intervention. Scroll events are recorded as
`target_search_scroll` in evidence. The mouse is moved to the visible work area
before scrolling so wheel input can reach the embedded bank pane.

Discovery can request bounded scrolling to inspect controls beyond the viewport,
then propose a locator from the newly observed pixels. Replay uses saved locators
and performs its bounded search without model calls.
This is bounded vertical search, not unrestricted exploration of arbitrary nested
or horizontal scroll areas.

Verification also searches by scrolling: the page heading and a typed field may
be verified in separate, overlapping screenshots. Final output rows are read
individually when the review table does not fit. Every search still checks
foreground layers and error handlers. A second bounded verification search permits
a delayed navigation to finish; an unresolved condition still requires human help.
The scenario tests cover normal replay, business errors, service notices and slow
pages at desktop and 500x270, plus dialog/session handoffs at desktop and 500x400.
