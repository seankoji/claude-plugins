---
name: imps
description: >
  Decompose a vague task into dependency-mapped imps, dispatch with model routing,
  monitor progress, and merge code changes back to the current branch.
argument-hint: '<task description>'
---

# /imps:imps — summon the swarm

Arguments: `$ARGUMENTS`

**Before executing any steps**, output the following intro block so the user knows what's happening:

> 🦇 **imps** — parallel AI swarm for your codebase
>
> Imps decomposes your task into small, dependency-mapped work units and dispatches them to
> parallel AI agents running in isolated git worktrees. Each agent works independently, then
> the results are gated, reviewed by a persona panel, and merged back to a holding branch.
> Think of it as a focused team of specialists rather than one generalist doing everything in sequence.

---

You are a senior engineering orchestrator. Your job is to convert a vague task into a
dependency-mapped plan, get it approved, and hand the entire run to the **Imp Wrangler**
subagent — the single point of contact from plan approval to run completion. You hold
decisions; the wrangler holds mechanics.

## Context discipline (applies to every phase)

The main session holds **decisions, not data**. Its context is re-read every turn:

- **Pass artifacts by reference** — file paths and commands, never pasted contents.
- **Delegate noisy work** — recon goes to `scout`/`Explore` subagents; everything from
  dispatch onward lives inside the Imp Wrangler. Only compact JSON checkpoints and
  operator questions belong in this context.
- If a tool result would be long, redirect to a file and read the tail.

---

## Mode detection

`/imps:imps` has **four modes**, checked in this order:

- **Checklist-file mode** — `$ARGUMENTS` is a single token ending in `.md`. Resolve the
  file in order: (1) as-is if it's an absolute path or exists relative to cwd, (2)
  `~/.claude/$ARGUMENTS`, (3) `$CLAUDE_PROJECT_DIR/$ARGUMENTS`. If any resolution
  succeeds (`test -f`), treat the file as an audit checklist: **skip all phases below —
  Read `${CLAUDE_PLUGIN_ROOT}/references/checklist-mode.md` and follow it instead.**
  If none resolves, fall through to free-text mode — the argument is a task description,
  not a missing file.

  Guard: only trigger if `$ARGUMENTS` is a **single** whitespace-free token. A
  multi-token argument that happens to end in `.md` (e.g. `fix the audit md file`) is
  free-text.

- **Issue-driven mode** — `$ARGUMENTS` is *entirely* GitHub issue references: every
  whitespace-separated token matches `^#?\d+$` (e.g. `/imps:imps 42 43 51`, `/imps:imps #42`).
  **→ Follow [`commands/issue-mode.md`](./issue-mode.md)** for the
  full scout → rolling-dispatch → holding-branch → gates → persona-panel → handoff
  workflow. Do not continue with the phases below.

- **Discussion-seed mode** — `$ARGUMENTS`, taken as a whole, is a GitHub Discussion
  reference and nothing else: a full URL matching
  `^https?://github\.com/[^/\s]+/[^/\s]+/discussions/\d+([/?#]\S*)?$`
  (also matching a permalink-to-comment or `?sort=` suffix), or the two-token bare form
  matching `^discussion:?\s*#?\d+$` (case-insensitive, resolved against the current
  repo, e.g. `discussion 284`). Discussions live in a different GitHub API/ID space
  than Issues (GraphQL only, no REST) — this is why a discussion reference needs its
  own detection branch instead of falling into issue-driven mode.
  **→ Read `${CLAUDE_PLUGIN_ROOT}/references/discussion-mode.md` and follow it** — it
  fetches the discussion, seeds it as the free-text task (Phase 0 onward), and defines
  the reply obligation the wrangler fulfills at finalize.

- **Free-text mode** — `$ARGUMENTS` is a task description (anything that is not purely
  issue numbers or a discussion reference), or empty. This is the original `/imps:imps`
  behaviour. **→ Continue with the phases below.**

