## Active rules

<!-- ≤10 bullets; promote confirmed learnings here when a pattern repeats across
     ≥2 runs; demote to run notes if it turns out to be one-off.
     Project-scoped: repo-specific rules are fine. -->

- Shell scripts shipped in `plugins/*/scripts/*.sh` run on the operator's own machine, and
  the stock `/bin/bash` on macOS is still 3.2.57 (last GPLv2 release) — it lacks bash 4+
  niceties, most sharply: `"${arr[@]}"` on an empty array throws "unbound variable" under
  `set -u`/`set -euo pipefail` instead of expanding to zero words. Any new possibly-empty
  array used in a command substitution needs the `"${arr[@]+"${arr[@]}"}"` idiom, not the
  bare form. Test shipped scripts against the real `/bin/bash` on macOS, not just Linux bash
  or zsh.
- CI gate logic in `.github/workflows/validate.yml` runs under bash (GitHub Actions'
  `run:` default), which can behave differently from this repo's interactive dev shell
  (zsh) for constructs like `for x in $multiline_var` (word-splitting differs). Always
  dry-run new gate steps with an explicit `bash -c '...'`, not the ambient shell, before
  trusting a local pass/fail result.
- The persona-review panel (`plugins/imps/personas/*.md`) is a self-critique/adversarial
  mechanism run by the same orchestrating session that authored the diff — it is not
  independent third-party validation, and framing it that way to the operator would be
  misleading. The actual external safeguard is that `/imps` never merges the integration
  PR itself; the human operator is the real final gate.

## 2026-07-02 — claude-plugins swarm/2026-07-01 audit-fix batch (5 issues)

**Outcome:** #13 (imps), #14 (elephant-goldfish), #15 (claude-tuneup), #16 (prompt-builder),
#17 (marketplace-level) all resolved and merged into holding branch `swarm/2026-07-01`;
integration PR #25 green, persona panel APPROVE after 2 rounds; handed off, not merged.
**What worked** — Scouting file-overlap up front (issue #17 touches every other issue's
README/plugin.json) and serializing it after the other four avoided any merge conflicts
across 5 issues.
**What caused rework** — scan_perms.py's leader-token fold-in for issue #15 didn't account
for `timeout`/`env` carrying a variable argument (duration, `VAR=val`), producing an
allowlist rule that could never match the real invocation — caught by the Head Imp, not
the implementing agent. The new `goldfish-judge.sh` timeout wrapper (issue #14) hit the
bash-3.2 empty-array bug above — caught by the grumpy-engineer persona, not local testing,
because the local dry-run used zsh/bash4 semantics where the bug doesn't reproduce.
**Routing notes** — 5-issue batches with mostly-disjoint plugin directories parallelize
cleanly; only the marketplace-level cross-cutting issue needed serialization.
