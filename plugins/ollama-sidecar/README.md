# ollama-sidecar

**Use this when `jq`/Python can't do the transform in one deterministic pass** — the
input is too irregular for a fixed rule (messy unstructured text, ad-hoc YAML, "does
this look like a secret") but still too big or too tedious to worth Claude's own
context budget. For anything a `jq` filter or a short Python script *can* express
exactly, that's strictly better — deterministic, instant, free, and you can verify it
yourself — and this plugin's own `deterministic` operations (below) cover the common
cases of that (dedup, sort, filter, decode, hash, format conversions) with no model
call at all. Reach for the LLM-backed operations only for the remainder: input that
genuinely needs judgment about its meaning, not just a mechanical pass.

Either way, Claude exchanges only a file path and an operation name (a few dozen
tokens) with this plugin's MCP tool, never the file contents. The transform runs
entirely on your machine; only a small status payload comes back.

---

## Why

Some tasks (reformatting a log file, extracting messy data into JSON, converting
between formats) don't need Claude's judgment — they need a mechanical pass, and
passing 10,000 lines through Claude's context costs real input *and* output tokens for
no benefit. But "mechanical" is doing a lot of work in that sentence: if the pass is
truly rule-based, `jq`/Python already solves it strictly better than a model call —
deterministic, instant, free, and verifiable — and Claude can write and run that
one-liner via Bash without the file's contents ever entering context either. This
plugin's own local Python `deterministic` operations exist for exactly that reason: no
model in the loop, so `"success"` means the documented algorithm ran on the whole
input.

The narrower, real niche is input irregular enough that no fixed rule covers it — but
still too large, too repetitive, or too low-stakes to spend Claude's own judgment on
one file at a time. For that slice, this plugin gives Claude a `process_local_file`
tool that:

1. Reads the input file directly from disk.
2. Runs the requested operation — either as pure local Python (deterministic
   operations: dedup, sort, filter, decode, hash, and a handful of stdlib-backed format
   conversions) or by sending it to your local/LAN Ollama model with a strict,
   operation-specific prompt (LLM operations: anything that needs judgment about the
   input's meaning).
3. **Validates the output before writing it** — a botched transform (malformed JSON,
   ragged CSV, gross truncation, a record count that doesn't add up) surfaces as
   `status: "error"`, not a false "success".
4. Writes the result to disk and returns only a tiny status payload.

**Important trust boundary:** the validators check *format*, not *content*. For
LLM-backed operations especially, they catch a model that returns broken JSON or drops
most of the records; they do **not** verify that field values are semantically correct.
For tasks where subtle content fidelity matters, spot-check the output file yourself
rather than trusting a bare `"success"`. Deterministic operations have a stronger
guarantee — there's no model in the loop, so "success" means the documented algorithm
ran on the whole input — but they're still only as correct as the fixed rules they
implement (see each operation's limitations below).

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

## Sizing `num_ctx` (LLM operations only — deterministic operations never touch Ollama)

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
  "instruction": "optional extra guidance for LLM operations",
  "params": {"...": "optional operation-specific parameters for deterministic operations"},
  "overwrite": false
}
```

- `input_path` / `output_path` — absolute or relative to the project root; must resolve
  **inside** the project directory (the server refuses anything that escapes it, and
  refuses to write through a symlinked `output_path` — dangling or not — so a link can't
  be used to redirect a write outside the project). `merge_files` uses `input_paths`
  (a list) instead; `split_file` treats `output_path` as an existing directory.
- `instruction` — free-text extra guidance, used only by the 5 LLM-backed operations.
- `params` — an object of operation-specific parameters, used only by deterministic
  operations and by `split_file`/`merge_files`. See each operation below.
- `overwrite` — if the target already exists and this isn't `true`, the result is
  written to `<output_path>.new` instead of clobbering it (and a second run without
  `overwrite` errors rather than clobbering that `.new` file too).
- On validation failure, the rejected output is written to `<output_path>.rejected` for
  inspection, and the file you asked for is left untouched.

### LLM-backed operations (call your configured Ollama model)

| Operation | What it does | What the validator checks |
|---|---|---|
| `extract_json` | Pulls messy/unstructured input into JSON. | Output parses as JSON; a record-count heuristic flags gross record-dropping (checks count, not per-record correctness). **Limitation:** the heuristic counts non-blank input *lines*, so it only bounds record loss for line-oriented input (logs, JSONL). A single-line input holding many records (minified JSON/CSV) isn't protected by it. |
| `convert_format` | Converts to the format implied by `output_path`'s extension. | `.json` → must parse as JSON. `.csv` → parses via the stdlib `csv` module with consistent column counts across all rows. Only `.json`/`.csv` are supported (stdlib-only, no bundled YAML parser) — checked up front from the extension, before the model is even called. |
| `clean_text` | Cleanup/reformatting (strip markup, normalize whitespace) per `instruction`. | Non-empty, and an output/input size-ratio bound (0.15–4.0×) to catch gross truncation or runaway generation. **Weak guarantee** — a ratio check can't catch subtle content changes. |
| `yaml_to_json` | Parses YAML input into JSON. There's no stdlib YAML parser to convert with or double-check against, so — unlike `convert_format` — this always goes through the model, in both directions. | Output parses as JSON. Same weak-content-fidelity caveat as `extract_json`: a structurally-valid-but-wrong translation would still pass. |
| `redact_secrets` | Finds values that look like credentials/API keys/tokens/private keys and replaces each with `[REDACTED]`. | Non-empty; a size-ratio bound (0.1–2.0×, wider than `clean_text`'s because a correct redaction can shrink a small, mostly-secret file a lot); and a scan for several well-known secret shapes (`sk-...`, `gh?_...`, `AKIA...`, `AIza...`, `xox?-...`, PEM private key headers) that must NOT survive in the output. **This is a safety net, not a guarantee** — it only catches known shapes and only bounds gross failure, not whether every real secret was actually found. Spot-check before sharing anything redacted this way. |

Every LLM operation also has a hard context-budget guard: if the estimated input size
would leave no room for the model's system prompt *and* output within `num_ctx`, the
call is refused up front with a message telling you to split the file, rather than
silently truncating.

### Deterministic operations (pure local Python — no Ollama call, no network)

These run instantly, need no Ollama instance, and give a stronger guarantee: there's no
model in the loop, so a `"success"` means the documented algorithm ran correctly on the
whole input — not just that the output happened to look right.

| Operation | What it does | `params` | Validator / limitation |
|---|---|---|---|
| `dedupe_lines` | Removes duplicate lines, keeping the first occurrence, preserving order. | `case_insensitive` (bool) | Fails if the output somehow has *more* lines than the input. |
| `sort_lines` | Sorts lines. | `numeric`, `reverse`, `unique`, `case_insensitive` (all bool) | Line count must match input exactly, unless `unique` is set (then it must not exceed it). |
| `filter_lines` | Keeps or drops lines matching a pattern, with optional lines of context around each match (like `grep`). | `pattern` (required), `mode`: `"include"` \| `"exclude"` (default `include`), `regex` (bool), `case_insensitive` (bool), `context_before`/`context_after` (int) | Fails if filtering somehow added lines. |
| `base64_decode` | Decodes base64 text to raw bytes. | `url_safe` (bool) | Fails on malformed base64, or if a non-empty input decoded to nothing. |
| `hash_file` | Computes a checksum of the input file, written as `{"algorithm", "hexdigest", "input_bytes"}` JSON. | `algorithm`: `sha256` (default) \| `sha1` \| `md5` \| `sha512` | Output must be JSON with a `hexdigest` key. |
| `strip_ansi_codes` | Removes ANSI terminal escape sequences (colors, cursor movement). | — | Only strips CSI sequences (the common case) — not every obscure escape family (OSC, DCS, ...). Fails if any CSI sequence survives. |
| `normalize_log_timestamps` | Rewrites recognized timestamp formats (Apache/NCSA combined log, US-style `MM/DD/YYYY HH:MM:SS`, syslog `Mon DD HH:MM:SS`) to ISO 8601. | — | **Known-formats only** — a fixed pattern library, not a general date parser; anything it doesn't recognize passes through unchanged rather than erroring. Validator requires line count to stay exactly the same (it's a substitution, never adds/drops lines). |
| `extract_field_list` | Projects a subset of fields from already-structured JSON (array of objects) or CSV input into JSON or CSV output — a mechanical "pick these columns." | `fields` (required list of field name strings) | Requires `output_path` to end in `.json` or `.csv`. Input must already be structured — and since `csv.DictReader` will happily "parse" arbitrary text as a degenerate single-column CSV, the operation additionally requires at least one requested field to actually appear as a key somewhere in the parsed input, or it errors rather than silently emitting an all-null projection. |
| `plist_to_json` | Converts an XML or binary macOS plist to JSON (via the stdlib `plistlib`). | — | Plist `data` values become base64 strings, `date` values become ISO 8601 strings, in the JSON output. Fails if the input isn't a valid plist. |
| `sqlite_dump_to_json` | Dumps a sqlite database's tables to JSON (`{table_name: [rows...]}`), opened read-only. | `tables` (optional list — allowlist of table names to include; default is all user tables) | Fails if the input isn't a valid sqlite database. |
| `split_file` | Splits `input_path` into numbered chunk files (`<stem>.partNNN<ext>`) written into the `output_path` directory, which must already exist. | `lines_per_chunk` **or** `num_chunks` (int, exactly one required) | Returns `chunk_paths` (a list) instead of a single `output_path` result. |
| `merge_files` | Concatenates `input_paths` (a list, ≥2 entries, used instead of `input_path`) into `output_path`. | `separator` (string, default `"\n"`) | Fails if the merged output ends up smaller than the sum of the inputs (a sign something went wrong writing it). |

### Adding an operation

**LLM-backed:** add an entry to `OPERATIONS` in `scripts/ollama_sidecar.py` with
`"kind": "llm"`, a prompt-builder function, and a
`validate(input_text, output_text, output_path, instruction) -> (bool, reason)` function.

**Deterministic:** add an entry with `"kind": "deterministic"`, a
`transform(input_bytes, params, instruction, output_path) -> output_bytes` function
(raise `SidecarError` on malformed input), and a
`validate(input_bytes, output_bytes, output_path, params) -> (bool, reason)` function.
If the operation needs the real file path rather than pre-read bytes (as
`sqlite_dump_to_json` does), set `"reads_own_input": True` and accept `input_path` as
the first argument instead.

**Multi-file shapes** (multiple outputs, like `split_file`, or multiple inputs, like
`merge_files`) don't fit that single-input/single-output pattern — give them their own
`handle_<name>` function and branch to it at the top of `handle_process_local_file`,
same as the existing two.

No build step in any case — just edit `scripts/ollama_sidecar.py` and it's live on the
next server restart.

---

## Failure modes (all return a structured `status: "error"`, never a hang or silent bad write)

- `input_path` missing, empty, or outside the project root (`input_paths` entries, for
  `merge_files`, are each checked the same way).
- `output_path`'s parent directory doesn't exist, or resolves outside the project root
  (for `split_file`, `output_path` itself must be an existing in-root directory).
- Required or malformed `params` for the operation (e.g. `filter_lines` without
  `pattern`, `split_file` without `lines_per_chunk`/`num_chunks`).
- A deterministic operation's input doesn't match what it expects (invalid base64, a
  non-plist file, a non-sqlite-database file, unstructured input to
  `extract_field_list`).
- LLM operations only: Ollama unreachable or times out (120s default); input too large
  for the configured `num_ctx`; Ollama's generation was cut off (`done_reason` other
  than `stop`).
- Output fails its operation's validator (written to `<output_path>.rejected` instead).

---

## License

MIT
