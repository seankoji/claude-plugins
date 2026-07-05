# Dispatch & monitor reference — Imp Wrangler Segment D

*Read by the `imp-wrangler` agent at the start of Segment D (fresh dispatch) and by its
resume mode. The orchestrator never executes this file — it hands the whole segment to
the wrangler.*

The state file (`~/.claude/imps/runs/<slug>.json`, path given in your prompt) is yours
from the moment you are spawned: the orchestrator wrote it once and will never touch it
again. You flip `phase`, advance `segment`, fill workflow identifiers, heartbeat, and
eventually delete it in finalize.

## 1. Claim the run

Read the state file. Set `phase: "wrangler_running"`, `segment: "dispatch"`, and
`dispatched_at` to the current ISO timestamp (`date -u +%Y-%m-%dT%H:%M:%SZ`). Leave every
other field intact.

## 2. Git preflight

Verify the tree, then rebase the working branch onto the default branch:

```sh
git rev-parse --abbrev-ref HEAD          # must match state "branch"
git remote show origin | grep 'HEAD branch'   # default branch
git fetch origin && git rebase origin/<default-branch>
```

Branch mismatch → `blocked` checkpoint (`reason: "branch_mismatch"`). Rebase conflict →
abort the rebase (`git rebase --abort`), then `blocked` checkpoint
(`reason: "dispatch_failed"`, `detail: { step: "rebase", conflict_files: [...] }`). Do
not launch the Workflow until the tree is clean and rebased.

**Fresh fetch before branching, always** — any branch cut during this run comes from a
fresh `git fetch origin <default-branch>`, never a stale local HEAD. A stale HEAD
pollutes the integration diff with unrelated commits.

## 3. Author and launch the Workflow

Write and launch a **Workflow** implementing the full dependency graph from the state
file's `tasks` array in a single call. The Workflow tool is explicitly authorized for
this run.

Rules for the workflow script:

