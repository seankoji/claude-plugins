# spec.md — required structure

A grading rubric a stranger could apply, not a wishlist. The test for every line: could
someone who was not in the conversation mark this pass or fail without asking anyone?

## 1. Pass/fail criteria
Itemised. Give each requirement a stable ID such as `[REQ-LOGIN]`, retain it through the handoff and implementation task, and specify its verification method (`inspection`, `command`, `runtime`, or `manual`). Each one checkable cold. For implementation, use `[verify:runtime]` for user journeys and `[verify:command]` for executable checks. Record the observable outcome, environment, failure/recovery cases and evidence needed. Unverified is incomplete; an implementer may not weaken a criterion to pass it. Cross-reference discovery.md where it sharpens the
criterion — "per the budget ceiling in discovery.md §2, no recommended option above £X".

Reject anything that cannot be marked without judgement calls the grader hasn't been given.
"Well researched" is not a criterion. "Every load-bearing claim cites a primary source with a
working URL" is.

## 2. Auto-reject conditions
A short list of things that fail the entire deliverable immediately, regardless of other
merits. Keep it short — if everything is auto-reject, nothing is. Typical members:

- Re-proposes an alternative discovery.md rules out
- Missing a section listed as non-negotiable
- A load-bearing claim with no source (research) or an untested code path (implementation)
- Touches a file the brief puts out of scope

## 3. Quality bar
What separates an expert result from a fluent, plausible, mediocre one *on this specific
topic*. Phrase it as concretely as the interview allowed. This section is the one a grader
uses to break ties, so vagueness here costs less than vagueness in §1 — but it still costs.

## 4. Grading notes
How to handle partial credit, source disagreement, and unresolvable open questions carried
over from discovery.md §7. State whether an honest "not knowable" is an acceptable answer; if
the interview never settled that, say it never settled.
