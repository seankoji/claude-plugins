# Finalize reference — Imp Wrangler Segment C tail

*Read by the `imp-wrangler` agent when the persona panel and fix loop are done, before
emitting the `run_complete` checkpoint. Everything here runs inside the wrangler — the
orchestrator only relays the operator's learnings answers afterwards.*

Execute in this order:

## 1. Flip the PR to ready

If this run opened a draft PR (`PR: yes` path): `gh pr ready <N>`. Record
`pr.ready: true`. `PR: no` path → skip.

## 2. Collect artifact links

Gather every `publish`-type task's artifact URL (Discussions, comments, issues) from
the state file's `artifacts` list captured in Segment D — they go in the `run_complete`
checkpoint's `artifacts` array so the orchestrator can print them.

## 3. Post the outcome comment to the source Discussion

Only if the state file's `source_discussion` is non-null **and its
`discussion_comment_url` is still null** — a non-null URL means a previous wrangler
already posted; never post twice. Build a short summary
(≤150 words: what shipped, PR/artifact URLs, any unresolved findings) and write it to a
temp file rather than interpolating it into a shell string — the summary routinely
contains backticks, `$`, and quotes that would otherwise be shell-expanded or break the
argument:

```bash
printf '%s' "$SUMMARY" > "${CLAUDE_JOB_DIR:-/tmp}/imps-discussion-comment.md"
gh api graphql -f query='
mutation($discussionId:ID!,$body:String!){
  addDiscussionComment(input:{discussionId:$discussionId, body:$body}){
    comment { url }
  }
}' -f discussionId="<source_discussion.id>" -F body=@"${CLAUDE_JOB_DIR:-/tmp}/imps-discussion-comment.md"
```

Use `source_discussion.id` verbatim (the GraphQL node ID captured when the run was
seeded) — never re-derive it from the discussion number. Write the returned comment URL
to the state file's `discussion_comment_url` **immediately** (the double-post guard
above depends on it), and carry it into the checkpoint. This runs whenever finalize is
reached on a
discussion-seeded run — it is not gated by the Push & PR decision or by whether any
`publish` tasks ran.

**Abort variant** (used from the abort path at any gate, not here): when the operator
sends `abort` and `source_discussion` is non-null, post
"Run aborted: `<one-line reason>`. No changes were merged." via the same mutation before
emitting the `aborted` checkpoint, and set `abort_notice_posted: true` in it.

## 4. Write the PR-monitor state file

Only if the run opened a PR. Write `~/.claude/imps/runs/<slug>.prs.json` (all values
from your own state — the PR fields from your `gh pr create`, the poll interval from the
run state file):

```json
{
  "repo": "<owner/repo>",
  "pr_number": <integer>,
  "pr_url": "<full PR URL>",
  "branch": "<working branch>",
  "base_branch": "<default branch>",
  "poll_interval_seconds": <from state file, default 300>,
  "started_at": "<ISO timestamp: date -u +%Y-%m-%dT%H:%M:%SZ>",
  "handled_comment_ids": [],
  "ci_fix_attempts": {},
  "max_age_hours": 48
}
```

You cannot activate `/imps:prs` yourself — skill invocation is unreliable from a
subagent. Set `prs_monitor: { "state_file": "<path>", "pr_number": <N> }` in
`run_complete`; the orchestrator makes the one Skill call.

## 5. Assemble run stats

Collect into `run_complete.run_stats` (structured fields — the orchestrator renders
them; omit anything empty):

- `dispatched_at` — from the state file.
- `elapsed` — `"Xm Ys"`, computed via:
  ```bash
  python3 -c "
  from datetime import datetime, timezone
  dispatched = datetime.fromisoformat('<dispatched_at>'.replace('Z','+00:00'))
  now = datetime.now(timezone.utc)
  secs = int((now - dispatched).total_seconds())
  print(f'{secs // 60}m {secs % 60}s')
  "
  ```
- `tokens_spent`, `model_counts` — from the dispatch summary assembled in Segment D.
- `tasks` — `[{ "id": N, "model": "<short model name>" }]` for every task (the final
  banner draws its glyph row from this).
- `achieved` — ≤5 one-liners in plain value terms: the capability, fix, or improvement
  now shipped and why it matters to whoever uses the project. Describe what changed for
  the user, NOT how it was built — no file counts, task-type tallies, or implementation
  detail.
- `decision_points` — one line per pivot: Head Imp amendments, merge conflicts resolved,
  skipped gates/tasks, model escalations. Omit if none.

## 6. Mark the run finalized

Set `segment: "complete"` in the state file. Do **not** delete it yet — if you die
between here and the `done` checkpoint, the resume guard still finds the file and a
fresh wrangler re-emits `run_complete` instead of the run silently losing its
`.prs.json` handoff.

Now emit the `run_complete` checkpoint (schema in your brief), including
`learnings_candidates`, and wait for the orchestrator's `learnings:` relay.

## 7. Learnings write (on relay)

Candidates: anything surprising, wrong, or notably effective this run — dispatch
failures, task-boundary problems, Head Imp amendments that changed the plan, model
escalations (or haiku tasks that needed sonnet), merge conflicts and how they resolved,
PR branch issues, agent failures, checkpoint-protocol friction. Phrase each as a concise
**rule to apply next time**, not a description of what happened. Trivial runs (everything
worked, no surprises) → `learnings_candidates: []`.

The orchestrator replies `learnings: none` (→ emit
`{ "checkpoint": "done", "learnings_saved": [] }`) or `learnings: ["...", "..."]` — plain
confirmed-learning text, no scope. Classify each one's scope yourself:
**project-specific** (mentions this repo's stack, commands, file paths, or conventions)
→ `.claude/imps/learnings.md` in the repo root; **generally applicable** (model routing,
task boundaries, agent/dispatch patterns, checkpoint protocol) → stack-agnostic →
`~/.claude/imps/learnings.md`. Append each to its scoped file using this format:

```markdown
## Active rules
<!-- ≤10 bullets; promote confirmed learnings here when a pattern repeats across
     ≥2 runs; demote to run notes if it turns out to be one-off.
     User-scoped: keep stack-agnostic. Project-scoped: repo-specific rules are fine. -->

## YYYY-MM-DD — <project> <task description>
- <confirmed learning 1>
- <confirmed learning 2>
```

If `## Active rules` does not exist yet in a file, create it. If a confirmed learning
repeats something already in a past run entry of the same file, promote it into that
file's Active rules instead of appending a new run note. Keep each file's Active rules
≤10 bullets.

Finally, delete the run state file — `rm ~/.claude/imps/runs/<slug>.json` — and emit
the final checkpoint, including the scope you classified each learning into:
`{ "checkpoint": "done", "learnings_saved": [{"rule": "...", "scope": "..."}] }`.
(The GOAL.md spine at `~/.claude/imps/runs/<slug>.md` stays — it is the human-readable
record.)
