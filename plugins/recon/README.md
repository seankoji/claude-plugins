# recon

A four-phase workflow for research, decisions, and reports — interrogate the problem, define how
the output will be judged, independently verify the brief stands alone, then execute against it
cold. Adapted from Part 1 of Dave Rensin's "Elephants, Goldfish and the New Golden Age of
Software Engineering," with the isolation between phases made structural instead of aspirational.

## Prerequisites

None — this plugin ships four skills only (no commands, agents, scripts, or env vars). It works
anywhere skills work: Claude Cowork and Claude Code.

## Install

```bash
claude plugin marketplace add seankoji/claude-plugins
claude plugin install recon@seankoji
```

In Cowork: **Customize → Plugins → Add marketplace**, enter `seankoji/claude-plugins`, then
install `recon` from the list.

## The core design decision

v0.1 of this plugin asked the execution phase to "disregard conversational context." That is not
an instruction a model can follow — attention has no off switch, and the failure mode is silent:
gaps in the saved documents get papered over from chat memory, the docs look better than they
are, and the saved record becomes untrustworthy. v0.2 replaces "forget" with two mechanisms that
actually work:

1. **Detect and refuse.** Verify and execute inspect the conversation first and refuse to run in
   any chat that contains prior discussion of the topic, redirecting to a fresh chat.
2. **Independent cold reads.** A new verify phase checks that the brief stands alone before any
   work happens, and grading is done by a fresh context (subagent where available) against the
   rubric — never silently by the author of the work.

## Components

| Skill | Purpose | Produces | Fresh chat? |
|---|---|---|---|
| `recon-discover` | Interrogation — surface the requirements the user hasn't thought of | `discovery.md` | No |
| `recon-spec` | Skeptical-reviewer interview — build a pass/fail rubric a stranger could apply | `spec.md` | No (same chat as discover is fine) — ends with a stop-and-choose: fresh chat, continue here, or hand off to `/imps:imps` |
| `recon-verify` | Cold read — comprehension test + critic review of the brief; also grades finished deliverables on request | `verify.md` (status: PASS/FAIL), `grade.md` | **Yes — refuses otherwise** |
| `recon-execute` | Produce the deliverable from the documents alone; independent grading with provenance | `deliverable.md`, `grade.md` | **Yes — refuses otherwise** |

Execute gates on all three prior files, requires `verify.md` to say PASS, and treats a brief
modified after its last verification as unverified (mtime check).

## Folder convention

```
recon/<topic-slug>/
  discovery.md      # problem, constraints, tradeoffs, ruled-out alternatives, non-goals
  spec.md           # pass/fail criteria, auto-reject conditions, quality bar
  verify.md         # status: PASS|FAIL, mode, findings
  deliverable.md    # the actual output (or native file type)
  grade.md          # grader: independent | self-assessed, per-criterion results
```

Every skill resolves the topic by listing `recon/` on disk, never from chat memory — so the
fresh-chat handoffs work without re-explaining anything. These files, not transcripts, are the
durable record: "why did we decide X?" is answered by discovery.md's alternatives section;
"did the result hold up?" by grade.md and its provenance line.

## Usage

1. Fresh or existing chat: "help me think through X" → discover, then "let's build the rubric" →
   spec, in the same conversation if convenient.
2. Spec stops and asks how to proceed — pick one:
   - **Fresh chat (recommended):** paste the block it gives you as the first message in a new
     chat, which runs `recon-verify`. Fix and re-verify until PASS, then, in another new chat,
     "run the recon on X".
   - **Continue here:** faster, but self-assessed — no independent cold read. Good for small or
     low-stakes topics; `verify.md` is saved as `status: SKIPPED` so the record stays honest.
   - **Hand off to `/imps:imps`:** for a larger implementation task in Claude Code — spec gives
     you a ready-to-run task description pointing at `discovery.md` and `spec.md`.
3. Optionally, in yet another new chat: "grade X" for an independent grade if execution had to
   self-assess.

## Known limits

- Skill triggering in Cowork is descriptive, not deterministic — mid-conversation "just do the
  research now" can bypass the pipeline entirely. The pipeline constrains what happens once
  invoked; it cannot force its own invocation.
- The contamination check is a model inspecting its own context — reliable for "this chat
  clearly contains the interviews," best-effort for subtler bleed.
- The mtime staleness check needs shell access to file timestamps; where unavailable, execute
  falls back to asking the user when the docs last changed.

## Porting to Claude Code

The schema is shared, so this drops into a Claude Code marketplace as-is. Two upgrades worth
making there:

- **Deterministic invocation:** mirror the four skills as explicit slash commands so the
  pipeline is entered on purpose, eliminating the trigger-reliability limit above.
- **Stronger judge:** `recon-verify`'s cold read is same-model-fresh-context. A lineage-diverse
  judge (e.g. a cold Gemini via CLI, as in an elephant-goldfish-style setup) is strictly
  stronger — the verify skill's Step 3 checks are written to be handed to any cold reader, so
  swap the dispatch mechanism and keep the checks and the `status:` contract unchanged.

## License

MIT
