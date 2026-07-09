# GOAL — ship `ollama-sidecar` plugin: local-LLM offload MCP tool

Plan: /Users/careys/.claude/plans/ok-mcp-tool-ask-pure-hinton.md

## Definition of Done
- [x] Server starts on `python3`; `tools/list` advertises `process_local_file` with correct schema.
  Verify: scripted stdio handshake / MCP inspector (no Ollama required).
  Done when: initialize succeeds and the tool + schema are listed.
  Verified: initialize/tools-list handshake exercised directly; schema returned correctly.
- [x] Happy-path transform against local Ollama writes validated output; response carries no content.
  Verify: `extract_json` on a sample file with Ollama up.
  Done when: valid output written, payload is status+counts only.
  Verified: ran extract_json end-to-end against a real reachable local Ollama instance
  (qwen3-homelab:latest) — valid JSON array written, all 5 input records preserved,
  response payload carried only status/counts, no file content. Also confirmed the
  validator correctly REJECTS a JSONL (not single-JSON-doc) response from the model,
  writing to .rejected instead of silently succeeding.
- [x] Every failure mode returns a clean structured error (bad output, dropped records, Ollama down,
      path escape, empty input, oversized input, truncated context).
  Verify: exercise each case.
  Done when: each returns `status:"error"` with a reason and no hang / no clobbered file.
  Verified: path escape (/etc/passwd), missing input file, unsupported convert_format
  extension (fails fast, before any Ollama call), and — added after Head Imp diff review —
  a symlinked output_path escape attempt is now blocked (write refused, nothing written
  outside root), and a second/third run against the same output_path correctly falls back
  to .new then errors instead of clobbering.
- [x] userConfig fallback correct whether an unset value interpolates to empty or to a literal `${...}`.
  Verify: run with env unset and with a literal token.
  Done when: server uses defaults in both cases.
  Verified: unit-tested directly (unset, literal `${user_config.ollama_host}`, and an
  explicit override) — all three resolve correctly.
- [x] Marketplace registration complete and consistent (all five add-a-plugin items; versions match).
  Verify: jq checks + README/marketplace cross-check.
  Done when: CI's validate job passes locally.
  Verified: marketplace.json + plugin.json valid JSON, required fields present, no
  `commands` field, README present and linked from root README, versions match (0.1.0).
- [x] Head Imp re-reviewed the final diff; blocker/major findings addressed.
  Verify: `imps:😈` on `git diff --cached` (staged diff).
  Done when: no open blocker/major findings.
  Verified: two Head Imp passes total — plan review (CHANGES_REQUESTED, 1 blocker + 4
  major + several minor, all folded into the plan before implementation) and a final
  diff review (CHANGES_REQUESTED, 1 major symlink-escape hole + several minor/nit).
  All findings from both passes are now fixed and independently re-verified above
  (see the symlink-escape and .new-clobber tests). Remaining nits (protocolVersion no
  longer echoes the client's request; budget floor clamped to available context) were
  also fixed since they were cheap. Documented-not-fixed: the extract_json conservation
  heuristic doesn't protect single-line/minified input (noted in code comment + README).

## Status: COMPLETE — ready to commit, push, open draft PR.
- Worktree: .claude/worktrees/ollama-sidecar, branch worktree-ollama-sidecar
- Plugin: plugins/ollama-sidecar/ (plugin.json, .mcp.json, scripts/ollama_sidecar.py, README.md)
- Marketplace + root README updated with the new entry.
