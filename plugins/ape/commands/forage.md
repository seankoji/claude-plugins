---
description: Forage through OSS repos for techniques transferable to this codebase
argument-hint: [focus area, e.g. testing | architecture | dx — optional]
allowed-tools: Task, Read, Write, Glob, Grep, Bash(gh:*), Bash(git clone:*), Bash(mkdir:*), Bash(tree:*), Bash(ls:*), Bash(cat:*), Bash(du:*), Bash(basename:*), Bash(date:*)
disable-model-invocation: true
---

Forage open-source repositories for techniques transferable to this project.

Focus area: $ARGUMENTS (if empty: architecture, testing, and developer experience broadly).

Workspace: `~/tmp/repo-research/<project-slug>/` where the slug is the current directory's basename. Create `repos/` and `reports/` under it now. Every phase writes its artifacts here so synthesis can be re-run later without re-foraging, and so the user can inspect raw outputs.

Open every phase heading you narrate to the user with its icon below, so a scan of the transcript reads at a glance:

|  | Phase |
|---|---|
|  | Phase 0 — preflight + fingerprint |
| 🐒 | Phase 1 — discovery (gibbon-scout) |
|  | Gate — triage / dedupe / rank |
|  | Cloning candidates |
| 🦧 | Phase 2 — analysis (orangutan-analyst) |
| 🦍 | Phase 3 — synthesis (silverback-synthesist) |

##  Phase 0 — Preflight + fingerprint (you; no subagents)

1. Run `gh auth status`. If unauthenticated, stop and tell the user to run `gh auth login` — nothing downstream works without it.
2. If `fingerprint.md` already exists in the workspace and is under 30 days old, reuse it. Otherwise write it (≤150 words): stack, domain, architecture, notable existing patterns, 3–5 current weaknesses relevant to the focus area, and an explicit **already-in-use** list of techniques and tooling. Nothing on the already-in-use list may be recommended later.
3. Show the fingerprint to the user before dispatching anything. It gates every downstream token — a wrong fingerprint produces convergent garbage at scale.

## 🐒 Phase 1 — Discovery (3 gibbon-scout agents)

Dispatch all three in ONE message so they run in parallel. Each gibbon gets the fingerprint, the focus area, and exactly ONE axis:

- **Axis A** — same domain
- **Axis B** — same stack/architecture in adjacent domains
- **Axis C** — curated sources: awesome-lists, "production-grade <X>" indexes, org accounts known for the domain

##  Gate (you)

Merge scout results. Dedupe. Drop archived, stale (>12 months), or licence-problematic candidates. Rank by expected learning value **against the fingerprint's weaknesses**, not by stars. Select the top 6 (hard cap 8). Write `candidates.md` recording the ranking, plus what you rejected and why.

 Clone the selection yourself in ONE bash call with backgrounded jobs, redirecting output to a log so raw clone progress doesn't spill into the conversation — report only the tail / exit status:

```
git clone --depth 1 --filter=blob:none <url> ~/tmp/repo-research/<slug>/repos/<name> >> clone.log 2>&1 &
...
wait
tail -n 20 clone.log
```

Add `--sparse` for anything over ~300MB diskUsage. Do NOT delete these clones afterwards — `/ape:clean` is the only sanctioned deletion path.

## 🦧 Phase 2 — Analysis (one orangutan-analyst per repo)

Dispatch ALL analysts in ONE message so they run in parallel — one repo each, 8 maximum. Each gets: the fingerprint, the focus area, its repo path, and its report path (`reports/<name>.md`). The per-analyst read budget and ≤400-word report cap are enforced in the agent definition; your job is only to pass clean inputs.

## 🦍 Phase 3 — Synthesis (one silverback-synthesist)

Dispatch ONE silverback-synthesist with the workspace path and the focus area. It reads `fingerprint.md`, `candidates.md`, and every `reports/*.md` itself, cross-checks techniques against the already-in-use list and against each other, and writes `RECOMMENDATIONS.md` to the workspace root.

Do NOT read the reports into your own context first — the whole point of this phase being agent-shaped is that the orchestrator never absorbs the raw report bodies. It returns only the top 2–3 recommendations (one paragraph each) plus near-miss notes; present that response to the user directly.
