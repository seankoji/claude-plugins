---
description: >
  Use when the current project needs broad discovery across multiple OSS repositories
  for transferable techniques. Do not use when the user already named one GitHub source;
  use /ape:study for that depth-first comparison.
argument-hint: [focus area, e.g. testing | architecture | dx — optional]
allowed-tools: Task, Workflow, Read, Write, Glob, Grep, Bash(gh:*), Bash(mkdir:*), Bash(cp:*), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/init-workspace.sh:*), Bash(tree:*), Bash(ls:*), Bash(cat:*), Bash(du:*)
disable-model-invocation: true
---

Forage open-source repositories for techniques transferable to this project.

Focus area: $ARGUMENTS (if empty: architecture, testing, and developer experience broadly).

Workspace: `~/tmp/repo-research/<project-slug>/` where the slug is the current directory's basename. Every phase writes its artifacts here so synthesis can be re-run later without re-foraging, and so the user can inspect raw outputs.

## Phase 0 — Preflight + fingerprint (you; no subagents, no Workflow yet)

1. Run `gh auth status`. If unauthenticated, stop and tell the user to run `gh auth login` — nothing downstream works without it.
2. Run `${CLAUDE_PLUGIN_ROOT}/scripts/init-workspace.sh` — creates a fresh `reports/run.<random>/` directory under the workspace. Keep the emitted `workspace=` and `reports=` values; never reuse a report directory for a new expedition. Recovery below deliberately reuses the saved run.
3. Inspect the current project and write `<fresh-report-directory>/fingerprint.md` on every run (≤150 words): stack, domain, architecture, notable existing patterns, 3–5 current weaknesses relevant to the focus area, and an explicit **already-in-use** list of techniques and tooling. Include the current commit and focus. Treat an old fingerprint only as a checklist to recheck against current files, including dirty changes. Nothing on the already-in-use list may be recommended later.
4. Show the fingerprint to the user before dispatching anything. It gates every downstream token — a wrong fingerprint produces convergent garbage at scale.

## Phase 1 — Sync the Workflow script

Workflow scripts only load from `~/.claude/workflows/*.js` — a plugin cannot ship one that runs directly. Each run, re-sync the bundled canonical copy over the previous one so it always matches the installed plugin version (a plain overwrite, not a version/hash check — simpler and can't drift). **The `Workflow` tool call below is not Bash — it does not expand `~`,** so resolve and echo the absolute path here first, and pass that literal echoed value (never the `~/...` form) into Phase 2:

```bash
mkdir -p ~/.claude/workflows
cp "${CLAUDE_PLUGIN_ROOT}/scripts/ape-forage.workflow.js" ~/.claude/workflows/ape-forage.js
WORKFLOW_DEST="$HOME/.claude/workflows/ape-forage.js"
echo "$WORKFLOW_DEST"
```

## Phase 2 — Run the expedition

Invoke the `Workflow` tool:

```
Workflow({
  scriptPath: "<the echoed $WORKFLOW_DEST value, e.g. /Users/you/.claude/workflows/ape-forage.js>",
  args: {
    pluginRoot: "${CLAUDE_PLUGIN_ROOT}",
    fingerprint: "<the full fingerprint content from Phase 0>",
    focusArea: "<focus area, or \"architecture, testing, and developer experience broadly\" if empty>",
    workspaceDir: "<the workspace dir from Phase 0>",
    reportsDir: "<the fresh reports= directory from Phase 0>"
  }
})
```

This runs in the background — discovery (3 axes in parallel), a ranking judgment call, cloning with one automatic retry on failure, per-repo analysis (in parallel), and synthesis are all real control flow inside the script now, not a hand-rolled checkpoint/resume protocol. Tell the user the expedition is running and that you'll report back when it completes — then stop; you'll be notified automatically.

**On completion, branch on the returned `status`:**

- **`final`** — this is the expedition's deliverable, not a status update. Present the `recommendations` field to the user directly, verbatim — do not re-summarize it, and do not read `RECOMMENDATIONS.md` or `reports/*.md` yourself to "check" it. Mention `nearMisses` if non-empty, and the `stats` line (repos analyzed, techniques surfaced).
  If `failed_analysts` is nonempty, list those repositories and label the result partial.
  Their cached reports were excluded; never claim the original breadth of coverage.
- **`blocked` (`reason: "no_candidates"`)** — nothing survived discovery/triage/ranking. Tell the user and stop; a fresh run is the only way forward (a different focus area may surface more candidates).
- **`blocked` (`reason: "clone_failed"`)** — surface the `failed` list to the user. Once they've addressed the cause (auth, rate-limit, disk space), re-run `/ape:forage` with a refreshed fingerprint and a fresh report directory.

- **`blocked` (`reason: "missing_reports"`)** — report the failed analysts or missing files. Do not substitute cached reports or claim the expedition completed.
- **`blocked` (`reason: "report_verification_failed"`)** — the verifier failed twice. Surface the returned report paths and keep the research files. Save the original Workflow `args` plus the returned `resume` object to `<reportsDir>/resume.json` with `Write`; report any write failure. Do not claim recommendations or complete coverage.
- **`blocked` (`reason: "invalid_reports_dir"`)** — run Phase 0 again and pass its fresh report directory.
- **Recovery requested for a saved run** — read its `resume.json`, sync the script as in
  Phase 1, then call `Workflow` with those saved arguments, including `resume`. Skip
  Phase 0 and the new-expedition argument construction. The runtime validates the exact
  report list, retries file verification, and synthesizes only after verification passes;
  discovery, clone, and analysis are skipped. Preserve the saved fingerprint and focus,
  and label the output as a recovery of that run rather than analysis of current changes.
- **`blocked` (`reason: "invalid_resume"`)** — the saved report list does not match the
  run directory. Report the mismatch; never replace it with a glob of cached reports.

**If the `Workflow` tool is unavailable in this session:** tell the user this command requires the `Workflow` tool and stop — there is no prose fallback path.
