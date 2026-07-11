# ollama-sidecar

**Use this when `jq`/Python can't do the transform in one deterministic pass** — the
input is too irregular for a fixed rule (messy unstructured text, "does this look like
a secret", "summarize this") but still too big or too tedious to be worth Claude's own
context budget. For anything a `jq` filter or a short Python script *can* express
exactly, that's strictly better — deterministic, instant, free, and you can verify it
yourself — and this plugin's own `deterministic` operations (below) cover the common
cases of that (dedup, sort, filter, slice, decode, hash, and a dozen structured-format
conversions) with no model call at all. Reach for the LLM-backed operations only for
the remainder: input that genuinely needs judgment about its meaning.

Either way, Claude exchanges only a file path and an operation name (a few dozen
tokens) with this plugin's MCP tool, never the file contents. The transform runs
entirely on your machine (or your LAN); only a small status payload comes back.

---

## Two LLM tiers

LLM operations route between two Ollama endpoints, because the right box depends on
the job:

| Tier | Meant for | Typical hardware | Default operations |
|---|---|---|---|
| `deep` | Judgment-heavy, low-volume work where quality matters and slow is fine | The biggest model your local machine can hold (e.g. a 30B on a 36GB MacBook) | `extract_json`, `redact_secrets`, `summarize` |
| `fast` | Bulk transforms with large outputs, where token throughput matters | A smaller model that fits entirely in a LAN GPU's VRAM (e.g. a MoE 30B-A3B or 20B on a 16GB card) | `clean_text`, `convert_format`, `yaml_to_json` (llm fallback only) |

- Every `fast` setting **falls back to its `deep` value when unset** — with no fast
  config, both tiers are the same endpoint and this is exactly the old single-endpoint
  plugin.
- A per-call `tier` argument overrides any operation's default.
- **Automatic failover:** if the chosen endpoint is unreachable (host down, laptop off
  the LAN) and the other tier points at a different host, the call retries there once
  and reports `fell_back_from` in the payload. HTTP errors (e.g. model not pulled) do
  *not* fail over — those are real answers from a live server.
- Timeouts: `deep` 300s, `fast` 120s — a big model on unified memory is slow by
  design; a fast tier that's slow is misconfigured. (Advanced: `OLLAMA_TIMEOUT` /
  `OLLAMA_FAST_TIMEOUT` override these, but they're plain env vars on the environment
  Claude Code launches from, not plugin config settings.)
- Success payloads name the `tier`, `model`, and `host` that actually served the call.

---

## Why

Some tasks (reformatting a log file, extracting messy data into JSON, summarizing)
don't need Claude's judgment — they need a mechanical pass, and passing 10,000 lines
through Claude's context costs real input *and* output tokens for no benefit. But
"mechanical" is doing a lot of work in that sentence: if the pass is truly rule-based,
`jq`/Python already solves it strictly better than a model call, and Claude can write
and run that one-liner via Bash without the file's contents ever entering context
either. This plugin's local Python `deterministic` operations exist for exactly that
reason: no model in the loop, so `"success"` means the documented algorithm ran on the
whole input.

The narrower, real niche is input irregular enough that no fixed rule covers it — but
still too large, too repetitive, or too low-stakes to spend Claude's own judgment on
one file at a time. For that slice, the `process_local_file` tool:

1. Reads the input file directly from disk.
2. Runs the requested operation — pure local Python, `yq` for the YAML pair, or the
   configured Ollama model on the tier the operation defaults to.
