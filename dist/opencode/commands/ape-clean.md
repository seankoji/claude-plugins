---
description: >
  Use only when the operator wants to delete ape's cloned repositories for this project.
  Keeps reports unless --all is explicit; do not use for ordinary workspace cleanup.
argument-hint: [--all to wipe the workspace | --run <path> to delete one saved run]
---

🐒 This is the "I say so" step — the only sanctioned way to delete ape's clones.

1. Use the actual workspace path returned by forage (`workspace=`), whose slug includes
   the remote origin and directory name. If unavailable, use a bounded `Glob` under
   `~/tmp/repo-research/` to locate this project's workspace and verify its repository
   identity before deletion; do not guess from the basename alone. If absent, stop.
   With `--run <path>`, verify the selected directory is a direct `reports/run.*` child
   of this workspace, show its size and contents, and confirm deletion including its
   evidence. Then run `__PLUGIN_ROOT__/scripts/clean-ape-workspace.sh <run-path> --all --confirm`
   and stop. This removes only that named run; no automatic expiry applies.
2. Use `Glob` to find existing clone directories at the top-level `repos/`, each `reports/run.*/repos/`, and every
   `studies/<owner>__<repo>/repos/` directory, then list their contents so the user sees
   exactly what is about to go and show `du -sh` for each. Skip absent legacy directories.
3. Ask the user to confirm.
4. On confirmation, run `__PLUGIN_ROOT__/scripts/clean-ape-workspace.sh <parent-of-each-listed-repos-directory> --confirm` once per directory shown above. Each call deletes that parent's `repos/` only. Keep every fingerprint, report, and `RECOMMENDATIONS.md` for inspection and re-synthesis.
5. Only if the user passed `--all` (or explicitly asks): run `__PLUGIN_ROOT__/scripts/clean-ape-workspace.sh <workspace-path> --all --confirm` after a second confirmation.
