# claude-plugins

A [Claude Code](https://code.claude.com/) plugin marketplace by [@seankoji](https://github.com/seankoji).

---

## Install the marketplace

```bash
claude plugin marketplace add seankoji/claude-plugins
```

## Available plugins

| Plugin | Description | Install |
|---|---|---|
| [elephant-goldfish](./plugins/elephant-goldfish/) | Self-validating `/elephant` design-doc command + Gemini goldfish judge | `claude plugin install elephant-goldfish@claude-plugins` |

---

## Adding a plugin to your project

```bash
# Add the marketplace (one-time)
claude plugin marketplace add seankoji/claude-plugins

# Install a plugin
claude plugin install elephant-goldfish@claude-plugins

# Install project-scoped (shared with teammates via .claude/)
claude plugin install elephant-goldfish@claude-plugins --scope project
```

## Updating

```bash
claude plugin marketplace update
```
