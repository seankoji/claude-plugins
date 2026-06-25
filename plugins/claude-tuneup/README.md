# claude-tuneup

A three-phase permission audit and settings tuneup command for [Claude Code](https://code.claude.com/) — scans recent transcripts to surface missing allowlist entries, classifies them by global vs project scope, deduplicates across settings files, and self-logs findings so each run is smarter than the last.

---

## What it does

Most permission prompts are avoidable. `claude-tuneup` scans what Claude actually ran, classifies it, and proposes the minimum set of rules needed to stop the prompts — without over-permissioning.

**Phase 1 — Scan**

Reads the last 50 session transcripts and builds frequency tables for Bash commands, MCP tool calls, and SSH remote subcommands. Surfaces candidates with count ≥ 3 that aren't already covered by an existing allow rule, then classifies each as **global** (applies everywhere) or **project** (specific to this repo). Pre-filters anything already auto-allowed by the Claude Code harness so you're never shown a rule you'd never be prompted for anyway.

**Phase 2 — Audit**

Compares `~/.claude/settings.json` against `.claude/settings.json` (and `.claude/settings.local.json` if present) and proposes:

- Strip exact duplicates that appear in both files
- Strip entries in the narrower file that are prefix-subsumed by a broader rule in any file
- Move project-specific entries out of global settings
- Move generic entries up from project settings to global
- Flag stale MCP server entries whose server is no longer connected
- Flag env vars that belong to a different project
- Flag CLAUDE.md content in the wrong scope (never auto-edits — print only)

**Phase 3 — Self-reflect**

After each run, appends a dated entry to `~/.claude/claude-tuneup.notes.md` noting what the scan missed, what the audit flagged, and any recurring papercuts. When the same finding appears in two or more distinct runs, proposes a concrete edit to the command itself — with your explicit approval before anything changes.

---

## Prerequisites

Claude Code only — no external tools required. The transcript scanner (`scan_perms.py`) is bundled with this plugin and invoked automatically.

---

## Install

```bash
claude plugin marketplace add seankoji
claude plugin install claude-tuneup@seankoji
```

---

## Usage

```
/claude-tuneup:claude-tuneup [flags]
```

Run from any git repo (or globally — Phase 2 always audits the global settings file regardless of where you invoke it).

### Flags

| Flag | Effect |
|---|---|
| `--scan-only` | Phase 1 only — propose new allowlist entries, skip the audit |
| `--audit-only` | Phase 2 only — audit existing settings, skip the transcript scan |
| `--dry-run` | Print all proposals from Phases 1 and 2; do not write anything. Skips Phase 3. |
| `--no-reflect` | Run Phases 1 and 2 but skip the self-reflect log (Phase 3) |
| _(no flag)_ | All three phases in order: scan → audit → reflect |

Default (no flag) is recommended: running Phase 1 before Phase 2 ensures that any new entries added by the scan are immediately checked by the audit for duplicates.

---

## Notes

- **Phase 1 requires `scan_perms.py`** — bundled with this plugin under `scripts/`. It is invoked automatically as `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scan_perms.py`. If the script is absent for any reason, Phase 1 is skipped and the run falls back to `--audit-only` automatically.
- The transcript scanner is read-only — `--audit-only` is safe to re-run at any time.
- Permission rules are exact-prefix match: `Bash(npm *)` covers `Bash(npm test)` but not vice versa. The command prefers the broader rule when classifying new additions.
- Env vars flagged in Phase 2 are **never auto-removed** — they require explicit confirmation via `AskUserQuestion`.
- CLAUDE.md scope findings are printed as `file:line` notes and never auto-edited.
- If `~/.claude/settings.json` is a symlink (common in dotfiles setups), the command follows it to the real file before writing.
- The self-reflect log lives at `~/.claude/claude-tuneup.notes.md` — one level above `commands/` so it is not auto-registered as a slash command.
- To remove: `rm ~/.claude/commands/claude-tuneup.md`. The built-in `/fewer-permission-prompts` covers Phase 1 alone.

---

## License

MIT