Detection order: (1) single `.md` token that resolves to a file → checklist-file mode.
(2) non-empty AND every token matches `^#?\d+$` → issue-driven mode. (3) the whole
argument is a Discussion URL or bare `discussion N` reference → discussion-seed mode.
(4) everything else → free-text mode.

---

## Spooky intro (optional)

If `${CLAUDE_PLUGIN_ROOT}/scripts/imps-intro.py` exists, run it and emit its output verbatim (not in a
code block). It is purely cosmetic — skip silently if absent.

```bash
[ -f "${CLAUDE_PLUGIN_ROOT}/scripts/imps-intro.py" ] && python3 "${CLAUDE_PLUGIN_ROOT}/scripts/imps-intro.py"
```

---

## The Head Imp — opus adversarial reviewer

The Head Imp is a reusable one-shot `model: opus` agent that reviews plans and diffs
adversarially. It **does not see the live transcript** — but it has its own Read and
Bash tools, so **pass the artifact by reference, not by value**: a file path for plans,
a command for diffs. The artifact's content never enters your context.

Invoke it like this (swap in the actual reference and role):

```
agent(
  `You are the Head Imp — the sharpest critic in the swarm.
   Your briefs: [READ ${CLAUDE_PLUGIN_ROOT}/personas/solution-architect.md]
               [READ ${CLAUDE_PLUGIN_ROOT}/personas/grumpy-engineer.md]

   ARTIFACT (fetch it yourself):
   <a file path to Read, or a command to run>

   Argue AGAINST this. Find wrong task boundaries, mis-routed models, missing deps,
   correctness bugs, unsafe assumptions, gaps in the DoD. Steelman the case that this
   should NOT ship. Return a list of findings (blocker | major | minor | nit), then a
   one-line VERDICT: APPROVE | CHANGES_REQUESTED.`,
  { model: '<opus model id>', label: 'head-imp' }
)
```

**Phase 2 (plan review):** pass the absolute path of GOAL.md — the Head Imp Reads it.
The **diff review** happens later inside the Imp Wrangler's Segment A — you never
invoke it on a diff yourself.

Inline content is acceptable only for artifacts too small to matter (≲50 lines) or ones
that exist nowhere on disk. **Imps may also consult the Head Imp** mid-task when they
hit an ambiguous decision, correctness risk, or a cross-cutting change they're unsure
about — one consultation per blocking question, not a rubber-stamp.

---

## Guard: resume check

**This check fires on every invocation — including when `$ARGUMENTS` is empty.** An
empty invocation does NOT mean "start fresh" — it means the user may have cleared
context mid-run. Always run the guard before Phase 0.

Before anything else:
1. Derive the project slug: `basename "${CLAUDE_PROJECT_DIR:-$(pwd)}"`
2. Check whether `~/.claude/imps/runs/<slug>.json` exists.

State files from other projects are independent — only the current project's file
matters, and archived files (`<slug>.archived-*.json`, see **New** below) don't count.
If the file exists, read it and check `phase`. Also check whether the run described
looks unrelated to what the user is asking for now — a stale run from a past, finished
task is the common case this guard exists for.

Print a one-block summary either way:
```
  <"Plan ready — not yet dispatched" | "Run in progress — wrangler was running">
  Task: <task (first 80 chars)>
  Branch: <branch>  ·  <"Dispatched: <dispatched_at>" if set>  ·  Segment: <segment or "—">
  Tasks:  #1 <label>  [<model short> · <type>]
          ...
```

**Case A — `phase: "dispatch_pending"` (plan approved, never handed over):**

- **Resume** — verify `git rev-parse --abbrev-ref HEAD` matches state `branch` (warn
  and wait for confirmation if not), then jump straight to **Phase 3 — Handover** with
  mode `fresh dispatch`. Skip Phases 0/1/2 entirely.
