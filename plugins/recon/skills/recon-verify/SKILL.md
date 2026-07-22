---
name: recon-verify
description: >
  This skill should be used to independently check recon documents or grade a finished recon
  deliverable. Trigger phrases include "verify <topic>", "check the brief for X", "does the X
  brief stand on its own", "recon verify", and — when a deliverable already exists — "grade the
  deliverable for X" or "grade <topic>". Requires discovery.md and spec.md to exist for the
  topic. This skill's checks are only meaningful in a conversation that did NOT produce the
  documents being checked.
metadata:
  version: "0.3.0"
---

# Recon: Verify Phase

This is the cold-read test. A brief that only makes sense to the conversation that wrote it is
not a brief — it's a private note. The entire value of this skill depends on isolation, so
isolation is checked before anything else.

## Step 1: Contamination check — before reading any recon files

Inspect the current conversation. If it contains the discovery interview, the spec interview, or
any substantive discussion of this topic beyond the message invoking this skill, stop
immediately. Say something like:

> "I can't give you an honest check here — this conversation already contains the discussion
> that produced these documents, so I'd be grading my own understanding, not the documents.
> Open a fresh chat and say 'verify <topic-slug>'; everything needed is on disk."

Do not proceed in a contaminated conversation under any circumstances, including if the user
insists — offer the fresh-chat path again and explain that an insisted-upon contaminated check
would produce a pass that means nothing.

## Step 2: Resolve topic and choose the isolation mechanism

List `recon/` and confirm the slug. Confirm `discovery.md` and `spec.md` both exist; if either
is missing, name it and the skill that produces it, then stop.

Determine whether this environment can dispatch a subagent whose context starts empty and whose
prompt is fully controlled (e.g. a Task/agent-dispatch tool). If yes, prefer it: the current
session reads nothing and acts only as dispatcher. If no such mechanism exists, the fallback is
valid because Step 1 already guaranteed this session is itself cold: perform the checks directly.

**Hard rule for subagent dispatch:** the dispatch prompt may contain ONLY the file paths and the
check instructions below. No topic summary, no restatement, no "context" — any paraphrase smuggles
the dispatcher's understanding into the reader and invalidates the test.

## Step 3: Run both checks (whichever context is doing the cold read)

**Check A — Comprehension.** Reading only `discovery.md` and `spec.md`: restate what is being
asked for, every binding constraint, what was ruled out and why, and how the finished work will
be judged. Flag anything ambiguous, contradictory, or missing that a one-shot researcher would
need. If the restatement requires guessing at any load-bearing point, that is a FAIL.

**Check B — Critic.** As an expert skeptical reviewer of this domain: what did the brief miss?
Faulty assumptions, unconsidered edge cases, criteria in spec.md that are not actually checkable
cold, auto-reject conditions that contradict discovery.md. Every genuine flaw found makes this
check more useful; expect roughly a third of findings to matter — that ratio is worth it.

## Step 4: Write verify.md

Save `recon/<topic-slug>/verify.md` with this exact first line so later phases can gate on it
deterministically:

```
status: PASS
```

or `status: FAIL`. Then: `verified_at: <ISO-8601 timestamp>`, `mode: subagent` or
`mode: cold-session`, followed by the comprehension restatement and the critic findings.

PASS means: the restatement required no guessing AND no critic finding rises above nitpick.
Anything else is FAIL.

## Step 5: Report to the user

On PASS, say something like:

> "The brief passed a cold read: it's clear what's being asked for and how it'll be judged,
> without needing anyone who was in the original conversation. A few findings worth folding in
> anyway are below. When you're ready, open another fresh chat and say 'run the recon on
> <topic-slug>'."

On FAIL, lead with what a stranger couldn't work out, in plain language, and say the fix is to
update discovery.md or spec.md (directly, or via another recon-discover/recon-spec pass) and
re-verify. Do not soften a FAIL into a "pass with notes."

## Grade mode (post-work)

If the user asked to grade and `recon/<topic-slug>/deliverable.*` exists: run Step 1's
contamination check against the conversation that produced the deliverable (same rule, same
refusal). Then, cold, walk the deliverable against every criterion and auto-reject condition in
spec.md — spec.md and the deliverable are the only inputs. Save the result as
`recon/<topic-slug>/grade.md` with first line `grader: independent`, a per-criterion pass/fail
table, and an overall verdict. Auto-reject conditions are absolute: one hit fails the
deliverable regardless of everything else.
