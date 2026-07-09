# ollama-sidecar

An MCP tool that offloads mechanical, format-checkable file transforms to a local or
LAN [Ollama](https://ollama.com) instance — Claude exchanges only a file path and an
operation name (a few dozen tokens), never the file contents. The transform runs
entirely on your machine; only a small status payload comes back.

---

## Why

Some tasks (reformatting a log file, extracting messy data into JSON, converting
between formats) don't need Claude's judgment — they need a mechanical pass. Passing
10,000 lines through Claude's context costs real input *and* output tokens for no
benefit. This plugin gives Claude a `process_local_file` tool that instead:

1. Reads the input file directly from disk.
2. Sends it to your local/LAN Ollama model with a strict, operation-specific prompt.
3. **Validates the model's output before writing it** — a botched transform (malformed
   JSON, ragged CSV, gross truncation) surfaces as `status: "error"`, not a false
   "success".
4. Writes the result to disk and returns only a tiny status payload.

**Important trust boundary:** the validators check *format*, not *content*. They catch
a model that returns broken JSON or drops most of the records; they do **not** verify
that field values are semantically correct. For tasks where subtle content fidelity
matters, spot-check the output file yourself rather than trusting a bare `"success"`.

---

## Prerequisites

- `python3` on PATH (standard library only — nothing to `pip install`).
- A reachable Ollama instance with the configured model already pulled
  (`ollama pull qwen2.5-coder:14b` or whatever model you configure).

No build step, no Node, no dependencies. The server is one file:
`scripts/ollama_sidecar.py`, invoked as `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ollama_sidecar.py`
via this plugin's `.mcp.json`. That file *is* the runtime artifact — there's nothing to
compile or bundle.

---

## Install

```bash
claude plugin marketplace add seankoji/claude-plugins
claude plugin install ollama-sidecar@seankoji
```

You'll be prompted for the plugin's config (or accept the defaults):

| Setting | Default | Purpose |
|---|---|---|
| `ollama_host` | `http://localhost:11434` | Base URL of the Ollama server. Point this at your LAN PC (e.g. `http://your-pc.local:11434`) to offload to a more powerful machine — no code change needed. |
| `ollama_model` | `qwen2.5-coder:14b` | Model tag. Must already be pulled on the target instance. |
| `num_ctx` | `16384` | Context window passed to Ollama. Raise it if you have the VRAM and need to process larger files. |

Reconfigure any of these later without reinstalling — see `claude plugin` config
commands for your Claude Code version.

---

## Sizing `num_ctx`

`num_ctx` is the total token window Ollama allocates for one request — the system
prompt, your input file, *and* the model's generated output all have to fit inside it.
This plugin's context-budget guard (see below) reserves roughly half of `num_ctx` for
output, so as a rule of thumb: **set `num_ctx` to roughly 2× the token size of the
largest file you expect to process.** ~4 characters per token is a reasonable estimate
for logs/prose; dense code or JSON runs a bit richer per token.

The trade-off is VRAM: `num_ctx` sizes the KV cache Ollama holds in memory *on top of*
the model's own weights, and that cost scales with the window size regardless of how
much of it a given request actually uses — doubling `num_ctx` roughly doubles the
context window's memory footprint. Set it too high for your GPU and Ollama will fail to
load the model (or silently fall back to slow CPU/partial offload); set it too low and
this plugin's budget guard will refuse files that would otherwise process fine.

Starting points — adjust the `num_ctx` userConfig value (a numeric string; default `16384`):

| Situation | Suggested `num_ctx` |
|---|---|
| Small files (a few hundred lines), tight VRAM (≤8GB) | `4096`–`8192` |
| Default — moderate files, mid-range GPU (12–16GB) | `16384` *(default)* |
| Larger files, GPU with headroom (16GB+, e.g. an RTX 4070 Ti Super) | `32768` |
| Very large files on a high-VRAM box | `65536`+, if the model and quantization leave room |

If a call fails with `"input too large for the current context budget"`, either raise
`num_ctx` (if you have the VRAM) or split the input into smaller chunks and process each
separately — the server refuses up front on purpose rather than silently truncating.

---

## The tool: `process_local_file`

```json
{
  "input_path": "logs/raw.txt",
  "output_path": "logs/clean.json",
  "operation": "extract_json",
  "instruction": "optional extra guidance",
  "overwrite": false
}
```

- `input_path` / `output_path` — absolute or relative to the project root; must resolve
  **inside** the project directory (the server refuses anything that escapes it, and
  refuses to write through a symlinked `output_path` — dangling or not — so a link can't
  be used to redirect a write outside the project).
- `overwrite` — if the target already exists and this isn't `true`, the result is
  written to `<output_path>.new` instead of clobbering it (and a second run without
  `overwrite` errors rather than clobbering that `.new` file too).
- On validation failure, the rejected output is written to `<output_path>.rejected` for
  inspection, and the file you asked for is left untouched.

### Operations (verified allowlist)

| Operation | What it does | What the validator checks |
|---|---|---|
| `extract_json` | Pulls messy/unstructured input into JSON. | Output parses as JSON; a record-count heuristic flags gross record-dropping (checks count, not per-record correctness). **Limitation:** the heuristic counts non-blank input *lines*, so it only bounds record loss for line-oriented input (logs, JSONL). A single-line input holding many records (minified JSON/CSV) isn't protected by it. |
| `convert_format` | Converts to the format implied by `output_path`'s extension. | `.json` → must parse as JSON. `.csv` → parses via the stdlib `csv` module with consistent column counts across all rows. Only `.json`/`.csv` are supported in v1 (stdlib-only, no bundled YAML parser) — checked up front from the extension, before the model is even called. |
| `clean_text` | Deterministic cleanup/reformatting (strip markup, normalize whitespace) per `instruction`. | Non-empty, and an output/input size-ratio bound (0.15–4.0×) to catch gross truncation or runaway generation. **Weakest guarantee of the three** — a ratio check can't catch subtle content changes. |

Every operation also has a hard context-budget guard: if the estimated input size would
leave no room for the model's system prompt *and* output within `num_ctx`, the call is
refused up front with a message telling you to split the file, rather than silently
truncating.

### Adding an operation

Add one entry to the `OPERATIONS` dict in `scripts/ollama_sidecar.py`: a prompt-builder
function and a `validate(input_text, output_text, output_path, instruction) -> (bool, reason)`
function. No build step — just edit and it's live on the next server restart.

---

## Failure modes (all return a structured `status: "error"`, never a hang or silent bad write)

- Ollama unreachable or times out (120s default).
- `input_path` missing, empty, or outside the project root.
- `output_path`'s parent directory doesn't exist, or resolves outside the project root.
- Input too large for the configured `num_ctx`.
- Ollama's generation was cut off (`done_reason` other than `stop`).
- Output fails its operation's validator (written to `<output_path>.rejected` instead).

---

## License

MIT
