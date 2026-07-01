---
name: imps
description: >
  Decompose a vague task into dependency-mapped imps, dispatch with model routing,
  monitor progress, and merge code changes back to the current branch.
argument-hint: '<task description>'
---

# /imps — summon the swarm

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
parallel agents (imps), dispatch them with right-sized models, monitor their progress every 60
seconds, and integrate results cleanly.

---

## Mode detection

`/imps` has **three modes**, checked in this order:

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
  whitespace-separated token matches `^#?\d+$` (e.g. `/imps 42 43 51`, `/imps #42`).
  **→ Follow [`commands/issue-mode.md`](./issue-mode.md)** for the
  full scout → rolling-dispatch → holding-branch → gates → persona-panel → handoff
  workflow. Do not continue with the phases below.

- **Free-text mode** — `$ARGUMENTS` is a task description (anything that is not purely
  issue numbers), or empty. This is the original `/imps` behaviour. **→ Continue with
  the phases below.**

Detection order: (1) single `.md` token that resolves to a file → checklist-file mode.
(2) non-empty AND every token matches `^#?\d+$` → issue-driven mode. (3) everything
else → free-text mode.

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
adversarially. It **does not see the live transcript** — it only sees what you explicitly
pass in the prompt. Always pass the relevant artifact directly.

Invoke it like this (swap in the actual content and role):

```
agent(
  `You are the Head Imp — the sharpest critic in the swarm.
   Your briefs: [READ ${CLAUDE_PLUGIN_ROOT}/personas/solution-architect.md]
               [READ ${CLAUDE_PLUGIN_ROOT}/personas/grumpy-engineer.md]

   <ARTIFACT>
   <what you're reviewing — GOAL.md task table, or the git diff output>
   </ARTIFACT>

   Argue AGAINST this. Find wrong task boundaries, mis-routed models, missing deps,
   correctness bugs, unsafe assumptions, gaps in the DoD. Steelman the case that this
   should NOT ship. Return a list of findings (blocker | major | minor | nit), then a
   one-line VERDICT: APPROVE | CHANGES_REQUESTED.`,
  { model: '<opus model id>', label: 'head-imp' }
)
```

**Phase 2 (plan review):** pass the full contents of GOAL.md as the artifact.
**Phase 5 (diff review):** capture `git diff <default-branch>..HEAD` and pass that output
as the artifact — do NOT assume the Head Imp can read it from context.

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

If the current project's file exists, read it and check the `phase` field.
If the `phase` field is absent (written by an older revision), treat it as `"dispatched"`.

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
  source — GOAL.md is human-readable but the state file is what Phase 3's banner and
  heartbeat read). Verify `git rev-parse --abbrev-ref HEAD` matches state `branch`;
  warn the user if it doesn't and wait for confirmation before continuing. Re-read key
  repo files to ground the Workflow prompts (the prior planning context was cleared).
  Skip Phases 0/1/2 entirely, jump straight to **Phase 3 dispatch**
  (rebase → update state → launch Workflow).
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

- **Resume** — skip discovery and set up the status heartbeat against the existing run
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

If `$ARGUMENTS` is empty AND the guard check (above) found no pending state file, ask "What's the task?" and wait — collect it here before invoking prompt-builder.

Use the **Skill tool**:
- `skill`: `prompt-builder:prompt-builder`
- `args`: the raw task description alone (no framing preamble).

  After prompt-builder's first response, steer if needed: "Skip model selection, test cases, and save-path guidance — I just need 1–2 sharp sentences I can decompose into parallel agents."

When the user confirms a refined description, store it as `<REFINED_TASK>`. Use `<REFINED_TASK>` in place of `$ARGUMENTS` for all subsequent phases.

---

## Phase 1 — Discovery

Task description: `<REFINED_TASK>`

Ask the following in a **single AskUserQuestion call** (batch all five), **skipping any questions prompt-builder already answered** during Phase 0:

1. Which repo and branch is this work in? (free text)
2. What concrete output artifacts are expected? Be specific — e.g. Bash scripts, GitHub Discussion post, PR, code changes.
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

**Step 1:** Call **`EnterPlanMode`**. You are now the opus planner. Explore the repo
as needed (read key files, check the default branch, confirm GATE_CMDS exist) to ground
the plan in reality. Then:

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

Add `- [ ] CI green on the PR` to the Definition of Done **only if this run will open a
PR** (see Phase 5 Step 4 — the endstate PR is the default for runs that produce code
changes). Omit it for query/publish-only runs that create no PR, or it stays permanently
unresolvable.

This file is the `/compact`-durable human-readable spine. The JSON state file
(`~/.claude/imps/runs/<slug>.json`, written in Step 6) is the **authoritative** task
table — Phase 3 and the heartbeat read from it, not from GOAL.md. If you hand-edit
GOAL.md's task table after approval, mirror the change into the state file (or re-run
planning) or it will not take effect. Update the Status section at each major milestone.

