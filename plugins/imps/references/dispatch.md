# Dispatch & monitor reference — Imp Wrangler Segment D

*Read by the `imp-wrangler` agent at the start of Segment D (fresh dispatch) and by its
resume mode. The orchestrator never executes this file — it hands the whole segment to
the wrangler.*

The state file (`~/.claude/imps/runs/<slug>.json`, path given in your prompt) is yours
from the moment you are spawned: the orchestrator wrote it once and will never touch it
again. You flip `phase`, advance `segment`, heartbeat, and eventually delete it at the
end of the run.

Note: the **Workflow tool is not available to subagents** (verified empirically) — you
dispatch the DAG yourself with the Agent tool, which subagents do have, including
background spawns, parallel batches, `isolation: 'worktree'`, and nested agent types.

## 1. Claim the run

Read the state file. Set `phase: "wrangler_running"`, `segment: "dispatch"`, and
`dispatched_at` to the current ISO timestamp (`date -u +%Y-%m-%dT%H:%M:%SZ`). Leave
every other field intact.

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
not dispatch until the working tree is clean and rebased.

**Fresh fetch before branching, always** — any branch cut during this run comes from a
fresh `git fetch origin <default-branch>`, never a stale local HEAD. A stale HEAD
pollutes the integration diff with unrelated commits.

## 3. Dispatch the imp stages

Topologically sort the state file's `tasks` into **stages**: a task lands in the first
stage after all its `deps`. You run the DAG stage by stage; within a stage every task
runs concurrently.

Per-task rules (identical for every imp you spawn):

- Spawn via the Agent tool with `subagent_type: '🦇'` — this bakes in atomic-task
  discipline, correct branch handling for publish tasks, and structured output
  conventions. **Agent-type fallback**: if the spawn errors with an agent-type
  registration failure, re-spawn as `general-purpose` with the full body of
  `agents/imp.md` prepended to the prompt.
- Every `code`-type task adds `isolation: 'worktree'`.
- **Worktree base**: `isolation: 'worktree'` creates the imp's worktree from the repo's
  last committed HEAD on the **default branch** — NOT your working branch. Committing
  in-progress changes to a *side* working branch therefore does NOT make them visible
  to the imp. If `code` tasks must see in-progress changes, those changes must first
  reach the default branch itself (merge or push them there before dispatch);
  committing to a non-default branch is not enough.
- **Gate before commit**: every `code` imp resolves the repo's gate/lint commands
  (from `package.json` scripts, `Makefile`, `pyproject.toml`, CI config, or
  `AGENTS.md`/`CONTRIBUTING.md`) and runs them — plus the autofix command if one
  exists — before committing. It fixes failures it caused and leaves pre-existing
  failures noted. This mirrors issue-mode's per-agent `GATE_CMDS`/`LINT_FIX` discipline
  so imps never finish gate-red (Segment A's aggregate gates are a backstop, not the
  first line).
- Apply the model routing recorded per task in the state file (`model:` on every
  spawn). Model IDs vary by session — read the exact identifiers from the session's
  model table rather than hardcoding them.
- Tag each spawn's `description` with its model tier so progress output shows it at a
  glance: `🦇` haiku · `🦇🦇` sonnet · `🦇🦇🦇` opus · `🦇🦇🦇🦇` fable, matching
  whatever `model:` you set on that same call (e.g. `description: "🦇🦇 docs task #4"`).
- One imp = one task ID — never combine multiple state-file tasks into one imp or
  split one task across imps.
- Never create GitHub PRs from inside dispatch. PRs are deferred to Segment B, created
  from the main working branch after merge — not from isolated worktree branches whose
  names are non-deterministic.
- Every imp's prompt demands structured output as its final message. `status` is an
  enum — `"done"` (task completed) or `"failed"` (the imp could not complete it):
  ```json
  { "id": 1, "label": "...", "type": "query", "status": "done|failed", "branch": null, "artifacts": [], "notes": "if failed, why (≤50 words)" }
  ```
  A `code` imp that fails (unresolvable error, gates it cannot get green) returns
  `"status": "failed"` with a `notes` reason and leaves its branch unmerged — §5 triage
  surfaces failed tasks and Segment A never merges them.

**Launch each stage as one message of parallel background spawns**
(`run_in_background: true` on every Agent call). Background completions arrive as
task-notifications carrying each imp's final structured JSON — that notification is the
completion signal (imp output files have no reliable completion marker; never grep or
read them). If background spawning is unavailable, degrade to synchronous parallel
batches per stage (one message, `run_in_background: false` — you lose mid-stage
heartbeats; note the degradation in your next checkpoint). If subagents error entirely →
`blocked · dispatch_failed` with the error.

## 4. Monitor loop

Set `segment: "monitor"`. The imps run as background children of **your** session — the
orchestrator cannot see them; your heartbeat is the only progress signal anyone has.

- Load the wait tool first: `ToolSearch: "select:Monitor"`. Foreground sleep is
  blocked — Monitor is the sanctioned wait between notifications.
- Loop: arm Monitor with timeout = `poll_interval_seconds` from the state file
  (default 300). Each time it returns — on child activity, a completion notification,
  or timeout — process whatever task-notifications have arrived, then write a
  **heartbeat** to the state file: `last_heartbeat` = now (ISO), plus incremental
  updates as completions arrive — append to `tasks_done`, record `code` branches into
  `worktrees` (`{"<task id>": "<branch>"}`), append publish URLs to `artifacts`. Then
  re-arm.
