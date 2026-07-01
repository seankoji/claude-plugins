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
| `/elephant-goldfish:elephant` (doc exists) | INTERACTIVE | Show a menu (Update / Regenerate / Check / Validate) |
| `/elephant-goldfish:elephant update` | UPDATE | Drift pass — doc stays authoritative, code is checked against it — then the Goldfish Gate |
| `/elephant-goldfish:elephant check` | CHECK | Read-only drift report; no writes, no gate |
| `/elephant-goldfish:elephant validate` | VALIDATE | Run the Goldfish Gate on the existing doc without any rewrite first |
| `/elephant-goldfish:elephant regenerate` | REGENERATE | Rebuild from code; preserve human rationale and Alternatives, then the Goldfish Gate |
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
| `OLLAMA_MODEL` | _(unset)_ | Optional second-opinion judge via `ollama run`, run sequentially after `agy`. Set to any model name `ollama` accepts (e.g. `qwen3:14b-q8_0`). READY requires both judges to agree. Honors `OLLAMA_HOST` for a remote instance. Do not use a Claude model. |
| `OLLAMA_NO_THINK` | `true` | Prepend `/no_think` to the Ollama prompt to suppress thinking-model preamble (qwen3, etc.) so `VERDICT:` is the first output line. Set `false` for non-thinking models that don't recognise the token. |
| `OLLAMA_HOST` | _(ollama default)_ | Override Ollama endpoint, e.g. `http://pc.robot.house:11434` for a remote instance. |
| `GOLDFISH_JUDGE` | `${CLAUDE_PLUGIN_ROOT}/scripts/goldfish-judge.sh` | Path to the cold-judge helper; bundled with this plugin |
| `MAX_GOLDFISH_ITERS` | `5` | Hard cap on judge → patch → re-judge rounds |
| `JUDGE_TIMEOUT` | `180` | Seconds before a hung `agy`/`ollama` judge call is killed (needs `timeout` or `gtimeout` on PATH; otherwise unguarded) |
| `GOLDFISH_AFTER_CREATE` | `true` | Set `false` to skip the gate after initial creation |

---

## The `goldfish-judge.sh` script

The bundled `scripts/goldfish-judge.sh` is the per-round oracle — a cold, read-only Gemini pass
(primary) plus an optional local second opinion via Ollama. All judges must produce a
`VERDICT: READY` or `VERDICT: NOT READY` line; anything else, or a disagreement, is **exit 2**
(fail-closed). Consensus is AND: READY only when every judge that ran says READY. See the script's
header comments for full behavioral notes.

Run it standalone to test:

```bash
bash /path/to/goldfish-judge.sh ./elephant.md
echo "exit: $?"

# With a second opinion from a local Ollama model:
OLLAMA_MODEL=llama3.1 bash /path/to/goldfish-judge.sh ./elephant.md
echo "exit: $?"
```

Exit codes: `0` = all judges READY · `10` = any judge NOT READY · `2` = any judge errored / empty / no verdict.

---

## License

MIT