**Step 3 — Head Imp review (mandatory):**
Before calling `ExitPlanMode`, summon the Head Imp (see the Head Imp section above).
Pass the full contents of `GOAL.md` as the artifact. The Head Imp argues AGAINST the
plan — wrong boundaries, mis-routed models, missing deps, gaps in the DoD. Fix what the
critique exposes before proceeding.

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
  "dispatched_at": null,
  "poll_interval_seconds": 300
}
```

Then print this handoff prompt (verbatim — it is informational, not a question):

```
Plan approved and durable in GOAL.md + state file.

  Recommended: /clear  →  /imps   (dispatches from a clean context)
  Sonnet currently inherits the full Opus planning window (a substantial
  portion of the context budget, depending on how much the planner explored).
  After /clear, re-read key repo files before authoring Workflow prompts.

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
`repo`, `branch`, `tasks`, and `poll_interval_seconds` intact from Phase 2. Leave
`workflow_task_id` and `workflow_run_id` null — they are filled in Step 4.

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
  "dispatched_at": "<ISO timestamp from Bash: date -u +%Y-%m-%dT%H:%M:%SZ>",
  "poll_interval_seconds": 300
}
```
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
- Use `log()` to emit progress markers. Format **must** be: `log('imp:start #N')` when starting task N, `log('imp:done #N')` when task N completes. The integer N **must exactly match** the `id` field of the corresponding task in the state file — never combine multiple state-file tasks into one agent or split one task across agents. One agent = one task ID. Mismatches cause the heartbeat to show tasks as perpetually running.
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

**Step 4:** Update `~/.claude/imps/runs/${SLUG}.json` with both IDs returned by the
Workflow tool — the tool result contains two distinct identifiers:
- `workflow_task_id`: the task ID (`wp...`) — used by `TaskOutput` to stream log output
- `workflow_run_id`: the run ID (`wf_...`) — used by the `/workflows` UI

The heartbeat reads `workflow_task_id` for TaskOutput calls; `workflow_run_id` is human-reference only.

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

**Step 6:** Invoke the `/imps:status` skill directly (no args). It self-reschedules
via `ScheduleWakeup` and stops cleanly when the state file is gone — do NOT use the
`loop` skill here, as that creates a `CronCreate` job which cannot self-cancel.

Return control to the user. Do not block. The merge phase is handled when the task
notification arrives (see Phase 5 below).

---

> **Phase 4** is intentionally not a separate section here. The numbering is kept aligned
> with `commands/issue-mode.md`, where Phase 4 is the persona panel — in free-text mode
> that panel is folded into Phase 5 below (Step 5).

## Phase 5 — Merge → Gates → Endstate PR → Persona panel → Fix loop → Finalize (triggered by task notification)

When the Workflow's `<task-notification>` arrives, this is your cue. The status loop will
stop on its own once the state file is deleted — do not wait for it, and do not merge from
within /imps:status. This session is the sole merge owner.

**Step 1 — Merge all code branches:**
1. Read the workflow result from the notification.
2. **Check for failed tasks first.** Any task whose structured output has
   `"status": "failed"` is NOT merged. List each failed task (`#<id> <label> — <notes>`)
   and surface them to the user before continuing. If a failed task blocks the run's
   acceptance criteria, pause and ask how to proceed (retry, skip, or abort) — do not
   silently merge a partial result set.
3. For each `code`-type task in `worktrees` that returned `"status": "done"`:
   a. `git merge <branch>` from the main working tree.
   b. Clean merge → print `` `  ✓ #<id> <label> (<n> files)` ``
   c. Conflicts → list the conflicting files and ask the user to resolve. Continue after
      each resolution.
4. Sync default branch into the working branch before the endstate PR (merge, not rebase):
   ```sh
   git fetch origin <default-branch> && git merge origin/<default-branch>
   ```

**Step 2 — Head Imp diff review (mandatory):**
Capture the merged diff explicitly:
```sh
git diff origin/<default-branch>..HEAD -- ':!*lock*' ':!dist'
```
Pass that output as the artifact to the Head Imp (see the Head Imp section above).
The Head Imp tries to break the diff — correctness bugs, missing changes, unsafe
assumptions. Make the amendments the critique demands, then proceed.

**Step 3 — Deterministic gates:**
Resolve the repo's gate commands once — check for `package.json` scripts, `Makefile`
targets, CI config — and run them in order: build → lint → test → type. For each gate:
- Pass → tick the corresponding GOAL.md DoD box (`[x]`)
- Fail → fix inline (one-shot sonnet fixer per failing gate); re-run the gate; repeat
  until green. If a gate cannot be fixed in 3 attempts, surface it to the user.

**Step 4 — Endstate PR (open it BEFORE the panel):**
The persona panel posts its findings as comments on a PR thread, so the PR must exist
first — mirroring `issue-mode.md`, where the integration PR opens in Phase 3, before the
Phase 4 panel. Opening the PR requires pushing the branch, so this step is also the push
gate. This is the correct moment: code branches are merged, the Head Imp reviewed the
diff, and gates are green.

