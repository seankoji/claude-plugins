# imps — swarm orchestrator for Claude Code

`/imps` decomposes a task (or a batch of GitHub issues) into parallel, model-routed
agents ("imps"), dispatches them via the Workflow tool, monitors progress through a
self-rescheduling heartbeat, and integrates results through deterministic gates and an
adversarial persona-review panel.

## Install

```sh
claude plugin install imps@seankoji
```

Or via the marketplace:

```sh
claude plugin marketplace add seankoji/claude-plugins
claude plugin install imps@seankoji
```

## Usage

Three entry modes, auto-detected from the argument:

| Invocation | Mode | What it does |
| --- | --- | --- |
| `/imps <free-text task>` | Free-text | Refine → plan (opus plan mode) → decompose → dispatch a Workflow → merge → gates → persona panel → endstate PR |
| `/imps 42 43 51` | Issue-driven | Scout issues → rolling dispatch in isolated worktrees → holding branch → gates → persona panel → operator handoff |
| `/imps path/to/checklist.md` | Checklist-file | Run each `Verify:`/`Done when:` item as a read-only audit, then offer remediation dispatch |

### Free-text mode walkthrough

1. `/imps` with a task description (or empty — it will ask).
2. `/imps` refines the brief via `prompt-builder`, asks five discovery questions, then enters plan mode (opus) to decompose and write `GOAL.md`.
3. The Head Imp (opus) adversarially reviews the plan; findings are addressed before dispatch.
4. After plan approval, `/imps` dispatches a Workflow and starts the `/imps:status` heartbeat.
5. When the Workflow completes, `/imps` merges code branches, runs gates, then opens the endstate PR (the default for runs that change code — decline the push to skip it), runs the persona panel on that PR, applies any fixes, and can hand the PR to the `/imps:prs` monitor.

### Issue-driven mode walkthrough

1. `/imps 42 43 51` — all tokens are issue numbers.
2. Scouts (haiku) fan out in parallel per issue; results seed the implementation queue.
3. Implementation agents run in isolated worktrees up to `PARALLEL_CAP=6` concurrent; file-overlapping issues serialize naturally.
4. After all issues merge into the holding branch, `/imps` runs full gates, opens the integration PR, and runs the persona panel.
5. Operator handoff — `/imps` does NOT merge the integration PR.

### Checklist-file mode

Pass a single `.md` token that resolves to a file with `- [ ]` checklist items, each having `Verify:` and `Done when:` sub-lines. `/imps` fans out read-only verification imps and emits a pass/fail audit report, then offers to dispatch remediation.

### Direct `/imps:issue-mode` invocation & handoff contract

Issue-driven mode is also directly invokable as `/imps:issue-mode` — useful for an upstream
audit or handoff tool that wants to skip `/imps`'s mode detection. It accepts either bare
issue numbers (`/imps:issue-mode 42 43 51`) or a structured JSON input:

```json
{ "issues": [42, 43, 51, 60], "holdingBranch": "audit/2026-06-12" }
```

- `issues` (required) — the issue numbers to work (capped at `ISSUE_CAP=200`).
- `holdingBranch` (optional) — the branch to integrate onto; defaults to
  `swarm/<YYYY-MM-DD>` cut fresh from the repo's default branch. If the branch and its
  tracking issue already exist, the run resumes from the first incomplete phase.

## Sub-commands

Both sub-commands are self-rescheduling via `ScheduleWakeup` — do NOT wrap them with `/loop`.

- **`/imps:status`** — heartbeat for active runs. Shows which imps are still out, elapsed time, and dependency-waiting detail. Stops automatically when the state directory is empty (run complete).
- **`/imps:prs`** — proactive PR monitor. After `/imps` pushes and creates the endstate PR, activate this to automatically address review comments, fix CI failures, and resolve merge conflicts. Stops when the PR is merged, closed, or 48 h old.

## Prerequisites

