# AGENTS.md — maintainer guide for claude-plugins

This file is loaded by Claude Code and other AI agents for sessions **inside this repo**.
It is for **marketplace maintainers**. Plugin *users* never see it.

Default branch: `master`.

---

## Layout

```
.claude-plugin/marketplace.json   # lists every plugin in plugins/
plugins/<name>/
  .claude-plugin/plugin.json      # this plugin's manifest
  commands/<name>.md              # the slash-command (its frontmatter is the source of truth)
  agents/<name>.md                # optional: subagent types this plugin registers on install
  scripts/*.sh                    # helpers; must be chmod +x
  README.md                       # user-facing docs for this plugin
README.md                         # marketplace overview + install table (one row per plugin)
```

---

## Add-a-plugin checklist

These five things must change **together** — missing one breaks the marketplace:

1. `plugins/<name>/.claude-plugin/plugin.json` — fill every required field
2. `.claude-plugin/marketplace.json` — add an entry under `"plugins"`
3. Root `README.md` "Available plugins" table — add one row
4. `plugins/<name>/README.md` — user-facing prerequisites, modes, env vars, license
5. `chmod +x plugins/<name>/scripts/*.sh` — every shipped helper must be executable

---

## Invariants

- **No machine paths.** Bundled scripts resolve themselves via `${CLAUDE_PLUGIN_ROOT}`.
  The pattern is already established in `goldfish-judge.sh` and `elephant.md` — match it.
- **Executable files are the source of truth.** `commands/*.md` owns mechanics;
  `scripts/*.sh` owns runtime behavior. READMEs *describe* them — don't restate or drift.
- **Fail-closed beats fail-open** everywhere. See `goldfish-judge.sh` for the pattern.

---

## Validate before committing

CI runs these checks automatically on every push and PR. For a quick local pre-commit check:
```bash
jq . .claude-plugin/marketplace.json && for f in plugins/*/.claude-plugin/plugin.json; do jq -e '.name' "$f"; done
grep -rn --include="*.md" 'CLAUDE_PLUGIN_ROOT' plugins/*/commands/ | head  # confirm rewrites landed
```
See .github/workflows/validate.yml for the full check suite.
