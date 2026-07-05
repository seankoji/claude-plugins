# imps — swarm orchestrator for Claude Code

## What it does

`/imps:imps` decomposes a task (or a batch of GitHub issues) into parallel, model-routed
agents ("imps"), dispatches them via the Workflow tool, and integrates results through
deterministic gates and an adversarial persona-review panel.

The orchestrating session is deliberately thin: it holds decisions (plan approval, the
push/PR gate, conflict resolution) while everything bulky — repo recon, merges, diffs,
gate logs, persona traffic — runs inside subagents that return compact JSON. Free-text
mode's whole integration phase is delegated to a single **Imp Wrangler** subagent that
reports back in checkpoints, so long runs don't grind the main context down.

## Prerequisites

| Requirement | Needed for |
| --- | --- |
| **Workflow tool** | Free-text mode dependency-graph dispatch. Degrades to sequential `Agent` calls if unavailable. |
| **`gh` CLI** (authenticated) | Issue-driven mode (issue reads, PR creates, CI checks). |
| **GitHub MCP** (`mcp__github__*`) | PR/issue reads in `/imps:prs`; improves issue-driven mode. |
| **Bundled agent types** (`imp`, `head-imp`, `imp-wrangler`) | Registered automatically once installed (`agents/*.md`). If registration fails for any reason, the commands fall back to `general-purpose` (the wrangler fallback prepends its brief to the prompt). |

Optional:

| Requirement | Needed for |
| --- | --- |
| **`CLAUDE_CDP_URL`** env var | Browser panel via CDP (default `ws://localhost:3000`). Point at a headless-Chrome container, local or LAN. |
| **Claude-in-Chrome MCP** | Browser panel fallback if no CDP endpoint is reachable. |
| **`~/.claude/scripts/persona-post.sh`** + dedicated GitHub Apps (`mm-solution-architect`, `mm-grumpy-engineer`, `mm-sre`, `mm-business-analyst`, `mm-ux-designer`) installed on the target repo, with their credentials in 1Password | Independent, per-persona review identity for the persona panel (see [The persona panel](#the-persona-panel)). Absent or failing → that persona falls back to an orchestrator-identity `[Persona: <Name>]` comment, clearly marked as degraded. |

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

Four entry modes, auto-detected from the argument:

| Invocation | Mode | What it does |
| --- | --- | --- |
| `/imps:imps <free-text task>` | Free-text | Refine → plan (opus plan mode) → decompose → dispatch a Workflow → merge → gates → persona panel → endstate PR |
| `/imps:imps 42 43 51` | Issue-driven | Scout issues → rolling dispatch in isolated worktrees → holding branch → gates → persona panel → operator handoff |
| `/imps:imps https://github.com/<owner>/<repo>/discussions/284` | Discussion-seed | Fetch the discussion via GraphQL, seed it as the free-text task, run the normal free-text flow, and always post a summary comment back to the discussion at the end |
| `/imps:imps path/to/checklist.md` | Checklist-file | Run each `Verify:`/`Done when:` item as a read-only audit, then offer remediation dispatch |

### Free-text mode walkthrough

1. `/imps:imps` with a task description (or empty — it will ask).
2. `/imps:imps` refines the brief via `prompt-builder`, asks five discovery questions, then enters plan mode (opus) to decompose and write `GOAL.md` (to `~/.claude/imps/runs/<slug>.md`, not the repo — see [Runtime state](#runtime-state)).
3. The Head Imp (opus) adversarially reviews the plan; findings are addressed before dispatch.
4. After plan approval, `/imps:imps` dispatches a Workflow and returns control — progress is visible via `/workflows`.
5. When the Workflow completes, `/imps:imps` hands integration to the **Imp Wrangler** subagent: it merges code branches, drives the Head Imp diff review, runs gates, then — after you approve the push — opens the endstate PR (the default for runs that change code — decline the push to skip it), runs the persona panel on that PR, and applies any fixes. The main session only relays your decisions and can hand the PR to the `/imps:prs` monitor.

### Issue-driven mode walkthrough

1. `/imps:imps 42 43 51` — all tokens are issue numbers.
2. Scouts (haiku) fan out in parallel per issue; results seed the implementation queue.
3. Implementation agents run in isolated worktrees up to `PARALLEL_CAP=6` concurrent; file-overlapping issues serialize naturally.
4. After all issues merge into the holding branch, `/imps:imps` runs full gates, opens the integration PR, and runs the persona panel.
5. Operator handoff — `/imps:imps` does NOT merge the integration PR.

### Discussion-seed mode walkthrough

1. `/imps:imps https://github.com/<owner>/<repo>/discussions/284` (or the bare `discussion 284` inside that repo).
2. `/imps:imps` fetches the discussion's title, body, and comments via `gh api graphql` (Discussions have no REST endpoint) and uses that content as the task description, skipping the "what's the task?" prompt.
3. Everything from there follows the free-text mode walkthrough above (discovery → plan → dispatch → integration).
4. Regardless of what the discovery answers say about output artifacts, `/imps:imps` always posts one summary comment back to the source discussion once the run finishes — this is not optional and does not require a PR to exist.

### Checklist-file mode

Pass a single `.md` token that resolves to a file with `- [ ]` checklist items, each having `Verify:` and `Done when:` sub-lines. `/imps:imps` fans out read-only verification imps and emits a pass/fail audit report, then offers to dispatch remediation.

### Direct `/imps:issue-mode` invocation & handoff contract

Issue-driven mode is also directly invokable as `/imps:issue-mode` — useful for an upstream
audit or handoff tool that wants to skip `/imps:imps`'s mode detection. It accepts either bare
issue numbers (`/imps:issue-mode 42 43 51`) or a structured JSON input:

```json
{ "issues": [42, 43, 51, 60], "holdingBranch": "audit/2026-06-12" }
```

- `issues` (required) — the issue numbers to work (capped at `ISSUE_CAP=200`).
- `holdingBranch` (optional) — the branch to integrate onto; defaults to
  `swarm/<YYYY-MM-DD>` cut fresh from the repo's default branch. If the branch and its
  tracking issue already exist, the run resumes from the first incomplete phase.

## Sub-command

Self-rescheduling via `ScheduleWakeup` — do NOT wrap it with `/loop`.

- **`/imps:prs`** — proactive PR monitor. After `/imps:imps` pushes and creates the endstate PR, activate this to automatically address review comments, fix CI failures, and resolve merge conflicts. Stops when the PR is merged, closed, or 48 h old.

## The persona panel

Five reviewer briefs, each argued from a distinct, deliberately-conflicting lens.
Bundled at `${CLAUDE_PLUGIN_ROOT}/personas/` — no manual setup needed.

| Slug | Name | Lens | Type | Model |
| --- | --- | --- | --- | --- |
| `solution-architect` | Bramble | Boundaries, contracts, coupling — "should this exist, in this shape?" | code | opus |
| `grumpy-engineer` | Grudge | Edge cases, error paths, lazy shortcuts — "is this line correct?" | code | opus |
| `sre` | Klaxon | Failure modes, ops, idempotency, resource limits — "what does the operator see at 3am?" | code | opus |
| `business-analyst` | Ledger | Diff satisfies each issue's acceptance criteria — "did we build the right thing?" | code | opus |
| `ux-designer` | Glint | Hierarchy, affordance, consistency, mobile — "what does the user actually see?" | browser | sonnet |

Each persona ends its review with a parseable verdict line:
```
VERDICT: APPROVE | CHANGES_REQUESTED @ <sha>
```
`CHANGES_REQUESTED` requires at least one `[blocker]` or `[major]` finding. Minors and
nits are recorded but never block. By default each persona posts as a **real GitHub PR
review under its own dedicated GitHub App identity** (`mm-solution-architect`,
`mm-grumpy-engineer`, `mm-sre`, `mm-business-analyst`, `mm-ux-designer`) via
`~/.claude/scripts/persona-post.sh` — never the orchestrator's own `gh`/GitHub-MCP
access, so each review is attributed to and traceable as a genuinely separate GitHub
actor, not the session that authored the diff. This is independent *attribution*, not
an unforgeable *gate*: the orchestrator still holds the credentials `persona-post.sh`
uses to mint every App's token, so it isn't a control the authoring session is
structurally unable to satisfy — it fixes the previous self-approval-under-one's-own-
name problem, not every trust concern a branch-protection rule might assume. If that
script is absent, fails, or its post can't be verified on the PR for a given persona
(Apps not installed on this repo, 1Password locked, no `op` access, etc.), that persona
alone falls back to an ordinary `[Persona: <Name>]` comment under the orchestrator's own
identity, clearly marked in the comment body as a degraded, non-independent review — the
rest of the panel is unaffected.

## Bundled assets

| Asset | Location |
| --- | --- |
| Persona briefs (5) | `${CLAUDE_PLUGIN_ROOT}/personas/<slug>.md` |
| `imp` agent type | `${CLAUDE_PLUGIN_ROOT}/agents/imp.md` |
| `head-imp` agent type | `${CLAUDE_PLUGIN_ROOT}/agents/head-imp.md` |
| `imp-wrangler` agent type | `${CLAUDE_PLUGIN_ROOT}/agents/imp-wrangler.md` |
| Summon banner (cosmetic) | `${CLAUDE_PLUGIN_ROOT}/scripts/imps-intro.py` |

No manual setup needed for any of these — the plugin installs them at
`${CLAUDE_PLUGIN_ROOT}` and the commands resolve them at runtime. The workflow
script inlines its own copy of the Head Imp persona for calls made from inside
a `Workflow` script (see below); the bundled `head-imp` agent type is what
resolves when it, or anything else, invokes it directly via the `Agent` tool
(`Agent(subagent_type: "head-imp", ...)`).

## Runtime state

Written to `~/.claude/imps/` on first run — not bundled:

| Path | Purpose |
| --- | --- |
| `~/.claude/imps/runs/<slug>.json` | Per-project dispatch state — resume + integration spine |
| `~/.claude/imps/runs/<slug>.md` | Per-run `GOAL.md` spine (`/compact`-durable) — lives here, not in the repo, so writing it never needs project-directory permission |
| `~/.claude/imps/runs/<slug>.prs.json` | Per-PR monitor state for `/imps:prs` |
| `~/.claude/imps/learnings.md` | Self-tuning `## Active rules` (≤10 bullets) + per-run notes |

The `learnings.md` `## Active rules` section is read at startup on every run and applied
to model routing, task boundaries, and dependency detection. `/imps:imps` appends a new run
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