| Requirement | Needed for |
| --- | --- |
| **Workflow tool** | Free-text mode dependency-graph dispatch. Degrades to sequential `Agent` calls if unavailable. |
| **`gh` CLI** (authenticated) | Issue-driven mode (issue reads, PR creates, CI checks). |
| **GitHub MCP** (`mcp__github__*`) | PR/issue reads in `/imps:prs`; improves issue-driven mode. |
| **`imp` agent type** (optional) | Used only if your runtime registers an `imp` agent type. This plugin does **not** ship one, so out of the box every task runs on `general-purpose` — the workflow detects the missing type and falls back automatically. The "atomic-task discipline / branch handling / structured-output" conventions are baked into the prompts either way. |

Optional:

| Requirement | Needed for |
| --- | --- |
| **`CLAUDE_CDP_URL`** env var | Browser panel via CDP (default `ws://localhost:3000`). Point at a headless-Chrome container, local or LAN. |
| **Claude-in-Chrome MCP** | Browser panel fallback if no CDP endpoint is reachable. |

## The persona panel

Five reviewer briefs, each argued from a distinct, deliberately-conflicting lens.
Bundled at `${CLAUDE_PLUGIN_ROOT}/personas/` — no manual setup needed.

| Slug | Name | Lens | Type | Model |
| --- | --- | --- | --- | --- |
| `solution-architect` | Alex | Boundaries, contracts, coupling — "should this exist, in this shape?" | code | opus |
| `grumpy-engineer` | Jordan | Edge cases, error paths, lazy shortcuts — "is this line correct?" | code | opus |
| `sre` | Riley | Failure modes, ops, idempotency, resource limits — "what does the operator see at 3am?" | code | opus |
| `business-analyst` | Morgan | Diff satisfies each issue's acceptance criteria — "did we build the right thing?" | code | opus |
| `ux-designer` | Sam | Hierarchy, affordance, consistency, mobile — "what does the user actually see?" | browser | sonnet |

Each persona ends its review with a parseable verdict line:
```
VERDICT: APPROVE | CHANGES_REQUESTED @ <sha>
```
`CHANGES_REQUESTED` requires at least one `[blocker]` or `[major]` finding. Minors and
nits are recorded but never block. Personas post as ordinary comments prefixed
`[Persona: <Name>]` using the orchestrator's own GitHub access — no per-persona
credentials required.

## Bundled assets

| Asset | Location |
| --- | --- |
| Persona briefs (5) | `${CLAUDE_PLUGIN_ROOT}/personas/<slug>.md` |
| Summon banner (cosmetic) | `${CLAUDE_PLUGIN_ROOT}/scripts/imps-intro.py` |

No manual setup needed for any of these — the plugin installs them at
`${CLAUDE_PLUGIN_ROOT}` and the commands resolve them at runtime.

## Runtime state

Written to `~/.claude/imps/` on first run — not bundled:

| Path | Purpose |
| --- | --- |
| `~/.claude/imps/runs/<slug>.json` | Per-project dispatch state — resume spine + heartbeat source |
| `~/.claude/imps/runs/<slug>.prs.json` | Per-PR monitor state for `/imps:prs` |
| `~/.claude/imps/learnings.md` | Self-tuning `## Active rules` (≤10 bullets) + per-run notes |

The `learnings.md` `## Active rules` section is read at startup on every run and applied
to model routing, task boundaries, and dependency detection. `/imps` appends a new run
entry after each completed run; confirmed learnings are promoted into Active rules.

## Browser review (optional)

The persona panel includes a browser half when the diff touches a renderable UI surface.
Transport is resolved in order:

1. **CDP endpoint** — set `CLAUDE_CDP_URL` (e.g. `ws://localhost:3000` or
   `ws://<lan-host>:3000` for a remote rig). Connect via `chromium.connectOverCDP` —
   never `connect()` (hangs); never pass `http://` (returns 426).
2. **Chrome MCP fallback** — if no CDP endpoint is reachable, the panel uses
   `mcp__claude-in-chrome__*` tools (requires the Claude-in-Chrome extension).
3. **No browser** — neither available → panel runs code-only; skip is noted in the report.

Repos with no UI surface skip the browser half entirely.

## License

MIT
