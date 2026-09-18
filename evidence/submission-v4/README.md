# Fresh end-to-end submission evidence

This bundle contains one genuine Gemini discovery and two deterministic replays of the exact saved artifact. All records are synthetic and were copied from local run evidence without prompts, credentials, typed values, or SQLite state.

- `capability.json`: schema-v3 artifact learned from the discovery run; provenance identifies `live_llm_discovery` and Gemini 3.5 Flash-Lite.
- `discovery/`: `success / DISCOVERY_COMPLETE`, 13 LLM calls, and the artifact hash below.
- `replay-normal/`: `success / REVIEW_READY`, zero LLM calls.
- `replay-not-found/`: `business_outcome / MEMBER_NOT_FOUND`, zero LLM calls.

All three result files pin artifact hash `594976ce1be88fbd9685de2796954dc0aefb87dfe5071fc498f3663b472c148d`. The normal replay uses the discovered member/month/delivery inputs; the not-found replay uses the same artifact with the proxy's injected not-found scenario. This demonstrates that replay follows the saved artifact without a model and reports an expected business outcome separately from a technical failure.
