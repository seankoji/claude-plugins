# elephant-goldfish

A self-validating `/elephant` command for [Claude Code](https://code.claude.com/) that writes and cold-checks a repo's durable design document — **`elephant.md`** — using a different-lineage Gemini reader as the judge.

Inspired by [Rensin's Elephant-Goldfish model](https://drensin.medium.com/elephants-goldfish-and-the-new-golden-age-of-software-engineering-c33641a48874).

---

## What it does

> "Design is the new code." — One repo = one `elephant.md` that lets a zero-context session re-bootstrap the project without reading all the code.

The command writes or updates `elephant.md` grounded in the repo, then runs a closed
judge → patch → re-judge loop (up to 5 rounds) where the judge is a cold Gemini read via
the `gemini` CLI — a different model lineage from the Claude author, with the doc inlined
into its prompt and no other repo access. A PASS means *a real zero-context reader can
bootstrap from this doc* — see **Limitations** below for what a PASS does **not** mean.

**Invocations:**

| Invocation | What it does |
|---|---|
| `/elephant-goldfish:elephant` | Write/update `elephant.md`, then run the judge loop |
| `/elephant-goldfish:elephant check` | Read-only factual drift check (citations + structure); no writes, no judge |
| `/elephant-goldfish:elephant <failure report>` | Fold in a goldfish failure report pasted back from a prior run |

## Limitations — read before trusting a PASS

The judge measures **plausibility, not truth**. It reads only the doc, by design — repo
access would let the doc cheat its own gaps — but that same design means a confidently
wrong doc can still pass; the judge has no way to catch a claim that's fluent, specific,
and false. Use `check` mode for factual accuracy; use the judge for bootstrap-sufficiency.
The two are complementary, not interchangeable.

---

## Prerequisites

**The `gemini` CLI must be on your PATH.**

The goldfish judge calls `gemini` for a cold, different-lineage read. If it's missing or
returns empty output, the judge fails **closed** (exit 2 — never a false pass). It will
not silently skip validation.

Do **not** point `GEMINI_MODEL` at a Claude model — that reintroduces the "clone grading
its own homework" problem.

Verify before using:

```sh
gemini --help
gemini -p "say VERDICT: READY"   # should print a VERDICT line
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

The command runs interactively inside a Claude Code session. Auto / accept-edits mode is recommended — the judge loop runs Bash (the judge) and Writes the doc multiple times without prompting.

### Env vars you can override

| Var | Default | Notes |
|---|---|---|
| `GEMINI_MODEL` | `gemini-2.5-pro` | Any Gemini model name accepted by `gemini` |
| `OLLAMA_MODEL` | _(unset)_ | Optional second-opinion judge via `ollama run`, run sequentially after `gemini`. Set to any model name `ollama` accepts (e.g. `qwen3:14b-q8_0`). READY requires both judges to agree. Honors `OLLAMA_HOST` for a remote instance. Do not use a Claude model. |
| `OLLAMA_NO_THINK` | `true` | Prepend `/no_think` to the Ollama prompt to suppress thinking-model preamble (qwen3, etc.) so `VERDICT:` is the first output line. Set `false` for non-thinking models that don't recognise the token. |
| `OLLAMA_HOST` | _(ollama default)_ | Override Ollama endpoint, e.g. a LAN host, for a remote instance. |
| `JUDGE_TIMEOUT` | `180` | Seconds before a hung `gemini`/`ollama` judge call is killed (needs `timeout` or `gtimeout` on PATH; otherwise unguarded) |

---

## The `goldfish-judge.sh` script

The bundled `scripts/goldfish-judge.sh` is the per-round oracle — a cold, read-only Gemini
pass (primary, doc inlined into the prompt, no file access or sandbox needed) plus an
optional local second opinion via Ollama. All judges must produce a `VERDICT: READY` or
`VERDICT: NOT READY` line; anything else, or a disagreement, is **exit 2** (fail-closed).
Consensus is AND: READY only when every judge that ran says READY. See the script's header
comments for full behavioral notes.

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
