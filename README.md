# claude-plugins

A [Claude Code](https://code.claude.com/) plugin marketplace by [@seankoji](https://github.com/seankoji).

## Available plugins

| Plugin | Description |
|---|---|
| [elephant-goldfish](./plugins/elephant-goldfish/) | Self-validating `/elephant` design-doc command + Gemini goldfish judge |
| [imps](./plugins/imps/) | Swarm orchestrator — parallel model-routed agents, Workflow dispatch, deterministic gates, persona-review panel |

---

## Install

```bash
# Add the marketplace (one-time)
claude plugin marketplace add seankoji/claude-plugins

# Install a plugin
claude plugin install elephant-goldfish@claude-plugins

# Install project-scoped (shared with teammates via .claude/)
claude plugin install elephant-goldfish@claude-plugins --scope project

# Keep marketplace up to date
claude plugin marketplace update
```
