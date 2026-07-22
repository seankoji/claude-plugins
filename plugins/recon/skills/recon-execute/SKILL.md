---
name: recon-execute
description: >
  This skill should be used to produce the final deliverable for a topic whose brief has passed
  verification. Trigger phrases include "run the recon on X", "do the research for X now",
  "produce the report for X", "recon execute". Requires discovery.md, spec.md, and a verify.md
  with a PASS status. Only meaningful in a conversation that contains no prior discussion of the
  topic — refuse and redirect to a fresh chat otherwise.
metadata:
  version: "0.3.0"
---

# Recon: Execution Phase

The two saved documents are the entire brief. That is not a stylistic preference — it is what
makes the verification meaningful and the saved reasoning trustworthy later.

## Step 1: Contamination check — first, before anything else

Inspect the current conversation. If it contains the discovery interview, the spec interview,
the verification run, or any substantive prior discussion of this topic beyond the invoking
message, stop. Say something like:

> "I can't run this here — this conversation already contains the discussion behind the brief,
> so the work wouldn't be a true test of the saved documents, and any gap in them would get
> silently papered over from memory. Open a fresh chat and say 'run the recon on <topic-slug>';
> everything needed is saved."

This is a refusal, not a warning. Do not proceed in a contaminated conversation even if asked;
if the user wants to add information, the answer is to update the documents and re-verify.

## Step 2: Gate on the artifacts — all three

List `recon/` and confirm the slug. Then check, in order:

1. `discovery.md` exists — else stop, name it, point to recon-discover.
2. `spec.md` exists — else stop, name it, point to recon-spec.
3. `verify.md` exists AND its first line is `status: PASS` — else stop and say the brief hasn't
   passed an independent cold read yet; point to recon-verify in a fresh chat.

**Staleness check:** compare file modification times (e.g. `ls -l --time-style=full-iso recon/<topic-slug>/`).
If discovery.md or spec.md was modified after verify.md, the verification no longer covers what's
on disk. Stop and say something like:

> "The brief changed after its last independent check, so that pass no longer covers what's
> written now. It's a quick fix: fresh chat, 'verify <topic-slug>', then come back."

## Step 3: Read the brief and surface it

Read discovery.md and spec.md in full. Before starting work, play back to the user in two or
three sentences: what is being produced, the binding constraints, and when the documents were
last touched. This is the user's last cheap moment to catch "that's three weeks old and my
budget changed."

## Step 4: Do the actual work

Use every tool the task genuinely requires — web search, files, connectors. This is the long,
effortful part, and the framing phases are precisely what make it safe to be thorough here
rather than a reason to rush. Respect the auto-reject conditions in spec.md as hard boundaries
while working, and never re-propose an alternative that discovery.md rules out.

## Step 5: Independent grading — never self-graded without saying so

Save the deliverable as `recon/<topic-slug>/deliverable.md` (or the natural file type — a
spreadsheet, doc, or deck — if markdown isn't the right shape).

Then grade it:

- **If a fresh-context subagent can be dispatched:** dispatch it with ONLY three things — the
  path to spec.md, the path to the deliverable, and the instruction to grade every criterion and
  auto-reject condition as pass/fail. No summary of the topic, no restatement. Save its output
  as `recon/<topic-slug>/grade.md`, first line `grader: independent`.
- **If not:** perform the rubric walk in this session, save it as grade.md with first line
  `grader: self-assessed` — and tell the user plainly, something like:

  > "One honest caveat: I graded this myself, and I'm the one who wrote it, so treat the grades
  > as a first pass. For a grade that actually means something, open a fresh chat and say
  > 'grade <topic-slug>' — it'll judge the work against the rubric cold."

Provenance in grade.md is mandatory in both cases. A wrong grade that is labeled self-assessed
is recoverable; a wrong grade stored as if it were independent poisons everything saved after it.

## Step 6: Present

Give the user the deliverable, the grade with its provenance stated, and any criterion not
fully met, flagged explicitly rather than silently dropped. The topic folder now holds the four
documents worth keeping — discovery, spec, verify, deliverable+grade — which is the compact
record of what was decided, why, and how well the result held up.
