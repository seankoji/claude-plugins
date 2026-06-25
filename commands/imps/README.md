# /imps — the swarm orchestrator

`/imps` decomposes a task (or a batch of GitHub issues) into parallel, model-routed
agents ("imps"), dispatches them, monitors progress, and integrates the results through
deterministic gates and an adversarial persona-review panel.

It has three entry modes, auto-detected from the argument:

| Invocation | Mode | What it does |
| --- | --- | --- |
| `/imps <free-text task>` | Free-text | Refine → plan (plan mode) → decompose → dispatch a Workflow → merge → gates → panel → endstate PR |
| `/imps 42 43 51` | Issue-driven | Scout issues → rolling dispatch in worktrees → holding branch → gates → panel → operator handoff |
| `/imps path/to/checklist.md` | Checklist | Run each `Verify:`/`Done when:` item as a read-only audit, then offer remediation |

Sub-commands, both self-rescheduling:

- `/imps:status` — heartbeat for active runs (shows which imps are still out).
- `/imps:prs` — proactive monitor that auto-fixes review comments, CI failures, and
  merge conflicts on the endstate PR.

## Install

Copy the commands and personas into your Claude Code config:

```sh
# commands (registers /imps, /imps:status, /imps:prs)
cp commands/imps.md            ~/.claude/commands/
cp -r commands/imps            ~/.claude/commands/

# persona briefs (read by the review panel)
mkdir -p ~/.claude/imps/personas
cp personas/*.md               ~/.claude/imps/personas/

# optional cosmetic summon banner
cp scripts/imps-intro.py       ~/.claude/imps/imps-intro.py
```

Runtime state and learnings are created on demand under `~/.claude/imps/`:

- `~/.claude/imps/personas/<slug>.md` — the five reviewer briefs
- `~/.claude/imps/learnings.md` — self-tuning `## Active rules` (≤10 bullets) + run notes
- `~/.claude/imps/runs/<slug>.json` — per-project dispatch state (the resume + heartbeat spine)
- `~/.claude/imps/runs/<slug>.prs.json` — per-PR monitor state

## Requirements

- The **Workflow** tool (for the dependency-graph dispatch in free-text mode). If it's
  unavailable, `/imps` degrades to sequential `Agent` calls.
- The **`gh`** CLI authenticated to the target repo (issue-driven mode), and ideally the
  GitHub MCP (`mcp__github__*`) for PR/issue reads.
- A subagent type named `imp` is preferred; the command falls back to `general-purpose`
  automatically if it isn't registered.

## Browser review (optional)

The review panel includes a browser half when the diff touches a renderable UI surface.
It needs a Chrome/Chromium instance, found in this order:

1. **CDP endpoint** — set `CLAUDE_CDP_URL` (default `ws://localhost:3000`). Point it at a
   headless-Chrome container, local or on a LAN host
   (e.g. `export CLAUDE_CDP_URL=ws://<lan-host>:3000` for a remote rig). The panel
   connects with `chromium.connectOverCDP(process.env.CLAUDE_CDP_URL)` — use
   `connectOverCDP`, never `connect()` (it hangs); an `http://` URL returns 426.
2. **Chrome MCP fallback** — if no CDP endpoint is reachable, the panel drives the browser
   through the `mcp__claude-in-chrome__*` tools (requires the Claude-in-Chrome extension
   connected to the session).
3. **No browser** — neither available → the panel runs code-only and notes the skip.

Repos with no UI surface skip the browser half entirely.

## The persona panel

Five reviewer briefs, each argued from a distinct, deliberately-conflicting lens. Drop
or add briefs in `~/.claude/imps/personas/` to retune the panel.

| Slug | Lens | Type |
| --- | --- | --- |
| `solution-architect` | boundaries, contracts, coupling | code |
| `grumpy-engineer` | edge cases, error paths, lazy shortcuts | code |
| `sre` | failure modes, ops, idempotency, resource limits | code |
| `business-analyst` | does the diff satisfy each issue's acceptance criteria | code |
| `ux-designer` | hierarchy, affordance, consistency, mobile | browser |

Each persona ends its review with a parseable line:

```
VERDICT: APPROVE | CHANGES_REQUESTED @ <sha>
```

`CHANGES_REQUESTED` requires at least one `[blocker]` or `[major]` finding; minors and
nits are recorded but never block. By default personas post as ordinary comments prefixed
`[Persona: <Name>]` using the orchestrator's own GitHub access — no per-persona bot
identity or credential setup is required.
