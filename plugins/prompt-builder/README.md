# prompt-builder

Iteratively build high-quality, reusable Claude prompts. Diagnoses your brief, picks the right structural framework, drafts, critiques, and delivers a finished artefact — complete with test cases, known failure modes, and a recommended model.

## What it does

1. **Diagnose** — asks targeted questions (hard cap: 20 per session) to nail down goal, inputs, output format, constraints, and success criteria.
2. **Select framework** — chooses from eight prompt frameworks (see below) and announces the choice with rationale before drafting.
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

None. This is a pure instruction command — no scripts, no MCP servers, no shell dependencies.

**Optional:** `~/.claude/prompt-builder/learnings.md` — if present, the skill reads it at session start to apply validated patterns, avoid recorded failure modes, and reuse exemplar prompts as few-shot scaffolding. It grows automatically as you use the skill.

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

If your `~/.claude/` is tracked in a dotfiles repo, the skill will prompt you to commit after any write.

## License

MIT