3. **Validates the output before writing it** — a botched transform (malformed JSON,
   ragged CSV, gross truncation, a record count that doesn't add up) surfaces as
   `status: "error"`, not a false "success".
4. Writes the result to disk and returns only a tiny status payload.

**Important trust boundary:** the validators check *format*, not *content*. For
LLM-backed operations especially, they catch a model that returns broken JSON or drops
most of the records; they do **not** verify that field values are semantically correct.
For tasks where subtle content fidelity matters, spot-check the output file yourself
rather than trusting a bare `"success"`. Deterministic operations have a stronger
guarantee — there's no model in the loop — but they're still only as correct as the
fixed rules they implement (see each operation's limitations below).

---

## Prerequisites

- `python3` on PATH (standard library only — nothing to `pip install`; `toml_to_json`
  needs Python 3.11+).
- A reachable Ollama instance with the configured model(s) already pulled — only for
  the LLM operations; everything deterministic runs without one.
- Optional: [`yq`](https://github.com/mikefarah/yq) (`brew install yq`). With it,
  `yaml_to_json` runs deterministically (no model at all) and `json_to_yaml` becomes
  available; without it, `yaml_to_json` falls back to the llm path and `json_to_yaml`
  errors with an install hint.

No build step, no Node, no dependencies. The server is one file:
`scripts/ollama_sidecar.py`, invoked via this plugin's `.mcp.json`.

---

## Install

```bash
claude plugin marketplace add seankoji/claude-plugins
claude plugin install ollama-sidecar@seankoji
```

You'll be prompted for the plugin's config (or accept the defaults):

| Setting | Default | Purpose |
|---|---|---|
| `ollama_host` | `http://localhost:11434` | **Deep tier** — primary Ollama server. Run the biggest model this box can hold. |
| `ollama_model` | `qwen3:14b` | Deep tier model tag. Must already be pulled there. |
| `num_ctx` | `16384` | Deep tier context window. |
| `fast_ollama_host` | *(unset → deep)* | **Fast tier** — optional second server for high-throughput bulk work, e.g. `http://your-pc.local:11434`. |
| `fast_ollama_model` | *(unset → deep)* | Fast tier model tag — smaller/faster, fully GPU-resident. |
| `fast_num_ctx` | *(unset → deep)* | Fast tier context window. |

Example split — a MacBook with lots of unified memory plus a gaming PC with a 16GB
GPU:

```
ollama_host      = http://localhost:11434       ollama_model      = qwen3-coder:30b
fast_ollama_host = http://your-pc.local:11434   fast_ollama_model = qwen3-coder:30b-a3b-q4_K_M
```

Reconfigure any of these later without reinstalling — see `claude plugin` config
commands for your Claude Code version.

**Upgrade note (0.2.0):** the default `ollama_model` changed from `qwen2.5-coder:14b`
to `qwen3:14b`. If you relied on the default, either `ollama pull qwen3:14b` or set
`ollama_model` explicitly — otherwise LLM operations fail with an Ollama HTTP error
until the model exists.

---

## Sizing `num_ctx` (LLM operations only)

`num_ctx` is the total token window Ollama allocates for one request — system prompt,
input file, *and* generated output all have to fit. The context-budget guard reserves
roughly half of `num_ctx` for output, so as a rule of thumb: **set `num_ctx` to
roughly 2× the token size of the largest file you expect to process** (~4 chars per
token for logs/prose). The trade-off is memory: `num_ctx` sizes the KV cache on top of
the model weights, per tier. Set it too high and Ollama fails to load the model or
silently spills to CPU; too low and the budget guard refuses files that would process
fine. If a call fails with `"input too large for the current context budget"`, raise
that tier's `num_ctx` or `split_file` the input — the server refuses up front on
purpose rather than silently truncating.

---

## The tool: `process_local_file`

```json
{
  "input_path": "logs/raw.txt",
  "output_path": "logs/clean.json",
  "operation": "extract_json",
  "tier": "deep",
  "instruction": "optional extra guidance for LLM operations",
  "params": {"...": "operation-specific parameters for deterministic operations"},
  "overwrite": false
}
```

- `input_path` / `output_path` — absolute or relative to the project root; must resolve
  **inside** the project directory (the server refuses anything that escapes it, and
  refuses to write through a symlinked `output_path` — dangling or not). `merge_files`
  uses `input_paths` (a list) instead; `split_file` treats `output_path` as an existing
  directory.
- `tier` — LLM operations only: `"deep"` or `"fast"`, overriding the operation's
  default (see the tiers table above).
- `instruction` — free-text extra guidance, LLM operations only.
- `params` — operation-specific parameters, deterministic operations only.
- `overwrite` — if the target exists and this isn't `true`, the result is written to
  `<output_path>.new` instead of clobbering (and a second run errors rather than
  clobbering that `.new` too).
- On validation failure, the rejected output is written to `<output_path>.rejected`
  for inspection, and the file you asked for is left untouched.

### LLM-backed operations

| Operation | Tier | What it does | What the validator checks |
|---|---|---|---|
| `extract_json` | deep | Pulls messy/unstructured input into JSON. | Parses as JSON; a record-count heuristic flags gross record-dropping. **Limitation:** counts non-blank input *lines*, so single-line inputs holding many records aren't protected. |
| `convert_format` | fast | Converts to the format implied by `output_path`'s extension. | `.json` parses; `.csv` parses with consistent column counts. Only `.json`/`.csv`, checked before the model is called. |
| `clean_text` | fast | Cleanup/reformatting (strip markup, normalize whitespace) per `instruction`. | Non-empty; output/input size ratio in [0.15, 4.0]. **Weak guarantee** — can't catch subtle content changes. |
| `redact_secrets` | deep | Replaces credential/API-key/token-looking values with `[REDACTED]`. | Non-empty; size ratio in [0.1, 2.0]; known secret shapes (`sk-…`, `gh?_…`, `AKIA…`, `AIza…`, `xox?-…`, PEM headers) must NOT survive. **A safety net, not a guarantee** — spot-check before sharing. |
| `summarize` | deep | Concise factual summary of the input. | Non-empty; for inputs >4000 chars the summary must be smaller than the input. Content fidelity is on the model. |
| `yaml_to_json` | fast *(llm fallback only)* | With `yq` installed this never touches a model — see the deterministic table. | Output parses as JSON. |

Every LLM operation has a hard context-budget guard: if the estimated input wouldn't
leave room for the system prompt *and* output within the tier's `num_ctx`, the call is
refused up front with a message telling you to split the file.

### Deterministic operations (no model in the loop)

| Operation | What it does | `params` | Validator / limitation |
|---|---|---|---|
| `dedupe_lines` | Removes duplicate lines, keeping first occurrence, order preserved. | `case_insensitive` | Output can't have more lines than input. |
| `sort_lines` | Sorts lines. | `numeric`, `reverse`, `unique`, `case_insensitive` | Line count must match input (unless `unique`). |
| `filter_lines` | grep-like keep/drop with optional context lines. | `pattern` (required), `mode` `include`\|`exclude`, `regex`, `case_insensitive`, `context_before`/`context_after` | Filtering can't add lines. |
| `slice_lines` | head / tail / line-range, like `sed -n`. | exactly one of `head`, `tail`, or `start`/`end` (1-based, inclusive) | Slice can't add lines; errors if it selects nothing. |
| `regex_replace` | sed-like regex substitution (Python `re` syntax). | `pattern` (required), `replacement` (default `""`), `count` (0 = all), `case_insensitive`, `multiline`, `dotall` | Nothing structural to verify — the replacement may legitimately change anything. |
| `base64_decode` / `base64_encode` | Base64 ↔ raw bytes. | `url_safe` | Decode: fails on malformed input. Encode: output must decode back to the exact input bytes. |
| `hash_file` | Checksum as `{"algorithm","hexdigest","input_bytes"}` JSON. | `algorithm`: `sha256` (default) \| `sha1` \| `md5` \| `sha512` | Output JSON has `hexdigest`. |
| `text_stats` | `wc`-style counts as JSON: bytes, chars, lines, non-blank lines, words, max line length. | — | Output JSON has the stats fields. |
| `strip_ansi_codes` | Removes ANSI CSI escape sequences. | — | CSI only (not OSC/DCS); none may survive. |
| `normalize_log_timestamps` | Rewrites known timestamp formats (Apache/NCSA, US-style, syslog) to ISO 8601. | — | Fixed pattern library — unrecognized formats pass through unchanged. Line count preserved. |
| `extract_fields` | Projects fields from structured JSON/CSV into JSON or CSV (was `extract_field_list`; old name still accepted). | `fields` (required) | Errors if no requested field appears anywhere (guards against unstructured input "parsing" as CSV). |
| `json_format` | Pretty-print or minify JSON. | `indent` (default 2), `minify`, `sort_keys` | **Round-trip equality** with the input — the strongest validator here. |
| `jsonl_to_json` / `json_to_jsonl` | JSON Lines ↔ JSON array. | — | One array element per non-blank line (and vice versa), every line parses. |
| `csv_to_json` | Header-row CSV → JSON array of row objects. All values stay strings. | `delimiter` (default `,`) | Errors on ragged rows (more fields than headers). BOM-safe. |
| `json_to_csv` | JSON array of objects → CSV. Nested values become embedded JSON strings. | `fields` (optional column order; default = first-seen order) | Consistent column count across all rows. |
| `toml_to_json` | TOML → JSON (stdlib `tomllib`, Python 3.11+). Datetimes become strings. | — | Output parses as JSON. |
| `xml_to_json` | Well-formed XML → JSON: attributes under `@attributes`, text under `#text`, repeated tags become lists. | — | **Lossy for mixed content** (text interleaved with elements keeps only the leading run). |
| `html_to_text` | Strips HTML to readable plain text; drops `script`/`style`/`head`. | — | Whitespace is normalized (loses `<pre>` formatting). No script/style markup may survive. |
| `yaml_to_json` | **With `yq` installed:** deterministic yq conversion, payload reports `"engine": "yq"`. Single-document YAML only. | — | Output parses as JSON. |
| `json_to_yaml` | JSON → YAML via `yq` (required — structured error with install hint without it). | — | **Round-trips back to JSON equal to the input** via yq. |
| `plist_to_json` | XML or binary macOS plist → JSON (`data`→base64, `date`→ISO 8601). | — | Fails if input isn't a valid plist. |
| `sqlite_to_json` | Dumps a sqlite DB's tables to JSON, opened read-only (was `sqlite_dump_to_json`; old name still accepted). | `tables` (optional allowlist) | Fails if input isn't a valid sqlite DB. |
| `split_file` | Splits into numbered chunks (`<stem>.partNNN<ext>`) in the `output_path` directory. | `lines_per_chunk` **or** `num_chunks` | Returns `chunk_paths` (a list). |
| `merge_files` | Concatenates `input_paths` (≥2) into `output_path`. | `separator` (default `"\n"`) | Merged output can't be smaller than the sum of inputs. |

### Adding an operation

**LLM-backed:** add an entry to `OPERATIONS` in `scripts/ollama_sidecar.py` with
`"kind": "llm"`, a `"default_tier"`, a prompt-builder, and a
`validate(input_text, output_text, output_path, instruction) -> (bool, reason)`.

**Deterministic:** `"kind": "deterministic"`, a
`transform(input_bytes, params, instruction, output_path) -> output_bytes` (raise
`SidecarError` on malformed input), and a matching `validate` over bytes. Set
`"reads_own_input": True` to receive the file path instead of bytes (as
`sqlite_to_json` does). Multi-file shapes (like `split_file`/`merge_files`) get their
own `handle_<name>` function instead.

No build step — edit the file and it's live on the next server restart.

---

## Failure modes (all return a structured `status: "error"`, never a hang or silent bad write)

- Paths missing, empty, or escaping the project root; output parent directory missing.
- Required or malformed `params` (e.g. `filter_lines` without `pattern`).
- A deterministic operation's input isn't what it expects (invalid base64/CSV/TOML/
  XML/plist/sqlite, multi-document YAML, unstructured input to `extract_fields`).
- `json_to_yaml` without `yq` on PATH (the error says `brew install yq`).
- LLM operations: chosen tier unreachable **and** no different-host tier to fail over
  to (or both unreachable); input too large for the tier's `num_ctx`; generation cut
  off (`done_reason` ≠ `stop`); Ollama HTTP errors such as a model that isn't pulled.
- Output fails its operation's validator (written to `<output_path>.rejected` instead).

---

## License

MIT
