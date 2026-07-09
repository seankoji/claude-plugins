# prompt-builder

Iteratively build high-quality, reusable Claude prompts. Diagnoses your brief, picks the right structural framework, drafts, critiques, and delivers a finished artefact — complete with test cases, known failure modes, and a recommended model.

## What it does

1. **Diagnose** — asks targeted questions (hard cap: 20 per session) to nail down goal, inputs, output format, constraints, and success criteria. If the cap is hit before diagnosis is complete, it proceeds on explicitly-flagged assumptions rather than stalling.
2. **Select framework** — chooses from eight prompt frameworks (see below) and announces the choice — and the reasoning behind it — before drafting; if it switches framework mid-session, it says so explicitly.
3. **Draft** — produces a structured prompt with variables documented, format locked down, and constraints phrased as prohibitions.
4. **Critique** — runs a pre-delivery quality checklist internally before presenting.
5. **Deliver** — outputs a complete artefact: prompt body, test cases, known failure modes, model recommendation, and suggested save path.
6. **Refine** — loops until you're satisfied, then optionally logs learnings for future sessions.

## Frameworks supported

| Framework | Best for |
|---|---|
| **RTF** (Role, Task, Format) | Simple, well-scoped, low-stakes tasks |
| **CO-STAR** (Context, Objective, Style, Tone, Audience, Response) | Tone- or audience-sensitive outputs |
| **CRISPE** (Capacity/Role, Insight, Statement, Personality, Experiment) | Brainstorming, ideation, exploratory prompts |
| **RISEN** (Role, Input, Steps, Expectation, Narrowing) | Multi-step agentic tasks, complex chaining |
| **RACE** (Role, Action, Context, Expectation) | Content, marketing, SEO — lighter than CO-STAR |
| **APE** (Action, Purpose, Expectation) | Minimal; constrain the output format only |
| **CARE** (Context, Action, Result, Example) | Style/format mimicry when a reference example exists |
| **TAG** (Task, Action, Goal) | Highly constrained, well-understood tasks |

Chain-of-Thought and Few-Shot are applied on top of any framework when the task warrants it.

## Prerequisites

None required. The skill's own logic is pure instruction — no MCP servers, no shell dependencies for diagnosis, drafting, or critique. It bundles one small script, `scripts/audit-log.sh`, that appends a structured entry to `~/.claude/audit.jsonl` at the end of a session; it needs `jq` on `PATH` and skips itself with a warning (not a failure) if `jq` is missing.

**Optional:** `~/.claude/prompt-builder/learnings.md` — if present, the skill reads it at session start to apply validated patterns, avoid recorded failure modes, and reuse exemplar prompts as few-shot scaffolding. It grows automatically as you use the skill.

**Also written:** `~/.claude/audit.jsonl` — one JSON line per full (non-embedded) session: `{plugin, command, exit_status, duration_ms, ...}`. Shared across every plugin in this marketplace that logs a structured audit trail; see the marketplace's `AGENTS.md` for the schema.

## Install

```sh
claude plugin marketplace add seankoji/claude-plugins
claude plugin install prompt-builder@seankoji
```

## Usage

```sh
/prompt-builder:prompt-builder [initial brief]
```

Pass an initial brief to skip the opening question and jump straight to diagnosis:

```sh
/prompt-builder:prompt-builder a prompt that reviews PRs for security issues
```

Or invoke with no arguments to be prompted interactively.

## Self-improvement

The skill improves across sessions through two gated mechanisms:

**Layer 1 — Learnings log:** At the end of each session, the skill may append validated patterns, failure modes, exemplar prompts, or overridden defaults to `~/.claude/prompt-builder/learnings.md`. It always tells you what it recorded. The log has a ~150-line soft cap; stale entries are consolidated when sections fill up.

**Layer 2 — Self-revision (gated):** When the same default is overridden multiple times or a failure mode recurs, the skill proposes editing its own command body. It shows the diff and the evidence, then waits for explicit approval before touching anything. No silent edits.

**Install-path caveat:** self-revision behaves differently depending on how you installed this command. If you installed it as a plugin (`claude plugin install prompt-builder@seankoji` — the path documented above), the skill resolves the real installed location with `claude plugin path prompt-builder`, then proposes the diff against the source in this marketplace repo and offers to open a PR — it will not hand-edit the installed plugin file, since `claude plugin update` can overwrite local changes there. Self-revision only edits a file directly, and prompts you to commit, when you're running a manually-copied install under `~/.claude/commands/` or a project's `.claude/commands/`.

If your `~/.claude/` is tracked in a dotfiles repo, the skill will prompt you to commit after any write to a manually-copied install.

## Example output

A finished deliverable looks like this (abridged, from the brief "a prompt that reviews PRs for security issues"):

```
## Prompt: PR Security Reviewer

**Use when:** reviewing a pull request's diff for security issues before merge
**Model:** claude-sonnet-5
**Variables:** `{{repo_name}}` (required), `{{pr_number}}` (required)
**MCP dependencies:** mcp__github__pull_request_read, mcp__github__get_file_contents
**Save as:** `.claude/commands/security-review-pr.md`

---

You are a security-focused code reviewer. Fetch the diff for PR {{pr_number}} in
{{repo_name}} using mcp__github__pull_request_read. Flag only concrete, exploitable
issues (injection, auth bypass, secrets, unsafe deserialization) — do not report
style or performance concerns. For each finding, cite the file, line, and a one-line
exploit scenario. If none found, state that explicitly; do not invent findings.

---

**Test cases**
1. A PR adding a new API endpoint with unsanitised user input passed to a DB query.
2. A PR that changes only comments/docs (expect: no findings, explicit "none found").
3. A PR that fixes one vulnerability but introduces a second, subtler one nearby.

**Known failure modes**
- May under-report when the vulnerable code is in a file not directly touched by the diff but reachable from it (the prompt only reviews changed lines).
- Long diffs (500+ lines) risk the model skimming later hunks — split large PRs before running.
```

## License

MIT