- When every imp of the current stage has completed, launch the next stage (§3). After
  the last stage, go to §5.
- **Dependency-failure propagation**: a `failed` imp does NOT count as a satisfied
  dependency. The moment an imp returns `"status": "failed"`, mark every
  not-yet-dispatched task that transitively depends on it as failed too
  (`notes: "dependency #<id> failed"`) — never dispatch onto a failed base. If that
  cascade leaves nothing still running and nothing dispatchable, do not sit out the
  timeout valve — jump straight to §5 triage (`imps_failed`). List cascade-failed
  dependents alongside the root failure in the `failed` array so the operator can name
  them in a `retry tasks` verb; retrying a root task whose retry succeeds makes its
  cascade-failed dependents eligible for retry as well.
- **Timeout valve**: if elapsed time since `dispatched_at` exceeds
  `max_dispatch_hours` (state file, default 6), stop waiting and emit
  `blocked · dispatch_timeout` with `{ elapsed, tasks_done, pending }`. Resume verbs:
  `wait <hours>` (extend the valve and keep monitoring) · `integrate partial` (treat
  unfinished tasks as failed and proceed to §5) · `abort`.

## 5. Capture and triage the result

When the last stage completes:

1. **Consolidate the state file**: final `tasks_done`, `worktrees`, `artifacts` (a
   post-dispatch death must not lose the branch map or the links that go in the
   Discussion summary and `run_complete`). This is what a future resume recovers from.
2. **Triage failed tasks** against the DoD in GOAL.md
   (`~/.claude/imps/runs/<slug>.md`). A failed task that blocks an acceptance criterion
   must not be silently integrated around: emit `blocked · imps_failed` with
   `{ failed: [{id, label, notes}], done: [ids] }` and wait. Resume verbs:
   `skip tasks #N,#M` (integrate without them) · `retry tasks #N,#M: <guidance>`
   (re-dispatch just those tasks per §3, then re-triage) · `abort`.
3. Non-blocking failures ride along in the `failed_tasks` field of the `gates_green`
   checkpoint — never merged, always reported.

Assemble the dispatch summary for the `gates_green` checkpoint's `dispatch` block —
elapsed since `dispatched_at`, `model_counts` tallied from the task table, and
`tokens_spent` totalled from the usage metadata the harness attaches to each imp's
completion (the `subagent_tokens` figure in the task-notification / Agent tool result —
NOT the imp's own JSON, which carries no usage field; null if the metadata is absent),
plus the `artifacts` list. The orchestrator never saw any of this — the checkpoint is
where it learns what ran.

Then set `segment: "integrate"` and proceed directly into Segment A. **No checkpoint is
emitted between your spawn and Segment A's outcome** unless something above blocked.

## 6. Resume-mode reconciliation

Entered when your prompt says `resume` (the orchestrator re-spawned you after a wrangler
death or a `/clear`). Any imps the dead wrangler had in flight belong to a dead session —
their notifications are lost and they are unreachable. Reconcile against ground truth
instead:

1. Read the state file: `segment`, `tasks`, `tasks_done`, `worktrees`, `artifacts`,
   `pr`, `verdicts`, `discussion_comment_url`, `last_heartbeat`.
2. Establish what actually finished:
   - `worktrees` map entries whose branch exists (`git branch --list <branch>`) →
     done `code` tasks.
   - `code` tasks not in `worktrees`: look for plausible orphan branches
     (`git branch --list` for recent unmerged branches touching that task's area) —
     adopt a branch only when clearly attributable, otherwise treat the task as not
     done.
   - `query`/`publish` tasks in `tasks_done` → done (for `publish`, verify the artifact
     exists when a URL was recorded in the state file's `artifacts` or GOAL.md).
   - Before re-dispatching a `publish` task, search for its artifact directly (e.g.
     `gh search`, the repo's Discussion/issue list) — a publish imp may have posted and
     died before the heartbeat recorded it. If the artifact exists, adopt its URL into
     `artifacts` instead of re-publishing a duplicate.
   - GOAL.md checkboxes and Status notes corroborate; the heartbeat bounds the
     uncertainty window to one poll interval.
3. Re-dispatch **only** the tasks with no completed branch/artifact, per §3. In-flight
   work from the dead session is deliberately rerun — worktree branches it never
   committed are unrecoverable.
4. Then continue from the recorded `segment`: `dispatch`/`monitor` → §5 onward;
   `integrate` → Segment A from the top (idempotent — merged branches no-op, reviews
   and gates re-run); `publish_finalize` → Segment B+C from the top, honoring every
   state-file marker (`pr`, `verdicts`, `discussion_comment_url` — see your brief);
   `complete` → the run already finalized: re-emit `run_complete` from what the state
   file holds, noting the recovery. (The orchestrator re-running its `run_complete`
   handling in this window is expected and safe: `/imps:prs` re-activation self-dedupes
   off the same `.prs.json`, and the learnings were genuinely never saved.)

## Protocol notes (hard-won)

- **Sync the default branch before opening the integration PR** — the default branch
  moves during long runs. `git fetch origin <default-branch> && git merge
  origin/<default-branch>` into the working branch before the PR so the diff stays
  clean (merge, not rebase: one merge commit = one conflict resolution and stable SHAs).
- **Workflow-file pushes need the SSH remote** — an HTTPS OAuth token often lacks the
  `workflow` scope, so pushing changes under `.github/workflows/` fails. Check
  `git remote get-url origin` and use the SSH remote for those pushes.