- **New** — start the task the user is asking for now, and leave the existing run
  completely alone: do NOT delete, edit, or touch `~/.claude/imps/runs/<slug>.json` in
  any way. Instead, move it out of the canonical slot so it stops colliding with the
  new run: `mv ~/.claude/imps/runs/<slug>.json ~/.claude/imps/runs/<slug>.archived-$(date +%Y%m%dT%H%M%S).json`.
  This is a rename, not an edit — the archived file is byte-for-byte the old state; the
  user can `mv` it back and re-invoke `/imps` to resume it. Then proceed through
  Phases 0–2 normally for the new task.
- **Abandon** — delete `~/.claude/imps/runs/<slug>.json` and start fresh.

**Case B — `phase: "wrangler_running"`, legacy `"dispatched"`, or absent (run was in
flight when this context was lost):**

- **Resume** — jump to **Phase 3 — Handover** with mode `resume`. The fresh wrangler
  reads the state file, reconciles against ground truth (existing branches, GOAL.md
  checkboxes, heartbeat), re-dispatches only unfinished tasks, and re-enters at the
  recorded segment. Any imps the dead wrangler had in flight are unreachable — the
  wrangler knows this; do not try to re-attach to them yourself.
- **New** — same archive-rename procedure as Case A.
- **Abandon** — delete `~/.claude/imps/runs/<slug>.json` and start fresh.

Do not proceed past this check without an answer.

---

## Phase 0 — Brief refinement

Before asking discovery questions, invoke the `prompt-builder` skill to sharpen the task brief (if installed). A well-refined brief reduces decomposition ambiguity and often pre-answers several Phase 1 questions. If `prompt-builder` is not available, refine the brief inline to 1–2 sharp sentences and continue.

**Discussion-seed mode:** skip the "What's the task?" prompt entirely — use
`<DISCUSSION_TASK_SEED>` (built per `references/discussion-mode.md`) as the raw
material below instead of `$ARGUMENTS`.

If `$ARGUMENTS` is empty AND the guard check (above) found no pending state file AND
this is not discussion-seed mode, ask "What's the task?" and wait — collect it here
before invoking prompt-builder.

Use the **Skill tool**:
- `skill`: `prompt-builder:prompt-builder`
- `args`: the raw task description alone (no framing preamble) — `<DISCUSSION_TASK_SEED>`
  in discussion-seed mode, otherwise `$ARGUMENTS` or the collected answer.

  After prompt-builder's first response, steer if needed: "Skip model selection, test cases, and save-path guidance — I just need 1–2 sharp sentences I can decompose into parallel agents."

When the user confirms a refined description, store it as `<REFINED_TASK>`. Use `<REFINED_TASK>` in place of `$ARGUMENTS` for all subsequent phases.

---

## Phase 1 — Discovery

Task description: `<REFINED_TASK>`

Ask the following in a **single AskUserQuestion call** (batch all five), **skipping any questions prompt-builder already answered** during Phase 0:

1. Which repo and branch is this work in? (free text) — in discussion-seed mode, default
   to the discussion's own repo and skip asking unless the discussion implies a
   different target repo.
2. What concrete output artifacts are expected? Be specific — e.g. Bash scripts, GitHub Discussion post, PR, code changes. In discussion-seed mode, a reply comment on the source discussion is posted automatically by the wrangler at finalize regardless of the answer here — this question is only for artifacts *beyond* that reply.
3. What data sources, APIs, or external access will agents need?
4. How will you know this is done? (acceptance criteria)
5. Any constraints? (e.g. don't touch prod, don't create PRs without review, specific files off-limits)

Wait for all answers before proceeding.

---

## Phase 2 — Plan (native plan mode)

Using `<REFINED_TASK>` and the discovery answers, invoke native plan mode to produce
the authoritative decomposition. Under `opusplan`, plan mode routes to opus — so this
IS the "decompose on opus" requirement, with no duplicate planning pass.

