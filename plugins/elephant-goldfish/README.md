# elephant-goldfish

A self-validating `/elephant` command for [Claude Code](https://code.claude.com/) that writes and gate-checks a repo's durable design document — **`elephant.md`** — using a cold, different-lineage Gemini reader as the judge.

Inspired by [Rensin's Elephant-Goldfish model](https://drensin.medium.com/elephants-goldfish-and-the-new-golden-age-of-software-engineering-c33641a48874).

---

## What it does

> "Design is the new code." — One repo = one `elephant.md` that lets a zero-context session re-bootstrap the project without reading all the code.

The command fans out parallel **haiku scouts** to map the codebase, then an **Opus author** writes the doc. It then runs a **Goldfish Gate**: a closed judge → patch → re-judge loop (up to 5 rounds) where the judge is a cold Gemini read via `agy` — a different model lineage from the Claude author. A pass means *a real zero-context reader can bootstrap from this doc*, not just that the author believes it's good.

**Modes** (passed as arguments):

| Invocation | Mode | What it does |
|---|---|---|
| `/elephant-goldfish:elephant` (no doc) | CREATE | Write a fresh `elephant.md`, then run the Goldfish Gate |
| `/elephant-goldfish:elephant` (doc exists) | GOLDFISH GATE | Validate the existing doc; fold in failures |
| `/elephant-goldfish:elephant reconcile` | RECONCILE | Drift pass — doc stays authoritative, code is checked against it |
| `/elephant-goldfish:elephant regenerate` | REGENERATE | Rebuild from code; preserve human rationale and Alternatives |
| `/elephant-goldfish:elephant <failure report>` | FEEDBACK | Fold in a manual goldfish failure report |

---

## Prerequisites

**`agy` (Antigravity CLI) must be on your PATH, pointed at a Gemini model.**

The goldfish judge calls `agy` to run a cold Gemini read. If `agy` is missing or returns empty output, the judge fails **closed** (exit 2 — never a false pass). It will not silently skip validation.

- Install: follow the [Antigravity CLI docs](https://antigravity.dev) for your platform.
- Do **not** point `agy` at a Claude model — that reintroduces the "clone grading its own homework" problem. `agy`'s default is already Gemini.

Verify before using:

```sh
agy --help
agy -p "say VERDICT: READY"   # should print a VERDICT line
```

---

## Install

```bash
claude plugin marketplace add seankoji/claude-plugins
claude plugin install elephant-goldfish@seankoji
```

---

## Usage

Run from the root of any git repo:

```
/elephant-goldfish:elephant
```

The command runs interactively inside a Claude Code session. Auto / accept-edits mode is recommended — the Goldfish Gate loop runs Bash (the judge) and Writes the doc multiple times without prompting.

### Env vars you can override

| Var | Default | Notes |
|---|---|---|
| `AGY_MODEL` | `gemini-3.1-pro` | Any Gemini model name accepted by `agy` |
| `GOLDFISH_JUDGE` | `${CLAUDE_PLUGIN_ROOT}/scripts/goldfish-judge.sh` | Path to the cold-judge helper; bundled with this plugin |
| `MAX_GOLDFISH_ITERS` | `5` | Hard cap on judge → patch → re-judge rounds |
| `GOLDFISH_AFTER_CREATE` | `true` | Set `false` to skip the gate after initial creation |

---

## The `goldfish-judge.sh` script

The bundled `scripts/goldfish-judge.sh` is the per-round oracle — a cold, read-only Gemini
pass that requires a `VERDICT: READY` or `VERDICT: NOT READY` line; anything else is
**exit 2** (fail-closed). See the script's header comments for full behavioral notes.

Run it standalone to test:

```bash
bash /path/to/goldfish-judge.sh ./elephant.md
echo "exit: $?"
```

Exit codes: `0` = READY · `10` = NOT READY · `2` = judge error / empty / no verdict.

---

## License

MIT
