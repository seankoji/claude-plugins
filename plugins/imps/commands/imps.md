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

You are a senior engineering orchestrator. Your job is to convert a vague task into discrete
parallel agents (imps), dispatch them with right-sized models, monitor their progress, and
integrate results cleanly.

## Context discipline (applies to every phase)

The main session holds **decisions, not data**. Its context is re-read every turn, so
anything bulky that enters it is paid for again and again:

- **Pass artifacts by reference.** Agents have their own tools — hand them a file path
  or a command to run (`git diff ...`), never pasted file contents, diffs, or logs.
- **Delegate noisy work.** Repo recon, gate runs, merge mechanics, log trawls → spawn a
  subagent and keep only its conclusions. Grep sweeps and lookups go to `scout`.
- **Compact returns only.** Every agent's final message is a tight JSON contract or a
  few lines of findings — never raw command output.
- If a tool result would be long (test runs, builds), redirect to a file and read the tail.

---

## Mode detection

`/imps:imps` has **four modes**, checked in this order:

- **Checklist-file mode** — `$ARGUMENTS` is a single token ending in `.md`. Resolve the
  file in order: (1) as-is if it's an absolute path or exists relative to cwd, (2)
  `~/.claude/$ARGUMENTS`, (3) `$CLAUDE_PROJECT_DIR/$ARGUMENTS`. If any resolution
  succeeds (`test -f`), treat the file as an audit checklist and **→ skip all phases
  below; follow the [Checklist-file mode](#checklist-file-mode) section instead.**
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
  (e.g. `https://github.com/seankoji-com/onebitemore/discussions/284`, also matching a
  permalink-to-comment or `?sort=` suffix), or the two-token bare form matching
  `^discussion:?\s*#?\d+$` (case-insensitive, resolved against the current repo, e.g.
  `discussion 284` or `discussion #284`). Discussions live in a different GitHub API/ID
  space than Issues (GraphQL only, no REST) — this is why a discussion reference needs
  its own detection branch instead of falling into issue-driven mode. **→ Fetch the
  discussion, then continue as free-text mode with the discussion as the seeded task**
  — see [Discussion-seed mode](#discussion-seed-mode) below.

- **Free-text mode** — `$ARGUMENTS` is a task description (anything that is not purely
  issue numbers or a discussion reference), or empty. This is the original `/imps:imps`
  behaviour. **→ Continue with the phases below.**

Detection order: (1) single `.md` token that resolves to a file → checklist-file mode.
(2) non-empty AND every token matches `^#?\d+$` → issue-driven mode. (3) the whole
argument is a Discussion URL or bare `discussion N` reference → discussion-seed mode.
(4) everything else → free-text mode.

---

## Discussion-seed mode

*Only entered when Mode detection chose this branch. This is not a separate phase
sequence like issue-driven mode — it's a fetch step that seeds free-text mode (Phase 0
onward) with the discussion's content, then adds one obligation that survives to Phase 6.*

**1. Resolve owner/repo/number.**
- Full URL → strip any `?query` or `#fragment` first, then parse `owner`, `repo`, `number`
  from the remaining path segments (a permalink like `.../discussions/284#discussioncomment-98765`
  still resolves to discussion `284`).
- Bare `discussion N` / `discussion #N` → resolve `owner/repo` from the current repo:
  `gh repo view --json nameWithOwner -q .nameWithOwner`.

**2. Fetch the discussion via GraphQL** (Discussions have no REST endpoint — this
mirrors the `publish`-type convention already used for Discussion creation in
`agents/imp.md`):

```bash
gh api graphql -f query='
query($owner:String!,$repo:String!,$num:Int!){
  repository(owner:$owner,name:$repo){
    discussion(number:$num){
      id title body url
      category { name }
      author { login }
      comments(first:20){ nodes { body author { login } isAnswer } }
    }
  }
}' -f owner="<owner>" -f repo="<repo>" -F num=<number>
```

Extract `id` (the GraphQL node ID — required later to post a reply, keep it verbatim,
never re-derive it from the number), `title`, `body`, `url`, `category.name`, and the
comment bodies.

**3. Seed the task.** Build `<DISCUSSION_TASK_SEED>` from the title + body (+ any
comments that add requirements or constraints — skip pure "+1"/social replies). This
replaces `$ARGUMENTS` as the input to Phase 0's brief refinement: pass
`<DISCUSSION_TASK_SEED>` to the `prompt-builder` skill instead of raw `$ARGUMENTS`, and
skip the "What's the task?" prompt — the discussion body *is* the task description.
Continue with Phase 0 onward exactly as free-text mode does from here.

**4. Record the source.** Carry `owner`, `repo`, `number`, `id` (GraphQL node ID), and
`url` as `source_discussion` in the durable state file (Phase 2 Step 6 / Phase 3 Step 2)
so a `/clear` mid-run doesn't lose the reply target.

**5. Mandatory reply obligation.** Regardless of what Phase 1 discovery answers for
"expected output artifacts," a discussion-seeded run posts one summary comment back to
the source discussion whenever Phase 6 Finalize is reached — this is not optional there
and is not a dispatched task (it needs the finished run's outcome, which doesn't exist
until Phase 5 completes). Phase 1 Q2 may still surface additional artifacts (PRs, code)
on top of this. **This obligation cannot fire if the run never reaches Phase 6** — an
early abort (Phase 5 Step 1's blocked-task pause, or Step 3's `abort` checkpoint) stops
before Finalize; see Phase 5 Step 1 and Step 3 for the "post a short abort notice instead"
handling this requires.

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
   <a file path to Read, or a command to run — e.g.
    "Read <repo-root>/GOAL.md" or
    "Run: git diff origin/<default-branch>..HEAD -- ':!*lock*' ':!dist'">

   Argue AGAINST this. Find wrong task boundaries, mis-routed models, missing deps,
   correctness bugs, unsafe assumptions, gaps in the DoD. Steelman the case that this
   should NOT ship. Return a list of findings (blocker | major | minor | nit), then a
   one-line VERDICT: APPROVE | CHANGES_REQUESTED.`,
  { model: '<opus model id>', label: 'head-imp' }
)
```

**Phase 2 (plan review):** pass the absolute path of GOAL.md — the Head Imp Reads it.
**Phase 5 (diff review):** performed by the Imp Wrangler, which passes the diff
*command* (`git diff origin/<default-branch>..HEAD -- ':!*lock*' ':!dist'`) — the Head
Imp runs it itself. Never capture a diff just to paste it into a prompt.

Inline content is acceptable only for artifacts too small to matter (≲50 lines) or ones
that exist nowhere on disk (e.g. a mid-task design question from an imp).

**Imps may also consult the Head Imp** mid-task when they hit an ambiguous decision,
correctness risk, or a cross-cutting change they're unsure about. Pass the relevant
context (the specific file, function, or design choice) as the artifact. One consultation
per blocking question — don't use it as a rubber-stamp.

---

## Guard: resume check

**This check fires on every invocation — including when `$ARGUMENTS` is empty.** An empty invocation does NOT mean "start fresh" — it means the user may have cleared context between plan and dispatch. Always run the guard before Phase 0.

Before anything else:
1. Derive the project slug: `basename "${CLAUDE_PROJECT_DIR:-$(pwd)}"`
2. Check whether `~/.claude/imps/runs/<slug>.json` exists.

State files from other projects are independent — only the current project's file matters.
Archived files (`<slug>.archived-*.json`, see **New** below) are not "the current project's
file" for this check — only the canonical `<slug>.json` triggers the guard.

If the current project's file exists, read it and check the `phase` field.
If the `phase` field is absent (written by an older revision), treat it as `"dispatched"`.
Also check whether the run described in the file looks unrelated to what the user is
asking for now (different task, different feature area) — a stale run from a past,
finished task is the common case this guard exists for, not just a same-task interruption.

**Case A — `phase: "dispatch_pending"` (plan approved, not yet dispatched):**

Print a one-block summary:
```
  Plan ready — not yet dispatched
  Task: <task (first 80 chars)>
  Branch: <branch>  ·  Poll: <poll_interval_seconds>s
  Tasks:  #1 <glyph> <label>  [<model short> · <type>]
          #2 <glyph> <label>  [<model short> · <type>]
          ...
```

- **Resume** — use the `tasks` array from the state file (it is the authoritative
  source — GOAL.md is human-readable but the state file is what Phase 3's banner
  reads). Verify `git rev-parse --abbrev-ref HEAD` matches state `branch`;
  warn the user if it doesn't and wait for confirmation before continuing. Ground the
  Workflow prompts via `scout` subagents (the prior planning context was cleared) rather
  than re-reading repo files into this context.
  Skip Phases 0/1/2 entirely, jump straight to **Phase 3 dispatch**
  (rebase → update state → launch Workflow).
- **New** — start the task the user is asking for now, and leave the existing run
  completely alone: do NOT delete, edit, or touch `~/.claude/imps/runs/<slug>.json` in
  any way. Instead, move it out of the canonical slot so it stops colliding with the new
  run: `mv ~/.claude/imps/runs/<slug>.json ~/.claude/imps/runs/<slug>.archived-$(date +%Y%m%dT%H%M%S).json`.
  This is a rename, not an edit — the archived file is byte-for-byte the old state, so
  nothing about the old run is lost; if the user later wants it back they can `mv` it
  back to `<slug>.json` and re-invoke `/imps` to resume it. Then proceed through Phases
  0–2 normally for the new task.
- **Abandon** — delete `~/.claude/imps/runs/<slug>.json` and start fresh

**Case B — `phase: "dispatched"` or absent (workflow running, completed, or pre-change file):**

Print a one-block summary:
```
  Existing run — <task (first 80 chars)>
  Branch: <branch>  ·  Dispatched: <dispatched_at first 16 chars>  ·  Workflow: <workflow_run_id or "pending">
  Tasks:  #1 <glyph> <label>  [<model short> · <type>]
          #2 <glyph> <label>  [<model short> · <type>]
          ...
```

- **Resume** — skip discovery and re-enter Phase 3 dispatch against the existing run
  (progress visible via `/workflows`)
- **New** — start the task the user is asking for now, and leave the existing run
  completely alone: do NOT delete, edit, or touch `~/.claude/imps/runs/<slug>.json` in
  any way. Instead, move it out of the canonical slot so it stops colliding with the new
  run: `mv ~/.claude/imps/runs/<slug>.json ~/.claude/imps/runs/<slug>.archived-$(date +%Y%m%dT%H%M%S).json`.
  This is a rename, not an edit — the archived file is byte-for-byte the old state, so
  nothing about the old run is lost; if the user later wants it back they can `mv` it
  back to `<slug>.json` and re-invoke `/imps` to resume it. Its Workflow run (if still
  active) keeps running independently in the background either way — this only affects
  which state file `/imps` tracks as canonical. Then proceed through Phases 0–2 normally
  for the new task.
- **Abandon** — delete `~/.claude/imps/runs/<slug>.json` and start fresh

Do not proceed past this check without an answer.

---

## Checklist-file mode

*Only entered when Mode detection chose this branch — a single `.md` arg that resolved
to an existing file. Do not run Phase 0–7 below.*

**1. Read the checklist file.**
The file path was resolved during mode detection. Read its full contents. Parse every
line matching `- [ ] ` (unchecked) or `- [x] ` (already checked) as a checklist item.
For each unchecked item, extract:
- The claim text (the line itself, stripped of the checkbox prefix)
- The `Verify:` sub-line (one line immediately following, or a labelled sub-bullet)
- The `Done when:` sub-line (same structure)

Items missing either `Verify:` or `Done when:` are surfaced as a parsing warning and
skipped; do not fabricate criteria.

**2. Confirm the resolved file with the operator.**
Print the resolved file path and item count:
```
Resolved checklist: <absolute-path>
Items to verify (unchecked): N
```
Ask: "Proceed with running these N verification commands?" Wait for confirmation.
Do not proceed to step 3 without an explicit yes — this is the gate that prevents
arbitrary shell execution from an unexpected file match.

**3. Build a query-only task table (Type=`query`).**
Create a GOAL.md in the repo root using the standard spine format (see Phase 2), with:
- Task = each unchecked checklist item (label = first 60 chars of the claim)
- Model = haiku for shell/grep checks; sonnet for items marked `[JUDGMENT — sonnet]`
- Type = `query` for all (read-only; no code changes)
- Depends-on = `—` unless one item's `Verify` step depends on a prior item's output

**4. Dispatch verification imps.**
For each task, spawn a `query` imp (haiku or sonnet, worktree-isolated=false) that:
- Runs the `Verify:` command(s)
- Evaluates output against `Done when:`
- Returns `PASS` or `FAIL: <reason>` (one line each)

Fan out in parallel where `Depends-on = —`. Collect results.

**5. Emit the audit report.**
Print a structured summary:

```
## Audit — <filename> — <date>

### Passed ✅ (<N>)
- [x] <item claim>

### Failed ❌ (<N>)
- [ ] <item claim>
      Result: <reason from imp>

### Skipped ⚠️ (<N>) — missing Verify/Done-when
- [ ] <item claim>
```

**6. Offer remediation dispatch.**
If any items FAILED, ask the operator:
> "N items failed. Dispatch remediation imps (code/publish tasks) for all, some, or none?"

- **All / specific selection** → add them as `code` or `publish` tasks to the existing
  GOAL.md and dispatch using the Phase 3 workflow (model-routed, worktree-isolated for
  code changes).
- **None** → stop here. The audit report is the deliverable.

Do NOT auto-dispatch fixes without operator confirmation. Default is read-only.

---

## Phase 0 — Brief refinement

Before asking discovery questions, invoke the `prompt-builder` skill to sharpen the task brief (if installed). A well-refined brief reduces decomposition ambiguity and often pre-answers several Phase 1 questions. If `prompt-builder` is not available, refine the brief inline to 1–2 sharp sentences and continue.

**Discussion-seed mode:** skip the "What's the task?" prompt entirely — use
`<DISCUSSION_TASK_SEED>` (built in [Discussion-seed mode](#discussion-seed-mode)) as the
raw material below instead of `$ARGUMENTS`.

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
   to the discussion's own repo (`owner/repo` resolved in step 1) and skip asking unless
   the discussion implies a different target repo.
2. What concrete output artifacts are expected? Be specific — e.g. Bash scripts, GitHub Discussion post, PR, code changes. In discussion-seed mode, a reply comment on the source discussion is posted automatically at Phase 6 regardless of the answer here (see [Discussion-seed mode](#discussion-seed-mode) step 5) — this question is only for artifacts *beyond* that reply.
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
quote or reason about its contents. The planning window this leaves behind is what the
dispatch phase inherits — keep it lean. Then:

- Break the work into discrete, atomic tasks. Each task has one clearly-stated output
  and is independently completable.
- For each task assign:
  - **Model** — assign by reasoning complexity (see
    [Model selection reference](#model-selection-reference)). Always set `model:` explicitly.
  - **Type** — `code` (file changes, worktree-isolated) · `query` (read-only) ·
    `publish` (GitHub artifacts; use `gh api graphql` for Discussions, not REST)
  - **Depends-on** — prerequisite task IDs, or `—` if independent

**Step 2:** Write **`GOAL.md`** to the repo root with this structure:

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
Planned — dispatching now.
```

Discussion-seed mode: add `- [ ] Outcome comment posted to the source Discussion` to the
Definition of Done — Phase 6 fulfills this automatically, it is not a dispatched task.
If the run later aborts before reaching Phase 6 Finalize (see Phase 5 Step 1/Step 3),
this box is expected to stay unchecked — note that in the Status section rather than
treating it as a bug.
Add `- [ ] CI green on the PR` to the Definition of Done **only if this run will open a
PR** (see Phase 5 Step 4 — the endstate PR is the default for runs that produce code
changes). Omit it for query/publish-only runs that create no PR, or it stays permanently
unresolvable.

This file is the `/compact`-durable human-readable spine. The JSON state file
(`~/.claude/imps/runs/<slug>.json`, written in Step 6) is the **authoritative** task
table — Phase 3 reads from it, not from GOAL.md. If you hand-edit
GOAL.md's task table after approval, mirror the change into the state file (or re-run
planning) or it will not take effect. Update the Status section at each major milestone.

**Step 3 — Head Imp review (mandatory):**
Before calling `ExitPlanMode`, summon the Head Imp (see the Head Imp section above).
Pass the **absolute path** of `GOAL.md` as the artifact — the Head Imp Reads it itself.
The Head Imp argues AGAINST the plan — wrong boundaries, mis-routed models, missing
deps, gaps in the DoD. Fix what the critique exposes before proceeding.

**Step 4:** Call **`ExitPlanMode`** — this IS the approval gate (replaces the old
Go / Edit / Abandon prompt). If the user requests changes, stay in plan mode and revise
`GOAL.md`; when approved, proceed to Phase 3.

**Step 5:** Set `poll_interval_seconds: 300` (5-minute default — no user prompt needed).

**Step 6:** Write the durable state file **now** — the task table is final in `GOAL.md`
and the poll interval was just captured. This makes the run resumable across a `/clear`.

Derive the slug, ensure the directory exists, and write to `~/.claude/imps/runs/${SLUG}.json`:
```sh
mkdir -p ~/.claude/imps/runs
SLUG=$(basename "${CLAUDE_PROJECT_DIR:-$(pwd)}")
```
```json
{
  "task": "<REFINED_TASK>",
  "repo": "<repo from discovery>",
  "branch": "<current branch>",
  "tasks": [
    { "id": 1, "label": "...", "model": "haiku", "type": "query", "deps": [] }
  ],
  "phase": "dispatch_pending",
  "workflow_task_id": null,
  "workflow_run_id": null,
  "workflow_output_file": null,
  "dispatched_at": null,
  "poll_interval_seconds": 300,
  "source_discussion": null
}
```

Discussion-seed mode: set `source_discussion` to
`{ "owner": "...", "repo": "...", "number": <int>, "id": "<GraphQL node ID>", "url": "<discussion URL>" }`
(fields fetched in [Discussion-seed mode](#discussion-seed-mode) step 2). Every other
mode leaves it `null`.

Then print this handoff prompt (verbatim — it is informational, not a question):

```
Plan approved and durable in GOAL.md + state file.

  Recommended: /clear  →  /imps:imps   (dispatches from a clean context)
  Dispatch inherits whatever the planning window holds. With recon delegated
  to scouts it should be lean — but /clear is still the clean cut for large
  plans. After /clear, ground the Workflow prompts via scout subagents, not
  by re-reading files into this context.

  Or just reply here to continue dispatching without clearing.
```

---

## Phase 3 — Dispatch

On approval:

**Step 1:** Capture the current branch and rebase onto main/master:
```sh
git rev-parse --abbrev-ref HEAD
```

Determine the default branch (`main` or `master`) by checking which exists on origin:
```sh
git remote show origin | grep 'HEAD branch'
```

Then rebase:
```sh
git fetch origin && git rebase origin/<default-branch>
```

If the rebase fails (conflicts), stop and tell the user what conflicted. Do not proceed to
dispatch until the working tree is clean and rebased.

**Step 2:** Update the state file written in Phase 2 Step 6. The file already exists when
following the normal path (Phase 2 wrote it) or the Case A resume path (the file was the
trigger for Case A). The create-from-scratch fallback only applies to in-session continues
where Phase 2 Step 6 was somehow skipped — **not** after `/clear` (the file must already
exist for Case A to fire; if it doesn't, the guard sends you down the fresh-start path).

```sh
mkdir -p ~/.claude/imps/runs
SLUG=$(basename "${CLAUDE_PROJECT_DIR:-$(pwd)}")
# STATE_FILE is exactly: ~/.claude/imps/runs/${SLUG}.json
```

Update (or create) `~/.claude/imps/runs/${SLUG}.json`: set `phase` → `"dispatched"` and
`dispatched_at` → current ISO timestamp (`date -u +%Y-%m-%dT%H:%M:%SZ`). Leave `task`,
`repo`, `branch`, `tasks`, `poll_interval_seconds`, and `source_discussion` intact from
Phase 2. Leave `workflow_task_id` and `workflow_run_id` null — they are filled in Step 4.

Full schema for the in-session create-from-scratch fallback (all values are in context):
```json
{
  "task": "<REFINED_TASK>",
  "repo": "<repo from discovery>",
  "branch": "<current branch>",
  "tasks": [
    { "id": 1, "label": "...", "model": "haiku", "type": "query", "deps": [] }
  ],
  "phase": "dispatched",
  "workflow_task_id": null,
  "workflow_run_id": null,
  "workflow_output_file": null,
  "dispatched_at": "<ISO timestamp from Bash: date -u +%Y-%m-%dT%H:%M:%SZ>",
  "poll_interval_seconds": 300,
  "source_discussion": null
}
```
This fallback only fires when Phase 2 Step 6 was skipped in-session — `source_discussion`
is recoverable from context in that case exactly like the other fields; set it from
[Discussion-seed mode](#discussion-seed-mode) step 2 if this run is discussion-seeded,
else `null`.
Imps are unnamed — each one is identified by a themed Nerd Font glyph derived from its task ID (see the dispatch banner in Step 5), so the state file carries no `name` field.

**Step 3:** Write and launch a **Workflow** that implements the full dependency graph in a
single call. The Workflow tool is explicitly authorized for this command.

Rules for the workflow script:
- Topologically sort tasks into stages; implement as `pipeline()` stages with inner `parallel()` for tasks that share a stage but have no mutual dependency.
- Every agent uses the `imp` agent type: `agent(..., { agentType: 'imp' })` — this bakes in atomic-task discipline, correct branch handling for publish tasks, and structured output conventions.
- **Agent-type fallback**: If a workflow agent call errors with an agent-type registration failure, the `imp` type may not be registered in this session. Change `agentType: 'imp'` to `agentType: 'general-purpose'` in the workflow script and re-run.
- Every `code`-type task adds `isolation: 'worktree'`: `agent(..., { agentType: 'imp', isolation: 'worktree' })`
- **Worktree base**: `isolation: 'worktree'` always creates the agent's worktree from the repo's last committed HEAD on the **default branch** — NOT the caller's working branch. Committing in-progress changes to a *side* working branch therefore does NOT make them visible to the worktree. If `code` tasks must see in-progress changes, those changes must first reach the default branch itself (merge or push them to the default branch before dispatch); committing to a non-default branch is not enough.
- **Gate before commit**: every `code` agent resolves the repo's gate/lint commands (from `package.json` scripts, `Makefile`, `pyproject.toml`, CI config, or `AGENTS.md`/`CONTRIBUTING.md`) and runs them — plus the autofix command if one exists — before committing. It fixes failures it caused and leaves pre-existing failures noted. This mirrors issue-mode's per-agent `GATE_CMDS`/`LINT_FIX` discipline so agents never push gate-red (Phase 5 Step 3's aggregate gates are a backstop, not the first line).
- Apply model routing per assignment above (see [Model selection reference](#model-selection-reference)): `agent(..., { agentType: 'imp', model: '<haiku|sonnet|opus model id from the session model table>' })`
- Use `log()` to emit progress markers. Format **must** be: `log('imp:start #N')` when starting task N, `log('imp:done #N')` when task N completes. The integer N **must exactly match** the `id` field of the corresponding task in the state file — never combine multiple state-file tasks into one agent or split one task across agents. One agent = one task ID. These markers are debug breadcrumbs — no automated consumer reads them; mismatches only mislead a human manually tailing `workflow_output_file`.
- Never create GitHub PRs from inside the workflow. PRs should be deferred to Phase 5, created from the main worktree branch after merge — not from isolated worktree branches whose names are non-deterministic.
- Every agent returns structured output via `schema`. `status` is an enum —
  `"done"` (task completed) or `"failed"` (the agent could not complete it):
  ```json
  { "id": 1, "label": "...", "type": "query", "status": "done|failed", "branch": null, "artifacts": [], "notes": "if failed, why (≤50 words)" }
  ```
  A `code` agent that fails (unresolvable error, gates it cannot get green) returns
  `"status": "failed"` with a `notes` reason and leaves its branch unmerged — Phase 5
  Step 1 surfaces failed tasks to the user and does NOT merge them.
- The workflow's final `return` must be:
  ```json
  {
    "completed": [{ "id": 1, "label": "...", "type": "query", "status": "done" }],
    "worktrees": { "6": "<branch-name>", "7": "<branch-name>" },
    "artifacts": [{ "id": 3, "url": "https://github.com/..." }],
    "tokens_spent": 12345,
    "model_counts": { "haiku": 3, "sonnet": 2, "opus": 1 }
  }
  ```
  Set `tokens_spent` to `budget.spent()` and `model_counts` by tallying the `model`
  field from each agent's structured output.

**Step 4:** Update `~/.claude/imps/runs/${SLUG}.json` with the identifiers returned by
the Workflow tool — the tool result contains:
- `workflow_task_id`: the task ID (`wp...`) — the `TaskOutput` lookup key
- `workflow_run_id`: the run ID (`wf_...`) — used by the `/workflows` UI
- `workflow_output_file`: the background task's **output file path**, if the tool result
  includes one (background tasks report where their output streams to). Record it
  verbatim; leave `null` if absent.

No automated process reads these back — they're captured for manual debugging only:
`workflow_output_file` can be grepped by hand for `imp:start`/`imp:done` markers without
ingesting the full log, `workflow_task_id` works with the session-scoped `TaskOutput`
tool, and `workflow_run_id` is what you'd look up in the `/workflows` UI.

**Step 5:** Print the dispatch banner by running this script (reads from the state file
written in Step 2; substitute `$SLUG` with the actual slug):

```bash
SLUG=$(basename "${CLAUDE_PROJECT_DIR:-$(pwd)}") python3 - <<'PYEOF'
import os, json, sys

slug      = os.environ['SLUG']
state_dir = os.path.expanduser('~/.claude/imps/runs')
with open(os.path.join(state_dir, f'{slug}.json')) as f:
    state = json.load(f)

tasks  = state['tasks']
wf     = state.get('workflow_run_id') or 'pending'
n      = len(tasks)
# imp/spirit/daemon-themed Nerd Font glyphs, assigned by task id (cycling)
# ghost · skull · devil · bat · spider · skull-crossbones · grave-stone · coffin
IMPS   = ['\U000F02A0','\U000F068C','\U000F0556','\U000F0B5F',
          '\U000F11D5','\U000F0680','\U000F0BAB','\U000F1322']
ATTY   = sys.stdout.isatty()
RST    = '\033[0m' if ATTY else ''
PINK   = '\033[38;5;211m' if ATTY else ''   # opus
YELLOW = '\033[93m'       if ATTY else ''   # sonnet
GREEN  = '\033[92m'       if ATTY else ''   # haiku / default

def model_color(m):
    m = (m or '').lower()
    if 'opus' in m:   return PINK
    if 'sonnet' in m: return YELLOW
    return GREEN

def colored_imp(t):
    idx = (t['id'] - 1) % len(IMPS)
    return f'{model_color(t.get("model",""))}{IMPS[idx]}{RST}'

bats = '  '.join(colored_imp(t) for t in tasks)
print(f'  {bats}  {n} imps dispatched')
for t in tasks:
    deps    = t.get('deps', [])
    dep_str = '  waits: ' + ', '.join(f'#{d}' for d in deps) if deps else ''
    label   = t.get('label', '')
    model   = t.get('model', '?').split('-')[1] if '-' in t.get('model','') else t.get('model','?')
    typ     = t.get('type', '?')
    print(f'  {colored_imp(t)}  #{t["id"]}  {label}  [{model} · {typ}{dep_str}]')
print()
print(f'Workflow: {wf}  ·  progress in /workflows  ·  type anything to keep working')
PYEOF
```

**Step 6:** Return control to the user. Do not block — the Workflow runs in the
background (progress visible via `/workflows`) and the merge phase is handled when
its task notification arrives (see Phase 5 below). There is no automated hang
detector for this phase — if the notification never arrives, check `/workflows`
for a stalled run.

---

> **Phase 4** is intentionally not a separate section here. The numbering is kept aligned
> with `commands/issue-mode.md`, where Phase 4 is the persona panel — in free-text mode
> that panel runs inside the Imp Wrangler's Segment B (Phase 5 Step 5).

## Phase 5 — Integration via the Imp Wrangler (triggered by task notification)

When the Workflow's `<task-notification>` arrives, this is your cue. This session is the
sole integration owner — but it owns *decisions*, not mechanics: the merge, Head Imp diff
review, gates, persona panel, and fix loop all run
inside one **imp-wrangler** subagent (see `agents/imp-wrangler.md` for its full protocol)
so that merge output, diffs, and gate logs never enter this context. Only compact JSON
checkpoints come back; this session handles the operator gates between them.

**Step 1 — Triage the workflow result:**
Read the workflow result from the notification (it is already compact — fetch nothing
else). Any task with `"status": "failed"` is never merged. If a failed task blocks the
run's acceptance criteria, pause and ask the user how to proceed (retry, skip, or abort)
before spawning the wrangler — do not silently integrate a partial result set.

Before proceeding, pull two scalar fields out of the state file into context now — this
is a cheap `jq` read, not the "fetch nothing else" bulk data the rule above guards
against: `source_discussion` and `poll_interval_seconds`. Hold onto both for the rest of
Phase 5/6 — `source_discussion` is the reply target for Phase 6 item 6 and the abort
notice below; `poll_interval_seconds` is needed by Phase 6 item 7a. Both are read now
because item 3 of Phase 6 deletes the state file before either is otherwise needed.

**If the user chooses abort here and `source_discussion` is set:** post a short comment
to the source discussion before stopping — "Run aborted: `<one-line reason>`. No changes
were merged." via the same `addDiscussionComment` mutation used in Phase 6 item 6. Then
stop; do not spawn the wrangler.

**Step 2 — Spawn the Imp Wrangler (Segment A: merge → Head Imp review → gates):**
Load SendMessage first (`ToolSearch: "select:SendMessage"`) — every checkpoint after the
spawn is answered through it, and the wrangler keeps its context across resumes.

Spawn synchronously via the Agent tool:

```
Agent(
  subagent_type: 'imp-wrangler',
  prompt: `Run Segment A per your brief.
    State file: ~/.claude/imps/runs/<slug>.json
    Workflow result: <the compact result JSON from the notification>
    Default branch: <default-branch>
    Persona briefs: <absolute paths of the four code-persona files and
                     ux-designer.md, resolved from ${CLAUDE_PLUGIN_ROOT}/personas/>`
)
```

Keep the `agentId` from the spawn result — Steps 3 and 5 resume this same wrangler.

**Agent-type fallback:** if `imp-wrangler` is not registered in this session, spawn
`general-purpose` with the full body of `agents/imp-wrangler.md` prepended to the
prompt. If subagents are unavailable entirely, execute that file's protocol inline in
this session (same steps, no offload) and note the degradation.

**Wrangler death mid-segment:** if SendMessage to the wrangler errors, or it returns
malformed / non-JSON output, do not guess at the tree state — re-spawn a fresh
`imp-wrangler` on the same segment. Segments are idempotent by design: already-merged
branches no-op, the Head Imp review and gates simply re-run. If the re-spawn also
fails, fall back to executing the protocol inline.

**Step 3 — Answer checkpoints until `gates_green`:**
Each wrangler segment ends in exactly one JSON checkpoint. Respond via `SendMessage` to
the wrangler's agentId:
- `blocked · merge_conflict` — the conflict is live in the shared working tree. List the
  branch + files for the user and let them resolve (or resolve trivial conflicts
  yourself), then send `resolved, continue`.
- `blocked · gate_red` — surface the gate name + log tail to the user; agree the next
  step (guidance for another fix attempt, skip, abort) and relay it.
- `blocked · branch_mismatch` — reconcile branch state with the user before resuming.

  The wrangler's brief defines the resume verbs it understands (`resolved, continue` ·
  `retry <gate>: <guidance>` · `skip <gate>` · `reconciled, continue` · `abort`) — use
  them verbatim. If the user chooses abort, send `abort`; the wrangler returns an
  `aborted` checkpoint describing the tree state — surface it and stop Phase 5 (leave
  the state file for a later resume decision). If `source_discussion` was captured in
  Step 1, post the same short "Run aborted: `<reason>`" comment described there before
  stopping — this is the other early-exit path that would otherwise skip the discussion
  reply obligation entirely.
- `gates_green` — print a one-block summary from the checkpoint fields (merged tasks,
  failed tasks, Head Imp verdict + amendment count, gate results, diff stat), then go
  to Step 4.

**Step 4 — Endstate PR decision (operator gate):**
The persona panel posts its findings as comments on a PR thread, so the PR must exist
first — mirroring `issue-mode.md`, where the integration PR opens before the panel.
This is the correct moment: branches are merged, the Head Imp reviewed the diff, gates
are green — and nothing has been pushed yet (Segment A is entirely local).

Ask the user with **AskUserQuestion**:
- **question**: `"Push this branch and open the endstate PR for review?"`
- **header**: `"Push & PR?"`
- **options**:
  1. `Push & open PR` — the wrangler pushes the branch and opens a draft PR (Step 6
     flips it to ready). That PR thread is where the persona panel comments. Add
     `- [ ] CI green on the PR` to the GOAL.md DoD (pending; `/imps:prs`, activated in
     Step 6, tracks it).
  2. `Not yet` — no push, no PR. The persona panel returns its findings in the final
     checkpoint (`findings_inline`) instead of on a PR thread; the branch stays local
     and no PR monitor starts. Do not add a `CI green on the PR` DoD line (there is
     no PR).

Opening the endstate PR is the default for free-text runs that produced code changes —
only `Not yet` skips it.

**Step 5 — Relay the decision (Segment B: PR + persona panel + fix loop):**
`SendMessage` the wrangler exactly `PR: yes` or `PR: no`. The wrangler then, per its
brief:
- pushes and opens the draft PR (`PR: yes` only) — personas post their findings there
  per `commands/issue-mode.md § Personas → Posting identity` (dedicated GitHub App
  identity by default, orchestrator-identity `[Persona: <Name>]` comment as fallback);
- runs the persona panel following `commands/issue-mode.md § Phase 4` (the canonical
  protocol — four opus code personas always; the sonnet collector + browser personas
  only when a UI surface and a browser transport exist);
- runs the fix loop (max 3 rounds — disjoint findings → parallel sonnet fixers,
  cross-cutting → one opus fixer), pushing each round's fix commits to the PR branch;
- returns the `final` checkpoint: PR URL + number, verdict tally, fix rounds, unresolved
  findings, diff stats — plus `findings_inline` when no PR was opened.

Print the verdict tally and update the GOAL.md Status section from the checkpoint. If
`findings_inline` is populated (`PR: no`) or `unresolved` lists blockers/majors that
survived 3 rounds, surface them to the user verbatim — they are the review record.

**Step 6 — Finalize:**
1. If the run opened a draft PR (the `final` checkpoint's `pr` field is non-null), flip
   it to ready for review now (`gh pr ready <N>`, number from the checkpoint). If the
   user chose `Not yet`, there is no PR — skip this item.
2. Print artifact links for `publish`-type tasks (Discussions, comments, etc.):
   ```
     󰭟 #3 Discussion → https://github.com/...
     󰭟 #5 Comment    → https://github.com/...
   ```
3. Delete `~/.claude/imps/runs/${SLUG}.json` (the same path written in Phase 3 Step 2).
4. Print the final banner. Run this script for the header line (substitute `TASKS_JSON`
   with the JSON array of all tasks, each with `id` and `model` fields):

```bash
TASKS_JSON='[{"id":1,"model":"claude-haiku-..."}]' python3 - <<'PYEOF'
import os, json, sys

tasks  = json.loads(os.environ['TASKS_JSON'])
n      = len(tasks)
# imp/spirit/daemon-themed Nerd Font glyphs, assigned by task id (cycling)
# ghost · skull · devil · bat · spider · skull-crossbones · grave-stone · coffin
IMPS   = ['\U000F02A0','\U000F068C','\U000F0556','\U000F0B5F',
          '\U000F11D5','\U000F0680','\U000F0BAB','\U000F1322']
SLEEP  = '\U000F04B2'
TOWER  = '♜'
BG     = '\033[40m' if sys.stdout.isatty() else ''
RST    = '\033[0m' if sys.stdout.isatty() else ''
TWRC   = '\033[38;5;245m' if sys.stdout.isatty() else ''
PINK   = '\033[38;5;211m' if sys.stdout.isatty() else ''   # opus
YELLOW = '\033[93m'       if sys.stdout.isatty() else ''   # sonnet
GREEN  = '\033[92m'       if sys.stdout.isatty() else ''   # haiku / default

def model_color(m):
    m = (m or '').lower()
    if 'opus' in m:   return PINK
    if 'sonnet' in m: return YELLOW
    return GREEN

cap_spec = {'H':0x210B,'I':0x2110,'L':0x2112,'R':0x211B,'B':0x212C,'E':0x2130,'F':0x2131,'M':0x2133}
low_spec = {'e':0x212F,'g':0x210A,'h':0x210E,'o':0x2134}

def italic(s):
    out = []
    for c in s:
        if 'A' <= c <= 'Z': out.append(chr(cap_spec.get(c, 0x1D434 + ord(c) - ord('A'))))
        elif 'a' <= c <= 'z': out.append(chr(low_spec.get(c, 0x1D44E + ord(c) - ord('a'))))
        else: out.append(c)
    return ''.join(out)

def colored_imp(t):
    idx = (t['id'] - 1) % len(IMPS)
    return f'{model_color(t.get("model",""))}{IMPS[idx]}{RST}{BG}'

label = italic(f'all {n} imp{"s" if n != 1 else ""} back')
imps  = ' '.join(colored_imp(t) for t in tasks)
print(f'{BG} {TWRC}{TOWER}{RST}{BG} {imps} {SLEEP}  {label}{RST}')
PYEOF
```

   Then print the categorized results as plain text:

```
  merged:    #6 Test data creation scripts    (3 files)
             #7 Test data integrity checker   (2 files)
  published: #3 Discussion → https://github.com/...
             #5 Comment    → https://github.com/...
  queries:   #1 #2 #4 — no artifacts
```

5. **Print a run stats block.** Collect from the state file, the workflow result, the
   wrangler's checkpoints (diff stats, verdicts, fix rounds), and your session memory of
   what happened. Format as a clean block:

   ```
   ─────────────────────────────────────────
   Run stats · <repo> · <workflow_run_id>

   Achieved
     <one line per outcome, in plain value terms — the capability, fix, or
      improvement now shipped and why it matters to whoever uses the project.
      Describe what changed for the user, NOT how it was built: avoid file
      counts, task-type tallies, and implementation detail (those live in the
      Imps and Tokens sections below). e.g. "Test data is now reproducible from
      one command" not "3 files across 2 code tasks".>
     <PR/Discussion URLs if any>

   Decision points
     <one line per pivot: Head Imp changes in Phase 2, user task-list edits,
      Head Imp amendments in Phase 5, merge conflicts resolved — omit if none>

   Timing
     Dispatched    <dispatched_at from state file, local time>
     Completed     <now, local time>
     Elapsed       <Xm Ys>
     (per-imp timing not available — Date.now() blocked in workflow scripts)

   Imps
     <N> haiku · <N> sonnet · <N> opus  (<N> total)

   Tokens (workflow output only)
     ~<tokens_spent> output tokens across <N> agents
     haiku ×<N>  ·  sonnet ×<N>  ·  opus ×<N>
   ─────────────────────────────────────────
   ```

   Omit any section that has nothing to show. Compute elapsed time with:
   ```bash
   python3 -c "
   from datetime import datetime, timezone
   dispatched = datetime.fromisoformat('<dispatched_at>'.replace('Z','+00:00'))
   now = datetime.now(timezone.utc)
   secs = int((now - dispatched).total_seconds())
   print(f'{secs // 60}m {secs % 60}s')
   "
   ```

6. **Post the outcome comment to the source Discussion** — only if `source_discussion`
   (read into context back in Phase 5 Step 1, before the state file was deleted in item
   3) is non-null. Build a short summary (≤150 words: what shipped, PR/Discussion URLs
   from item 2, any unresolved findings from Step 5) and write it to a temp file rather
   than interpolating it into a shell string — the summary routinely contains backticks,
   `$`, and quotes (findings text, code refs) that would otherwise be shell-expanded or
   break the argument:
   ```bash
   printf '%s' "$SUMMARY" > "${CLAUDE_JOB_DIR:-/tmp}/imps-discussion-comment.md"
   gh api graphql -f query='
   mutation($discussionId:ID!,$body:String!){
     addDiscussionComment(input:{discussionId:$discussionId, body:$body}){
       comment { url }
     }
   }' -f discussionId="<source_discussion.id>" -F body=@"${CLAUDE_JOB_DIR:-/tmp}/imps-discussion-comment.md"
   ```
   Use `source_discussion.id` verbatim (the GraphQL node ID captured in
   [Discussion-seed mode](#discussion-seed-mode) step 2) — never re-derive it from the
   discussion number. Print the returned comment URL. This step runs whenever Finalize is
   reached on a discussion-seeded run — it is not gated by the Push & PR decision in Step
   4 or by whether any `publish` tasks ran (the two earlier abort paths that skip this
   entirely are handled in Phase 5 Step 1 and Step 3, not here).

7. **Activate the PR monitor** — only if the run opened a PR (Step 5 with `PR: yes`).
   The wrangler already pushed the branch, so no second push prompt is needed here.
   a. Use `poll_interval_seconds` (read into context back in Phase 5 Step 1, before the
      state file was deleted in item 3). Fall back to `300` if unavailable.
   b. Write `~/.claude/imps/runs/${SLUG}.prs.json` (substitute all values):
      ```json
      {
        "repo": "<owner/repo — e.g. your-org/my-app>",
        "pr_number": <integer PR number from the final checkpoint>,
        "pr_url": "<full GitHub PR URL from the final checkpoint>",
        "branch": "<current branch name>",
        "base_branch": "<default branch — main or master>",
        "poll_interval_seconds": <from state file, default 300>,
        "started_at": "<ISO timestamp: date -u +%Y-%m-%dT%H:%M:%SZ>",
        "handled_comment_ids": [],
        "ci_fix_attempts": {},
        "max_age_hours": 48
      }
      ```
   c. Invoke the `/imps:prs` skill (no args). It checks the PR immediately then
      self-reschedules via `ScheduleWakeup` until the PR is merged, closed, or 48 h old.
   d. Print: `PR monitor active — watching PR #<N>. I'll address comments, fix CI
      failures, and resolve merge conflicts automatically.`

   If the user chose `Not yet` in Step 4 (no PR opened), print instead: "Branch is local
   only and no PR was opened — push and open a PR, then invoke `/imps:prs` to activate the
   monitor."

8. **Learnings gate.** Identify non-trivial things that happened this run — anything
   surprising, wrong, or notably effective. Candidates include: wrong workflow IDs,
   task ID mismatches in log lines, Head Imp amendments that changed the plan, model
   escalations (or haiku tasks that needed sonnet), merge conflicts and how they
   resolved, PR branch issues, agent failures, wrangler blocked-checkpoints and any
   checkpoint-protocol friction, and anything the Head Imp flagged in Phase 5. Trivial things (everything worked as expected, no surprises) do not need
   a learning.

   If there are candidates, present them all at once using **AskUserQuestion**
   (`multiSelect: true`):
   - **question**: `"Any of these worth saving as a learning?"`
   - **header**: `"Learnings"`
   - **options**: one option per candidate learning, phrased as a concise rule
     (not a description of what happened — the rule to apply next time)

   If any were confirmed, immediately follow with a second **AskUserQuestion**
   (`multiSelect: true`) to determine scope:
   - **question**: `"Which of these are project-specific? (the rest will be saved globally)"`
   - **header**: `"Scope"`
   - **options**: one option per confirmed learning (same text)

   Selected → write to `.claude/imps/learnings.md` in the repo root (project-scoped).
   Unselected → write to `~/.claude/imps/learnings.md` (user-scoped, applies to all projects).

   For each destination file, append using this format:

   ```markdown
   ## Active rules
   <!-- ≤10 bullets; promote confirmed learnings here when a pattern repeats across
        ≥2 runs; demote to run notes if it turns out to be one-off.
        User-scoped: keep stack-agnostic. Project-scoped: repo-specific rules are fine. -->

   ## YYYY-MM-DD — <project> <task description>
   - <confirmed learning 1>
   - <confirmed learning 2>
   ```

   If `## Active rules` does not exist yet in a file, create it. If a confirmed
   learning repeats something already in a past run entry of the same file, promote
   it into that file's Active rules instead of appending a new run note. Keep each
   file's Active rules ≤10 bullets.

   After writing, print the confirmed learnings as a brief closing line:
   ```
   Learnings saved: "<rule 1>" [project] · "<rule 2>" [user]
   ```
   If none were confirmed: `No learnings saved this run.`

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

- Never dispatch without explicit confirmation of the task list.
- Never `git merge --force`, `git reset --hard`, or `git push` without explicit user instruction — **exceptions**: (1) the Imp Wrangler pushes and opens the endstate PR only after the operator's `Push & open PR` answer is relayed to it (Phase 5 Step 5), and pushes fix-loop commits to that same PR branch; (2) the `/imps:prs` PR monitor pushes fix commits to the PR branch autonomously once activated in Phase 5 Step 6.
- Never create GitHub PRs without user instruction — prefer direct branch merges.
- If a task touches a production system, pause and confirm before that task runs.
- If the Workflow tool is unavailable, fall back to sequential `Agent` tool calls and note the degradation.

### Hard-won protocol notes (apply in both modes)

These are carried over from the proven issue-driven workflow and apply equally to
free-text runs that fetch, branch, and push:

- **Fresh fetch before branching, always** — cut any working/holding branch from a
  fresh `git fetch origin <default-branch>`, never from a stale local HEAD. A stale
  HEAD pollutes the integration diff with unrelated commits.
- **Sync the default branch before opening the integration PR** — the default branch
  moves during long runs. `git fetch origin <default-branch> && git merge
  origin/<default-branch>` into the working branch before the PR so the diff stays
  clean (merge, not rebase: one merge commit = one conflict resolution and stable SHAs).
- **Workflow-file pushes need the SSH remote** — an HTTPS OAuth token often lacks the
  `workflow` scope, so pushing changes under `.github/workflows/` fails. Check
  `git remote get-url origin` and use the SSH remote for those pushes.
