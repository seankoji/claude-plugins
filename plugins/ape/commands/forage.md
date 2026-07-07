---
description: Forage through OSS repos for techniques transferable to this codebase
argument-hint: [focus area, e.g. testing | architecture | dx — optional]
allowed-tools: Task, SendMessage, Read, Write, Glob, Grep, Bash(gh:*), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/init-workspace.sh:*), Bash(tree:*), Bash(ls:*), Bash(cat:*), Bash(du:*)
disable-model-invocation: true
---

Forage open-source repositories for techniques transferable to this project.

Focus area: $ARGUMENTS (if empty: architecture, testing, and developer experience broadly).

Workspace: `~/tmp/repo-research/<project-slug>/` where the slug is the current directory's basename. Every phase writes its artifacts here so synthesis can be re-run later without re-foraging, and so the user can inspect raw outputs.

Open every phase heading you narrate to the user with its icon below, so a scan of the transcript reads at a glance:

|  | Phase |
|---|---|
|  | Phase 0 — preflight + fingerprint |
| 🧭 | Phases 1–3 — expedition (ape-wrangler) |

##  Phase 0 — Preflight + fingerprint (you; no subagents)

1. Run `gh auth status`. If unauthenticated, stop and tell the user to run `gh auth login` — nothing downstream works without it.
2. Run `${CLAUDE_PLUGIN_ROOT}/scripts/init-workspace.sh` — a single preapprovable command that creates `repos/` and `reports/` under the workspace and reports whether `fingerprint.md` already exists (with its `ls -la` timestamp).
3. If the script reported an existing `fingerprint.md` and its timestamp is under 30 days old, reuse it. Otherwise write it (≤150 words): stack, domain, architecture, notable existing patterns, 3–5 current weaknesses relevant to the focus area, and an explicit **already-in-use** list of techniques and tooling. Nothing on the already-in-use list may be recommended later.
4. Show the fingerprint to the user before dispatching anything. It gates every downstream token — a wrong fingerprint produces convergent garbage at scale.

## 🧭 Phases 1–3 — Expedition (ape-wrangler)

Discovery, triage/rank, cloning, analysis, and synthesis all run inside ONE `ape-wrangler`
subagent — none of the per-scout dispatches, clone progress, or per-analyst completions
should reach your context. Load `SendMessage` first (`ToolSearch: "select:SendMessage"`) —
every checkpoint after the initial spawn is answered through it, and the wrangler keeps its
context across resumes.

**Spawn synchronously** via the Agent tool:

```
Agent(
  subagent_type: 'ape:📣',
  prompt: `Run Segment A per your brief.
    Fingerprint: <the full fingerprint content>
    Focus area: <focus area, or "broad" if empty>
    Workspace: <workspace-dir>`
)
```

Keep the `agentId` from the spawn — you resume this same wrangler for Segment B.

**Agent-type fallback:** if `ape:📣` is not registered in this session, spawn
`general-purpose` with the full body of `agents/ape-wrangler.md` prepended to the prompt. If
subagents are unavailable entirely, execute that file's protocol inline in this session (same
steps, no offload) and note the degradation.

**Answering checkpoints:**

- **`candidates_ready`** — narrate it to the user in 1–2 sentences (candidate count, names,
  disk footprint from the checkpoint fields), then `SendMessage` the wrangler `continue` to
  start analysis + synthesis.
- **`blocked` (`reason: "no_candidates"`)** — nothing survived triage. Tell the user and stop;
  there is no resume verb for this one.
- **`blocked` (`reason: "clone_failed"`)** — surface the `detail.failed` list to the user;
  once they've addressed the cause (auth, rate-limit, disk space), `SendMessage` the same
  wrangler `retry clone` — per its brief, it re-clones only the failed repos and re-checks.
- **`final`** — this is the expedition's deliverable, not a status update. Present the
  `recommendations` field to the user directly, verbatim — do not re-summarize it, and do not
  read `candidates.md` or `reports/*.md` yourself to "check" it. That re-absorbs exactly what
  handing this off was meant to avoid.

**Wrangler death mid-segment:** if `SendMessage` to the wrangler errors, or it returns
malformed/non-JSON output, re-spawn a fresh `ape:📣`:

- **Died before `candidates_ready`** (mid Segment A) — re-spawn with the original Segment A
  prompt. This re-burns each gibbon-scout's search budget and may surface a different
  candidate set than the lost attempt — note that to the user, it isn't silent.
- **Died after `candidates_ready`, before `final`** (mid Segment B) — re-spawn with just
  `Run Segment B per your brief. Workspace: <workspace-dir>`. Its brief has it re-read
  `fingerprint.md` and `candidates.md` from disk itself, so nothing else needs re-supplying.

If the re-spawn also fails, fall back to executing the protocol inline.