- Topologically sort tasks into stages; implement as `pipeline()` stages with inner `parallel()` for tasks that share a stage but have no mutual dependency.
- Every agent uses the `imp` agent type: `agent(..., { agentType: 'imp' })` — this bakes in atomic-task discipline, correct branch handling for publish tasks, and structured output conventions.
- **Agent-type fallback**: If a workflow agent call errors with an agent-type registration failure, the `imp` type may not be registered in this session. Change `agentType: 'imp'` to `agentType: 'general-purpose'` in the workflow script and re-run.
- Every `code`-type task adds `isolation: 'worktree'`: `agent(..., { agentType: 'imp', isolation: 'worktree' })`
- **Worktree base**: `isolation: 'worktree'` always creates the agent's worktree from the repo's last committed HEAD on the **default branch** — NOT the caller's working branch. Committing in-progress changes to a *side* working branch therefore does NOT make them visible to the worktree. If `code` tasks must see in-progress changes, those changes must first reach the default branch itself (merge or push them to the default branch before dispatch); committing to a non-default branch is not enough.
- **Gate before commit**: every `code` agent resolves the repo's gate/lint commands (from `package.json` scripts, `Makefile`, `pyproject.toml`, CI config, or `AGENTS.md`/`CONTRIBUTING.md`) and runs them — plus the autofix command if one exists — before committing. It fixes failures it caused and leaves pre-existing failures noted. This mirrors issue-mode's per-agent `GATE_CMDS`/`LINT_FIX` discipline so agents never finish gate-red (Segment A's aggregate gates are a backstop, not the first line).
- Apply the model routing recorded per task in the state file: `agent(..., { agentType: 'imp', model: '<haiku|sonnet|opus model id from the session model table>' })`. Model IDs vary by session — read the exact identifiers from the session's model table rather than hardcoding them.
- Use `log()` to emit progress markers. Format **must** be: `log('imp:start #N')` when starting task N, `log('imp:done #N')` when task N completes. The integer N **must exactly match** the `id` field of the corresponding task in the state file — never combine multiple state-file tasks into one agent or split one task across agents. One agent = one task ID. Your monitor loop (§4) greps these markers out of the workflow output file for the heartbeat.
- Never create GitHub PRs from inside the workflow. PRs are deferred to Segment B, created from the main working branch after merge — not from isolated worktree branches whose names are non-deterministic.
- Every agent returns structured output via `schema`. `status` is an enum —
  `"done"` (task completed) or `"failed"` (the agent could not complete it):
  ```json
  { "id": 1, "label": "...", "type": "query", "status": "done|failed", "branch": null, "artifacts": [], "notes": "if failed, why (≤50 words)" }
  ```
  A `code` agent that fails (unresolvable error, gates it cannot get green) returns
  `"status": "failed"` with a `notes` reason and leaves its branch unmerged — §5 triage
  surfaces failed tasks and Segment A never merges them.
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

If the Workflow tool is unavailable, fall back to sequential `Agent` calls (`imp` type,
same per-task rules, worktree isolation for `code` tasks) and note the degradation in
your next checkpoint. If the launch errors after the agent-type fallback →
`blocked · dispatch_failed` with the error.

**Record identifiers immediately.** The Workflow tool result contains the task ID
(`wp...`), run ID (`wf_...`), and — for background tasks — an output file path. Write
them into the state file as `workflow_task_id`, `workflow_run_id`,
`workflow_output_file` (null if absent) before anything else. The heartbeat and any
future resume depend on `workflow_output_file`.

## 4. Monitor loop

Set `segment: "monitor"` in the state file. The Workflow runs as a background task of
**your** session — the orchestrator cannot see it; your heartbeat is the only progress
signal anyone has.

- Load the waiting tools first: `ToolSearch: "select:Monitor,TaskOutput,TaskGet"`.
- Wait with **Monitor** on the workflow task, timeout = `poll_interval_seconds` from the
  state file (default 300). Foreground sleep is blocked — Monitor is the sanctioned wait.
- Each time Monitor returns without completion, write a **heartbeat** to the state file:
  `last_heartbeat` = now (ISO), `tasks_done` = the task IDs whose `imp:done #N` markers
  appear in `workflow_output_file` (grep the file — never read it whole). Then re-issue
  Monitor.
- **Timeout valve**: if elapsed time since `dispatched_at` exceeds `max_workflow_hours`
  (state file, default 6), stop waiting and emit
  `blocked · workflow_timeout` with `{ elapsed, tasks_done, pending }`. Resume verbs:
  `wait <hours>` (extend the valve and keep monitoring) · `integrate partial` (treat
  unfinished tasks as failed and proceed to §5) · `abort`.

## 5. Capture and triage the result

When the Workflow completes, take its compact return value (never its raw log):

1. **Snapshot into the state file immediately**: `worktrees` (the branch map),
   `tasks_done` (all completed IDs). This is what a future resume recovers from.
2. **Triage failed tasks** against the DoD in GOAL.md
   (`~/.claude/imps/runs/<slug>.md`). A failed task that blocks an acceptance criterion
   must not be silently integrated around: emit `blocked · workflow_failed_tasks` with
   `{ failed: [{id, label, notes}], done: [ids] }` and wait. Resume verbs:
   `skip tasks #N,#M` (integrate without them) · `retry tasks #N,#M: <guidance>`
   (re-dispatch just those tasks as a mini-Workflow or direct `imp` agents, then
   re-triage) · `abort`.
3. Non-blocking failures ride along in the `failed_tasks` field of the `gates_green`
   checkpoint — never merged, always reported.

Keep the workflow summary (`run_id`, elapsed, `tokens_spent`, `model_counts`,
`artifacts`) — the `gates_green` checkpoint's `workflow` block carries it to the
orchestrator, which never saw the workflow result.

Then set `segment: "integrate"` and proceed directly into Segment A. **No checkpoint is
emitted between your spawn and Segment A's outcome** unless something above blocked.

## 6. Resume-mode reconciliation

Entered when your prompt says `resume` (the orchestrator re-spawned you after a wrangler
death or a `/clear`). The old Workflow — if one was launched — belongs to a dead session
and is unreachable; never try to re-attach to `workflow_task_id`. Reconcile against
ground truth instead:

1. Read the state file: `segment`, `tasks`, `tasks_done`, `worktrees`, `pr`,
   `last_heartbeat`.
2. Establish what actually finished:
   - `worktrees` map entries whose branch exists (`git branch --list <branch>`) →
     done `code` tasks.
   - `code` tasks not in `worktrees`: look for plausible orphan branches
     (`git branch --list` for recent unmerged branches touching that task's area) —
     adopt a branch only when clearly attributable, otherwise treat the task as not done.
   - `query`/`publish` tasks in `tasks_done` → done (for `publish`, verify the artifact
     exists when a URL was recorded in GOAL.md or the state file).
   - GOAL.md checkboxes and Status notes corroborate; the heartbeat bounds the
     uncertainty window to one poll interval.
3. Re-dispatch **only** the tasks with no completed branch/artifact, as a fresh
   mini-Workflow under §3's rules (or direct `imp` agents when ≤2 tasks remain). In-flight
   work from the dead session is deliberately rerun — worktree branches it never
   committed are unrecoverable.
4. Then continue from the recorded `segment`: `dispatch`/`monitor` → §5 onward;
   `integrate` → Segment A from the top (idempotent — merged branches no-op, reviews and
   gates re-run); `publish_finalize` → Segment B+C from the top, **skipping
   `gh pr create` if `pr` is non-null** (push to the existing PR branch instead).

## Protocol notes (hard-won)

- **Sync the default branch before opening the integration PR** — the default branch
  moves during long runs. `git fetch origin <default-branch> && git merge
  origin/<default-branch>` into the working branch before the PR so the diff stays
  clean (merge, not rebase: one merge commit = one conflict resolution and stable SHAs).
- **Workflow-file pushes need the SSH remote** — an HTTPS OAuth token often lacks the
  `workflow` scope, so pushing changes under `.github/workflows/` fails. Check
  `git remote get-url origin` and use the SSH remote for those pushes.
