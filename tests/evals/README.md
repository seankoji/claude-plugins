# Workflow policy regression comparison

Run `node tests/evals/workflow-policy-eval.js > workflow-results.json` from a full
repository clone. `tests/run-js.sh` runs it in CI and fails on any candidate false
completion or false block. The JSON retains each case, call trace, expected and
observed merge, elapsed test time, source hash and dataset hash.

The baseline is commit `52f4ee982b5b156975a84d6dd927efa4f12ef604`. Both variants run
their actual `drivePrAndClose` implementation against the same 24 scripted scenarios,
three times each. Candidate snapshot, review, CI and acceptance responses are fixtures.
This tests policy transitions, not whether a model can produce correct evidence.

| Result (9 September 2026) | Baseline | Candidate |
| --- | ---: | ---: |
| Trials | 72 | 72 |
| Expected outcomes | 15 | 72 |
| False completion | 57 | 0 |
| False block | 0 | 0 |
| Mean scripted agent calls | 2.125 | 9.542 |

The adversarial corpus deliberately concentrates on missing protections; these are
not production failure rates. Repeating deterministic cases checks consistency,
not statistical confidence. More verification calls are a real orchestration
tradeoff; test execution time is not model latency. Model IDs and cost remain null.
Real-model and lean-baseline comparisons, interactive host parity and independent
holdout tasks remain rollout work tracked in issue #264. No model-quality or
cost-effectiveness claim follows from these results.
