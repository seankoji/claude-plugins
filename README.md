# claude-plugins

A [Claude Code](https://code.claude.com/) plugin marketplace by [@seankoji](https://github.com/seankoji).

## Available plugins

| Plugin | Description |
|---|---|
| [elephant-goldfish](./plugins/elephant-goldfish/) | Self-validating `/elephant-goldfish:elephant` design-doc command + Gemini goldfish judge |
| [claude-tuneup](./plugins/claude-tuneup/) | Permission audit and settings tuneup for Claude Code |
| [prompt-builder](./plugins/prompt-builder/) | Iterative prompt engineering assistant |
| [imps](./plugins/imps/) | Swarm orchestrator — parallel model-routed agents, Workflow dispatch, deterministic gates, persona-review panel |
| [ape](./plugins/ape/) | Forages OSS repos for transferable techniques — discovery, ranking, cloning, analysis, and synthesis as a real Workflow script |
| [offload-sidecar](./plugins/offload-sidecar/) | MCP tool that offloads file transforms, log triage, and vision tasks — paths in, paths out, no file content through Claude's context. Local Ollama tiers (private) plus budget-gated Gemini tiers via the agy CLI. Formerly ollama-sidecar |
| [recon](./plugins/recon/) | Discovery → spec → verify → execute workflow for research, decisions, and reports — for Claude Cowork and Claude Code. Structural context isolation between phases via detection-and-refusal plus independent verification |

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