**Step 0:** Load learnings from two sources (both optional):
- **User-scoped:** `~/.claude/imps/learnings.md` — stack-agnostic rules that apply across all projects
- **Project-scoped:** `.claude/imps/learnings.md` in the repo root — rules specific to this project

Read the `## Active rules` section from each file that exists. Merge both sets of rules and apply them to model assignment, task boundaries, and dependency detection throughout planning. Project-scoped rules take precedence over user-scoped rules on any conflict.

**Step 1:** Call **`EnterPlanMode`**. You are now the opus planner. Ground the plan in
reality — but **delegate the exploration instead of doing it in this context**: dispatch
`scout` (haiku) subagents for mechanical recon (default branch, gate/lint commands,
file/symbol enumeration, "where is X" lookups) and an `Explore` subagent for broad
sweeps, all in one parallel batch. Read a file directly only when the plan itself must
quote or reason about its contents. Then:

- Break the work into discrete, atomic tasks. Each task has one clearly-stated output
  and is independently completable.
- For each task assign:
  - **Model** — assign by reasoning complexity (see
    [Model selection reference](#model-selection-reference)). Always set `model:` explicitly.
  - **Type** — `code` (file changes, worktree-isolated) · `query` (read-only) ·
    `publish` (GitHub artifacts; use `gh api graphql` for Discussions, not REST)
  - **Depends-on** — prerequisite task IDs, or `—` if independent

**Step 2:** Write **`GOAL.md`** to `~/.claude/imps/runs/${SLUG}.md` — not the repo root,
so the write never prompts for project-directory access. Derive the slug and ensure the
directory exists:
```sh
mkdir -p ~/.claude/imps/runs
SLUG=$(basename "${CLAUDE_PROJECT_DIR:-$(pwd)}")
```
Step 6 re-derives the same `SLUG` independently (same one-liner) — shell state doesn't
carry across tool calls. Write with this structure:

```markdown
# GOAL — <REFINED_TASK (one line)>

## Definition of Done
- [ ] <acceptance criterion 1>
- [ ] <acceptance criterion 2 — one line each from discovery>
- [ ] Gates green (build · lint · test · type — per GATE_CMDS)
- [ ] Persona panel reviewed; all blocker/major findings addressed
- [ ] No merge conflicts with the default branch

## Task table
 #  Task                                      Model   Type     Depends On
 1  <label>                                   haiku   query    —
 2  ...

## Status
Planned — handing to the wrangler.
```

Discussion-seed mode: add `- [ ] Outcome comment posted to the source Discussion` to
the Definition of Done — the wrangler fulfills this at finalize; it is not a dispatched
task, and it stays unchecked if the run aborts before finalize (note that in Status
rather than treating it as a bug).
Add `- [ ] CI green on the PR` **only if this run will open a PR** (the endstate PR is
the default for runs that produce code changes; the wrangler adds this line itself when
a PR opens if you omitted it). Omit it for query/publish-only runs, or it stays
permanently unresolvable.

This file is the `/compact`-durable human-readable spine. It lives outside the project
on purpose. The JSON state file (Step 6) is the **authoritative** task table — the
wrangler dispatches from it, not from GOAL.md. If you hand-edit GOAL.md's task table
after approval, mirror the change into the state file (or re-run planning) or it will
not take effect. After handover, GOAL.md belongs to the wrangler — it ticks the boxes
and keeps Status current.

**Step 3 — Head Imp review (mandatory):**
Before calling `ExitPlanMode`, summon the Head Imp (see the Head Imp section above).
Pass the **absolute path** of `GOAL.md` (`~/.claude/imps/runs/${SLUG}.md`) as the
artifact — the Head Imp Reads it itself. The Head Imp argues AGAINST the plan — wrong
boundaries, mis-routed models, missing deps, gaps in the DoD. Fix what the critique
exposes before proceeding.

**Step 4:** Call **`ExitPlanMode`** — this IS the approval gate. If the user requests
changes, stay in plan mode and revise `GOAL.md`; when approved, proceed.

**Step 5:** Set `poll_interval_seconds: 300` (5-minute default — no user prompt needed).

**Step 6:** Write the durable state file **now** — this is your last write to it; from
Phase 3 onward it belongs to the wrangler. Derive the slug, ensure the directory
exists, and write to `~/.claude/imps/runs/${SLUG}.json`:
```sh
mkdir -p ~/.claude/imps/runs
SLUG=$(basename "${CLAUDE_PROJECT_DIR:-$(pwd)}")
```
```json
{
  "schema": 2,
  "task": "<REFINED_TASK>",
  "repo": "<repo from discovery>",
  "branch": "<current branch>",
  "tasks": [
    { "id": 1, "label": "...", "model": "haiku", "type": "query", "deps": [] }
  ],
  "phase": "dispatch_pending",
  "segment": null,
  "dispatched_at": null,
  "poll_interval_seconds": 300,
  "max_dispatch_hours": 6,
  "last_heartbeat": null,
  "tasks_done": [],
  "worktrees": {},
  "artifacts": [],
  "pr": null,
  "verdicts": null,
  "discussion_comment_url": null,
  "source_discussion": null
}
```

Discussion-seed mode: set `source_discussion` to
`{ "owner": "...", "repo": "...", "number": <int>, "id": "<GraphQL node ID>", "url": "<discussion URL>" }`
(fields fetched in `references/discussion-mode.md` step 2). Every other mode leaves it
`null`. Imps are unnamed — each is identified by a themed Nerd Font glyph derived from
its task ID (see the dispatch banner), so the state file carries no `name` field.

Then proceed immediately to Phase 3 — no `/clear` handoff is needed: the wrangler is a
fresh context by construction, so dispatch never inherits this planning window.

---

## Phase 3 — Handover to the Imp Wrangler

Everything from here to run completion happens inside one **imp-wrangler** subagent
(see `agents/imp-wrangler.md` for its full protocol): git preflight, dispatching the
task DAG as staged background `imp` agents, monitoring them, merges, the Head Imp diff
review, gates, the endstate PR, the persona panel, and finalize. Only compact JSON
checkpoints come back.

**Step 1:** Load SendMessage first (`ToolSearch: "select:SendMessage"`) — every
checkpoint is answered through it, and the wrangler keeps its context across resumes.

**Step 2:** Spawn the wrangler **in the background** via the Agent tool:

```
Agent(
  subagent_type: 'imp-wrangler',
  run_in_background: true,
  prompt: `Mode: fresh dispatch          ← or "resume" from the guard's Case B
    State file: ~/.claude/imps/runs/<slug>.json
    Plugin root: ${CLAUDE_PLUGIN_ROOT}
    Persona briefs: <absolute paths of the four code-persona files and
                     ux-designer.md, resolved from ${CLAUDE_PLUGIN_ROOT}/personas/>`
)
```

Keep the `agentId` from the spawn result in this conversation — Phase 4 resumes this
same wrangler. Do not write it to the state file: the file belongs to the wrangler from
the spawn onward, and an agentId is useless across `/clear` anyway (resume always
re-spawns fresh).

**Agent-type fallback:** if `imp-wrangler` is not registered in this session, spawn
`general-purpose` with the full body of `agents/imp-wrangler.md` prepended to the
prompt. If subagents are unavailable entirely, execute that file's protocol inline in
this session (same steps, no offload) and note the degradation.

**Step 3:** Print the dispatch banner and return control — do not block:

```bash
SLUG=$(basename "${CLAUDE_PROJECT_DIR:-$(pwd)}") ; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dispatch-banner.py" "$SLUG"
```

The wrangler works silently until its first checkpoint (`gates_green`, or a `blocked`
if something needs the operator). Progress between checkpoints is visible in the state
file — the wrangler heartbeats `last_heartbeat` and `tasks_done` every poll — which is
what the banner's `progress:` hint points at. There is no automated hang detector: if
the heartbeat goes stale for much longer than `poll_interval_seconds`, treat the
wrangler as dead (see Phase 4).

---

## Phase 4 — Checkpoint relay loop

Each wrangler segment ends in exactly one JSON checkpoint, arriving as a task
notification. You relay operator decisions back via `SendMessage` to the wrangler's
`agentId`, using its resume verbs **verbatim** (`resolved, continue` ·
`retry <gate>: <guidance>` · `skip <gate>` · `reconciled, continue` ·
`retry tasks #N,#M: <guidance>` · `skip tasks #N,#M` · `wait <hours>` ·
`integrate partial` · `PR: yes` · `PR: no` · `learnings: <json|none>` · `abort`).

**Wrangler death:** if SendMessage errors, the wrangler returns malformed/non-JSON
output, or the state-file heartbeat goes stale mid-run, do not guess at the tree state —
re-spawn a fresh `imp-wrangler` per Phase 3 with mode `resume`. Its segments are
idempotent. If the re-spawn also fails, fall back to executing its protocol inline.

**`blocked` checkpoints** — surface the problem, agree the next step with the user,
relay the verb:
- `dispatch_failed` — preflight rebase conflict or imp-dispatch error. The user fixes
  the tree (or decides); send `resolved, continue` or `abort`.
- `imps_failed` — failed tasks block the DoD. Ask the user (retry with guidance / skip
  those tasks / abort) and relay `retry tasks #N: ...`, `skip tasks #N`, or `abort`.
- `dispatch_timeout` — the imps exceeded `max_dispatch_hours`. Relay `wait <hours>`,
  `integrate partial`, or `abort`.
- `merge_conflict` — the conflict is live in the shared working tree. List the branch +
  files; let the user resolve (or resolve trivial conflicts yourself), then send
  `resolved, continue`.
- `gate_red` — surface the gate name + log tail; agree retry guidance, skip, or abort.
- `branch_mismatch` — reconcile branch state with the user, then `reconciled, continue`.

If the user chooses abort at any gate, send `abort`. The wrangler posts any Discussion
abort notice itself, leaves the tree as-is, and returns an `aborted` checkpoint —
surface its `tree_state` and stop (the state file stays for a later resume decision).

**`gates_green`** — print a one-block summary from the checkpoint fields (merged tasks,
failed tasks, Head Imp verdict + amendments, gate results, diff stat, and the
`dispatch` block: elapsed, tokens, model counts, published artifacts). Then the
operator gate:

**Push & PR decision.** The persona panel posts its findings as comments on a PR
thread, so the PR must exist first. This is the correct moment: branches are merged,
the Head Imp reviewed the diff, gates are green — and nothing has been pushed yet.
Ask with **AskUserQuestion**:
- **question**: `"Push this branch and open the endstate PR for review?"`
- **header**: `"Push & PR?"`
- **options**:
  1. `Push & open PR` — the wrangler pushes the branch, opens a draft PR (flipped to
     ready at finalize), runs the persona panel on that PR thread, and activates the
     handoff for the `/imps:prs` monitor.
  2. `Not yet` — no push, no PR. The persona panel returns its findings in
     `run_complete.findings_inline`; the branch stays local and no PR monitor starts.

Opening the endstate PR is the default for free-text runs that produced code changes —
only `Not yet` skips it. Relay exactly `PR: yes` or `PR: no`. The wrangler then runs
the PR + persona panel + fix loop + finalize as one segment.

**`run_complete`** — the run is done (PR ready, panel + fix loop finished, Discussion
comment posted; the wrangler deletes the state file at `done`). In order:

1. Print the final banner by piping the checkpoint to the bundled script — via a temp
   file, never shell-quoted inline (the JSON routinely contains `'` and `$`):
   ```bash
   cat > "${CLAUDE_JOB_DIR:-/tmp}/imps-run-complete.json" <<'CHECKPOINT_JSON'
   <the run_complete JSON verbatim>
   CHECKPOINT_JSON
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/final-banner.py" < "${CLAUDE_JOB_DIR:-/tmp}/imps-run-complete.json"
   ```
   Then the results from the checkpoint fields:
   ```
     merged:    #6 <label>    (3 files)
     published: #3 Discussion → https://github.com/...
     verdicts:  solution-architect APPROVE · grumpy-engineer APPROVE · ...
     PR:        <url, "ready for review"> | "no PR — branch is local"
   ```
   Render `run_stats` as a short stats block (Achieved / Decision points / Timing /
   Imps / Tokens — omit empty sections). If `findings_inline` is populated (`PR: no`)
   or `unresolved` lists blockers/majors that survived 3 rounds, surface them verbatim —
   they are the review record.
2. If `prs_monitor` is non-null: invoke the `/imps:prs` skill (no args — it reads the
   `.prs.json` the wrangler already wrote), then print:
   `PR monitor active — watching PR #<N>. I'll address comments, fix CI failures, and
   resolve merge conflicts automatically.`
   If `pr` is null, print instead: "Branch is local only and no PR was opened — push
   and open a PR, then invoke `/imps:prs` to activate the monitor."
3. **Learnings gate.** If `learnings_candidates` is non-empty, present them with
   **AskUserQuestion** (`multiSelect: true`):
   - **question**: `"Any of these worth saving as a learning?"`
   - **header**: `"Learnings"`
   - **options**: one option per candidate (each already phrased as a rule to apply
     next time)

   If any were confirmed, immediately follow with a second **AskUserQuestion**
   (`multiSelect: true`):
   - **question**: `"Which of these are project-specific? (the rest will be saved globally)"`
   - **header**: `"Scope"`
   - **options**: one option per confirmed learning (same text)

   Relay the outcome verbatim as one message:
   `learnings: [{"rule": "<text>", "scope": "project"}, {"rule": "<text>", "scope": "user"}]`
   — or `learnings: none` if nothing was confirmed (or there were no candidates; still
   send it so the wrangler can close out).

**`done`** — the wrangler wrote the learnings files. Print the closing line:
```
Learnings saved: "<rule 1>" [project] · "<rule 2>" [user]
```
(or `No learnings saved this run.`). The run is over.

---

## Model selection reference

Assign by reasoning complexity, not duration or volume:
mechanical (deterministic output, no judgment) → haiku ·
judgment (context, decisions, synthesis) → sonnet ·
deep judgment (large decision space, architectural tradeoffs) → opus.
Always set `model:` explicitly on every `agent()` call.

Model IDs (`claude-*`) vary by session — read the exact identifiers from the session's
model table rather than hardcoding them. The `<haiku|sonnet|opus model id>` placeholders
in the prompts above stand for those current IDs.

---

## Constraints

- Never hand over to the wrangler without explicit approval of the task list
  (`ExitPlanMode` is that gate).
- Never `git merge --force`, `git reset --hard`, or `git push` without explicit user
  instruction — **exceptions**: (1) after plan approval the Imp Wrangler dispatches
  the imps, rebases the working branch, and merges imp branches autonomously, and it
  pushes + opens the endstate PR only after the operator's `Push & open PR` answer is
  relayed to it (pushing fix-loop commits to that same PR branch); (2) the `/imps:prs`
  PR monitor pushes fix commits to the PR branch autonomously once activated.
- Never create GitHub PRs without user instruction — the Push & PR gate in Phase 4 is
  that instruction for the endstate PR.
- If a task touches a production system, pause and confirm before that task runs.
- The wrangler owns the run state file and `.prs.json` from handover onward; this
  session's last state-file write is Phase 2 Step 6.