Ask the user with **AskUserQuestion**:
- **question**: `"Push this branch and open the endstate PR for review?"`
- **header**: `"Push & PR?"`
- **options**:
  1. `Push & open PR` — run `git push`, then `gh pr create` from the current branch (a
     draft is fine — Step 7 flips it to ready). Print the PR URL. This is the thread the
     persona panel comments on. Add `- [ ] CI green on the PR` to the GOAL.md DoD (pending;
     `/imps:prs`, activated in Step 7, tracks it).
  2. `Not yet` — do NOT push and do NOT open a PR. The persona panel then surfaces its
     findings **inline in this session** instead of on a PR thread; the branch stays local
     and no PR monitor starts. Do not add a `CI green on the PR` DoD line (there is no PR).

Opening the endstate PR is the default for free-text runs that produced code changes —
only `Not yet` skips it.

**Step 5 — Persona panel (code + browser):**
Follow `commands/issue-mode.md § Phase 4` exactly — it is the canonical reference.
Short version:
- **Code panel** (always): dispatch all four opus personas (`solution-architect`,
  `grumpy-engineer`, `sre`, `business-analyst`) in parallel. Each Reads its brief from
  `${CLAUDE_PLUGIN_ROOT}/personas/<slug>.md`, reviews the integration diff (excluding
  lockfiles/generated via `git diff ... ':!*lock*' ':!dist'`), ends with
  `VERDICT: APPROVE | CHANGES_REQUESTED @ <sha>`.
- **Browser panel** (when a UI surface exists): one sonnet collector drives the browser
  over every page at 1440×900 + 375×812 and saves a bundle; `ux-designer` (sonnet) judges
  the bundle. Browser transport resolves in order — `CLAUDE_CDP_URL` (default
  `ws://localhost:3000`) via `chromium.connectOverCDP`, else the `mcp__claude-in-chrome__*`
  tools, else skip the browser panel and note it (see issue-mode § Browser rig).
- **Where personas post:** if Step 4 opened a PR, personas post comments on that PR thread
  prefixed `[Persona: <Name>]`. If the user chose `Not yet` (no PR), personas surface the
  same findings inline in this session instead.
- Parse VERDICT lines. CHANGES_REQUESTED requires ≥1 blocker or major.
  Update the live GOAL.md Status section with the tally.

**Step 6 — Fix loop (max 3 rounds):**
For each CHANGES_REQUESTED verdict:
- Disjoint findings → parallel sonnet fixers (one per finding)
- Cross-cutting findings → one opus fixer
Re-review only the dissenting personas scoped to the delta. Repeat until all personas
APPROVE or only minors/nits remain (they never block). Update GOAL.md DoD:
`[x] Persona panel reviewed; all blocker/major findings addressed`.

**Step 7 — Finalize:**
1. If Step 4 opened a draft PR, flip it to ready for review now (`gh pr ready <N>`). If the
   user chose `Not yet`, there is no PR — skip this item.
2. Print artifact links for `publish`-type tasks (Discussions, comments, etc.):
   ```
     󰭟 #3 Discussion → https://github.com/...
     󰭟 #5 Comment    → https://github.com/...
   ```
3. Delete `~/.claude/imps/runs/${SLUG}.json` (the same path written in Phase 3 Step 2).
   The status loop will detect this on its next tick (when the directory is empty) and stop.
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

5. **Print a run stats block.** Collect from the state file, workflow result, git output,
   and your session memory of what happened. Format as a clean block:

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

6. **Activate the PR monitor** — only if Step 4 pushed and opened a PR. The branch was
   already pushed in Step 4 (its `Push & open PR` option), so no second push prompt is
   needed here.
   a. Capture `poll_interval_seconds` from the state file read earlier in this phase
      (before it was deleted in item 3). Fall back to `300` if unavailable.
   b. Write `~/.claude/imps/runs/${SLUG}.prs.json` (substitute all values):
      ```json
      {
        "repo": "<owner/repo — e.g. your-org/my-app>",
        "pr_number": <integer PR number from Step 4>,
        "pr_url": "<full GitHub PR URL from Step 4>",
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

7. **Learnings gate.** Identify non-trivial things that happened this run — anything
   surprising, wrong, or notably effective. Candidates include: wrong workflow IDs,
   task ID mismatches in log lines, Head Imp amendments that changed the plan, model
   escalations (or haiku tasks that needed sonnet), merge conflicts and how they
   resolved, PR branch issues, agent failures, and anything the Head Imp flagged in
   Phase 5. Trivial things (everything worked as expected, no surprises) do not need
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
- Never `git merge --force`, `git reset --hard`, or `git push` without explicit user instruction — **exception**: the `/imps:prs` PR monitor pushes fix commits to the PR branch autonomously once activated via the Push now option in step 9.
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
