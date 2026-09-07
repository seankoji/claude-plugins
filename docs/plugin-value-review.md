# Plugin value review

Assessment of the seven plugins at `b14c062`, 7 September 2026. This is a source and
behavior review, not a measured productivity study. No usage or latency data establishes
that running the entire catalogue improves on a capable agent with ordinary tools.

The project earns its existence where executable code or a repeatable verification
process removes a specific failure. Personality, longer prompts, extra agents, and
self-reported success do not establish value. Install by need.

| Plugin | Assessment | Improvement in this change | Use the simpler alternative when |
| --- | --- | --- | --- |
| offload-sidecar | Keep. File I/O outside the parent model's context, deterministic transforms, local/cloud boundaries, and output rejection are concrete capabilities. | Success receipts name validation limits and hash output artifacts. A structurally valid model answer cannot quietly present itself as verified content. | `jq`, Python, or a shell pipeline expresses the operation directly. No measured savings justify routing small inputs through another model. |
| claude-tuneup | Keep for Claude settings maintenance. Cross-scope analysis is useful; call frequency alone is a poor permission policy. | Correlate calls with results, deduplicate replayed IDs, and provide transcript/line evidence. Require demonstrated approval friction before suggesting a rule. | A single known rule needs editing, or the transcript contains no evidence of avoidable approvals. |
| babysitter | Keep for ongoing PR recovery. Worktree isolation and event handling are distinct from a one-off merge command. | Stop resolving threads based on a comment prefix. Pin merge requests to the checked head; reject incomplete review state and ambiguous API outcomes. | The PR is ready and the host can verify and merge it once. Native GitHub auto-merge already handles waiting for required checks. |
| elephant-goldfish | Keep for a durable bootstrap document. A reader without repo access can reveal missing explanations. | Verify cited claims against the repository before the cold read, record the checked revision, and report factual grounding separately from readability. | A short README answers the actual onboarding question. Different model lineage does not prove independence or truth. |
| imps | Conditional. Durable task state and isolated integration are useful for substantial independent work. Its long mandatory workflow is expensive for small tasks. | Require each dispatch to resolve a distinct unknown or produce an independent result; reuse existing evidence and report worker/integration/repair overhead. | One agent can complete the change with ordinary tests and review. A single atomic task should not be split to fill a roster. |
| ape | Conditional. Source comparisons are valuable when tied to a local gap. Recommendations are hypotheses until an adoption experiment succeeds. | Synthesize only the current run's explicit reports; failed analysts or verification block completion. Allow zero recommendations and require a simpler alternative, baseline, pass condition, and abandon condition. | The source is already known, so use `study`, or there is no concrete local weakness worth researching. |
| prompt-builder | Weakest original case. A structured prompt and an internal critique are easy to obtain without a plugin. Keep only for repeated tasks with regression evidence. | Freeze a baseline and cases before drafting; run fresh comparisons, replay deterministic checks, keep the simpler passing prompt, and label unavailable execution honestly. Bound questions and revision rounds. | A direct request works, or evaluation cannot identify a recurring failure worth fixing. |

The critical defects were observable in executable paths: babysitter accepted any
`[babysitter]` reply as authority to resolve a thread, omitted the merge head SHA, and
treated HTTP 200 as enough to announce a merge. Ape globbed cached reports and proceeded
when report verification returned no result. Regression tests now exercise those cases.
The permission scanner's new JSON mode and the prompt evaluator run entirely locally.

The installed Claude CLI advertised `plugin eval` with a no-plugin baseline, but
`claude plugin eval init --bare prompt-value` returned
`plugin eval is currently in early access`. Prefer that host capability when it is
available. The bundled comparator only checks saved outputs across runtimes; it does
not claim to execute models or replace a native experiment runner.

The prompt comparison follows the sequence in Anthropic's
[evaluation guidance](https://platform.claude.com/docs/en/test-and-evaluate/develop-tests):
define criteria before testing and evaluate observable outputs. The merge helper uses
GitHub's [expected head SHA](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request)
to reject a changed head. Neither mechanism substitutes for judgment about the task.

## What would establish value next

For each plugin, choose a real recurring task and compare the native-tool baseline with
the plugin using the same inputs. Record completed outcomes, failures, wall time, model
usage where available, and human corrections. Keep the fixture and failed runs. Repeat
before claiming general gains; the tests in this PR prove specific behavior, not ROI.

If prompt-builder or imps adds steps without improving those outcomes, retire that
workflow or reduce it to an optional skill. Do not add another orchestration layer to
explain away a losing comparison.
