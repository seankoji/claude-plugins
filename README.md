# claude-plugins

A [Claude Code](https://code.claude.com/) plugin marketplace by [@seankoji](https://github.com/seankoji).

## Available plugins

| Plugin | Description |
|---|---|
| [elephant-goldfish](./plugins/elephant-goldfish/) | Self-validating `/elephant-goldfish:elephant` design-doc command + Gemini goldfish judge |
| [claude-tuneup](./plugins/claude-tuneup/) | Permission audit and settings tuneup for Claude Code |
| [prompt-builder](./plugins/prompt-builder/) | Iterative prompt engineering assistant |
| [imps](./plugins/imps/) | Swarm orchestrator — parallel model-routed agents, Workflow dispatch, deterministic gates, persona-review panel |

---

## Install

```bash
# Add the marketplace (one-time)
claude plugin marketplace add seankoji/claude-plugins

# Install a plugin
claude plugin install elephant-goldfish@seankoji

# Install project-scoped (shared with teammates via .claude/)
claude plugin install elephant-goldfish@seankoji --scope project

# Keep marketplace up to date
claude plugin marketplace update
```

---

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for how to add a plugin, test changes locally, and open a PR.
