#!/usr/bin/env python3
"""ollama-sidecar MCP server.

Offloads mechanical, format-checkable text transforms to a local/LAN Ollama
model. Claude exchanges only file paths and an operation name over MCP (a
few dozen tokens) — file contents are read and written entirely on this
machine and never cross into the assistant's context, in either direction.

Design constraints (see plan for the full rationale):
  * Standard library only. No pip install, no build step, no committed
    bundle — this file IS the runtime artifact.
  * Hand-rolled MCP stdio transport: newline-delimited JSON-RPC 2.0 on
    stdin/stdout. Only stderr is used for diagnostics.
  * A fixed, VERIFIED allowlist of operations, not a free-text passthrough.
    Each operation validates its own output before writing it, so a botched
    transform surfaces as a structured error instead of a silent "success".
    Validators catch FORMAT failures (bad JSON, ragged CSV, gross
    truncation/record-loss) — they do NOT prove content is semantically
    correct. Documented explicitly in the tool description and README.
"""

import csv
import io
import json
import os
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Configuration — env-driven, all with hardcoded local-only defaults so the
# server is correct out of the box and can be pointed elsewhere (e.g. the
# LAN PC) purely via the plugin's userConfig, no code change.
# ---------------------------------------------------------------------------

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:14b"
DEFAULT_NUM_CTX = 16384
REQUEST_TIMEOUT_SECONDS = 120
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ollama-sidecar"
SERVER_VERSION = "0.1.0"


def _env(name, default):
    """Read an env var, falling back to `default` if unset OR if it still
    contains an unexpanded ``${...}`` token. Claude Code interpolates
    userConfig values into .mcp.json's env block; if a value is left unset
    by the user, the substitution behavior for that case is not something
    this server can assume, so it treats either an empty string or a
    literal, un-interpolated placeholder as "not configured"."""
    val = os.environ.get(name, "")
    if "${" in val:
        val = ""
    return val or default


def get_ollama_host():
    return _env("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)


def get_ollama_model():
    return _env("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)


def get_num_ctx():
    raw = _env("OLLAMA_NUM_CTX", str(DEFAULT_NUM_CTX))
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_NUM_CTX


# ---------------------------------------------------------------------------
# Path scoping — every input/output path must resolve inside the project
# root. Output paths often don't exist yet, so we realpath the PARENT
# directory (which must exist) rather than the file itself; realpath()-ing
# a nonexistent path is the wrong move (throws on some platforms, silently
# no-ops on others) and was the reviewed blocker in the original design.
# ---------------------------------------------------------------------------


class SidecarError(Exception):
    """Domain error surfaced to the caller as a structured status:"error"."""


def resolve_root():
    root = _env("SIDECAR_ROOT", "")
    root = root or os.getcwd()
    return os.path.realpath(root)


def _check_within_root(root, real_path, label):
    if real_path != root and not real_path.startswith(root + os.sep):
        raise SidecarError(f"{label} escapes the allowed project root: {real_path}")


def resolve_input_path(root, input_path):
    if not input_path:
        raise SidecarError("input_path is required")
    if not os.path.isabs(input_path):
        input_path = os.path.join(root, input_path)
    if not os.path.isfile(input_path):
        raise SidecarError(f"input_path does not exist or is not a file: {input_path}")
    real = os.path.realpath(input_path)
    _check_within_root(root, real, "input_path")
    return real


def resolve_output_path(root, output_path):
    if not output_path:
        raise SidecarError("output_path is required")
    if not os.path.isabs(output_path):
        output_path = os.path.join(root, output_path)
    parent = os.path.dirname(output_path)
    if not os.path.isdir(parent):
        raise SidecarError(f"output directory does not exist: {parent}")
    real_parent = os.path.realpath(parent)
    _check_within_root(root, real_parent, "output_path")
    return os.path.join(real_parent, os.path.basename(output_path))


def choose_write_path(output_path, overwrite):
    """Never silently clobber an existing file, and never write through a
    symlink — a symlinked basename (even a dangling one, which
    os.path.exists() reports as absent) would otherwise let a write escape
    the project root that resolve_output_path() approved. If the target
    already exists and the caller didn't explicitly opt in, write beside
    it — but check the '.new' fallback for the same hazards too, so a
    second run can't silently clobber the first run's '.new'."""
    if os.path.islink(output_path):
        raise SidecarError(f"output_path is a symlink, refusing to write through it: {output_path}")
    if overwrite or not os.path.exists(output_path):
        return output_path
    candidate = output_path + ".new"
    if os.path.islink(candidate):
        raise SidecarError(f"'.new' fallback is a symlink, refusing to write through it: {candidate}")
    if os.path.exists(candidate):
        raise SidecarError(f"both output_path and its '.new' fallback already exist: {candidate}")
    return candidate


def write_text_no_symlink(path, text):
    """Write `text` to `path`, refusing to follow a symlink at open time
    (O_NOFOLLOW) so a link swapped in between the choose_write_path() check
    and this call still fails closed rather than writing outside root."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# Context-budget guard. num_ctx has to hold system prompt + input +
# generated output, not just input — charging the guard against input alone
# re-admits silent truncation for same-size transforms. We reserve half the
# post-system-prompt budget for output, which is the right ballpark for
# clean_text/convert_format/extract_json (all roughly input-sized).
# ---------------------------------------------------------------------------


def estimate_tokens(text):
    # Deliberately crude and treated as an UNDER-estimate — real tokenizers
    # often split punctuation/whitespace into extra tokens. Used only as a
    # conservative gate, not a precise budget.
    return max(1, len(text) // 4)


def max_input_tokens_for(system_prompt, num_ctx):
    overhead = estimate_tokens(system_prompt)
    available = max(1, num_ctx - overhead)
    # The 256-token floor exists so tiny inputs aren't over-restricted, but
    # it must never be allowed to exceed what's actually available — a
    # misconfigured small num_ctx would otherwise have the floor override
    # the very reservation it's meant to enforce.
    return min(max(256, available // 2), available)


# ---------------------------------------------------------------------------
# Ollama client
# ---------------------------------------------------------------------------


class OllamaError(Exception):
    pass


def call_ollama(host, model, system_prompt, user_prompt, num_ctx):
    url = host.rstrip("/") + "/api/generate"
    body = json.dumps(
        {
            "model": model,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
            "options": {"num_ctx": num_ctx},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise OllamaError(
            f"ollama at {host} returned HTTP {e.code} for model '{model}': {detail[:300]}"
        )
    except urllib.error.URLError as e:
        raise OllamaError(f"could not reach ollama at {host}: {e.reason}")
    except OSError as e:
        # Covers socket.timeout and other low-level connection failures.
        raise OllamaError(f"ollama request to {host} failed: {e}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise OllamaError(f"ollama returned a non-JSON response: {e}")


# ---------------------------------------------------------------------------
# Operations — the verified allowlist. Each entry provides a prompt builder
# and a validator; adding an operation means adding one entry here.
# ---------------------------------------------------------------------------

BASE_SYSTEM_PROMPT = (
    "You are a strict mechanical data-processing subroutine. Perform exactly "
    "the requested operation on the data that follows. Output ONLY the final "
    "result: no markdown code fences, no backticks, no explanations, no "
    "preamble or postamble."
)


def _with_instruction(text, instruction):
    if instruction:
        return f"{text} Additional instruction: {instruction}"
    return text


def _prompt_extract_json(instruction, output_path):
    return _with_instruction(
        BASE_SYSTEM_PROMPT + " Operation: extract the data into well-formed JSON.",
        instruction,
    )


def _prompt_convert_format(instruction, output_path):
    ext = os.path.splitext(output_path)[1].lstrip(".").upper() or "JSON"
    return _with_instruction(
        BASE_SYSTEM_PROMPT + f" Operation: convert the data into valid {ext} format.",
        instruction,
    )


def _prompt_clean_text(instruction, output_path):
    return _with_instruction(
        BASE_SYSTEM_PROMPT
        + " Operation: clean and reformat the text (strip markup, normalize "
        "whitespace) while preserving all information content.",
        instruction,
    )


# NOTE: counts non-blank LINES, so it only bounds record loss for
# line-oriented input (one record per line — logs, JSONL, etc). A single-
# line input holding many logical records (minified JSON/CSV) collapses to
# n_in=1 and the conservation check in _validate_extract_json effectively
# no-ops for it. Documented limitation, not fixed in v1 — see README.
def _count_input_records(text):
    return len([line for line in text.splitlines() if line.strip()])


def _validate_extract_json(input_text, output_text, output_path, instruction):
    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError as e:
        return False, f"output is not valid JSON: {e}"

    # Conservation heuristic: catches gross record-dropping. This checks
    # record COUNT only, not content correctness — a model that renames or
    # mangles field values while keeping the same record count will still
    # pass.
    n_in = _count_input_records(input_text)
    n_out = None
    if isinstance(parsed, list):
        n_out = len(parsed)
    elif isinstance(parsed, dict):
        list_values = [v for v in parsed.values() if isinstance(v, list)]
        if list_values:
            n_out = max(len(v) for v in list_values)
    if n_out is not None and n_in > 0:
        floor = max(1, int(n_in * 0.3))
        if n_out < floor:
            return False, (
                f"possible record loss: input has ~{n_in} non-blank lines but "
                f"output has {n_out} records (below the {floor}-record floor)"
            )
    return True, None


SUPPORTED_CONVERT_EXTENSIONS = {".json", ".csv"}


def _validate_convert_format(input_text, output_text, output_path, instruction):
    ext = os.path.splitext(output_path)[1].lower()
    if ext == ".json":
        try:
            json.loads(output_text)
        except json.JSONDecodeError as e:
            return False, f"output is not valid JSON: {e}"
        return True, None
    if ext == ".csv":
        try:
            rows = list(csv.reader(io.StringIO(output_text)))
        except csv.Error as e:
            return False, f"output is not valid CSV: {e}"
        if not rows:
            return False, "output CSV is empty"
        ncols = len(rows[0])
        for i, row in enumerate(rows):
            if len(row) != ncols:
                return False, (
                    f"inconsistent CSV column count at row {i}: "
                    f"expected {ncols}, got {len(row)}"
                )
        return True, None
    return False, (
        f"unsupported target format '{ext}' — v1 supports only .json and .csv "
        "output_path extensions (stdlib-only, no YAML parser bundled)"
    )


def _validate_clean_text(input_text, output_text, output_path, instruction):
    if not output_text.strip():
        return False, "output is empty"
    ratio = len(output_text) / max(1, len(input_text))
    if ratio < 0.15 or ratio > 4.0:
        return False, (
            f"output/input size ratio {ratio:.2f} is outside the sane bounds "
            "[0.15, 4.0] — likely truncation or runaway generation. This check "
            "only catches gross size anomalies, not content correctness."
        )
    return True, None


OPERATIONS = {
    "extract_json": {
        "system_prompt": _prompt_extract_json,
        "validate": _validate_extract_json,
    },
    "convert_format": {
        "system_prompt": _prompt_convert_format,
        "validate": _validate_convert_format,
    },
    "clean_text": {
        "system_prompt": _prompt_clean_text,
        "validate": _validate_clean_text,
    },
}


TOOL_DEFINITION = {
    "name": "process_local_file",
    "description": (
        "Run a mechanical text transform on a local file using a local Ollama "
        "model, entirely on this machine. Only file paths and an operation name "
        "are exchanged — file contents never enter the assistant's context, and "
        "the response never contains file content, only a small status payload. "
        "Use ONLY for genuinely mechanical, deterministic transforms whose output "
        "is format-checkable (must parse as JSON/CSV, etc). The built-in "
        "validators catch FORMAT failures (bad JSON, ragged CSV, gross "
        "truncation or record-loss) — they do NOT verify content is semantically "
        "correct. Do not use for tasks requiring judgment, and for tasks where "
        "subtle content fidelity matters, spot-check the output file directly "
        "instead of trusting a bare 'success' status."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "input_path": {
                "type": "string",
                "description": (
                    "Path to the source file, absolute or relative to the project "
                    "root. Must resolve inside the project directory."
                ),
            },
            "output_path": {
                "type": "string",
                "description": (
                    "Path to write the result to, absolute or relative to the "
                    "project root. Must resolve inside the project directory. If "
                    "the target already exists and overwrite is not set, the "
                    "result is written to '<output_path>.new' instead."
                ),
            },
            "operation": {
                "type": "string",
                "enum": sorted(OPERATIONS.keys()),
                "description": (
                    "extract_json: pull structured data into JSON. "
                    "convert_format: convert to the format implied by "
                    "output_path's extension (.json or .csv). "
                    "clean_text: deterministic cleanup/reformatting."
                ),
            },
            "instruction": {
                "type": "string",
                "description": "Optional extra guidance (e.g. which fields to extract).",
            },
            "overwrite": {
                "type": "boolean",
                "description": (
                    "If true, allow overwriting an existing output_path. Default "
                    "false (writes to '<output_path>.new' instead)."
                ),
            },
        },
        "required": ["input_path", "output_path", "operation"],
        "additionalProperties": False,
    },
}


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------


def error_payload(reason, extra=None):
    payload = {"status": "error", "reason": reason}
    if extra:
        payload.update({k: v for k, v in extra.items() if v is not None})
    return payload


def handle_process_local_file(args):
    operation = args.get("operation")
    if operation not in OPERATIONS:
        return error_payload(
            f"unknown operation '{operation}', must be one of {sorted(OPERATIONS)}"
        )
    op = OPERATIONS[operation]
    instruction = args.get("instruction") or ""
    overwrite = bool(args.get("overwrite", False))

    try:
        root = resolve_root()
        input_real = resolve_input_path(root, args.get("input_path", ""))
        output_real = resolve_output_path(root, args.get("output_path", ""))
    except SidecarError as e:
        return error_payload(str(e))

    if operation == "convert_format":
        ext = os.path.splitext(output_real)[1].lower()
        if ext not in SUPPORTED_CONVERT_EXTENSIONS:
            # Checked up front, before reading the input or spending a
            # potentially 120s model call, since this is knowable from
            # output_path alone.
            return error_payload(
                f"unsupported target format '{ext}' for convert_format — v1 "
                "supports only .json and .csv output_path extensions "
                "(stdlib-only, no YAML parser bundled)"
            )

    try:
        with open(input_real, "r", encoding="utf-8", errors="replace") as f:
            input_text = f.read()
    except OSError as e:
        return error_payload(f"failed to read input_path: {e}")

    if not input_text.strip():
        return error_payload("input file is empty")

    system_prompt = op["system_prompt"](instruction, output_real)
    num_ctx = get_num_ctx()
    budget = max_input_tokens_for(system_prompt, num_ctx)
    input_tokens = estimate_tokens(input_text)
    if input_tokens > budget:
        return error_payload(
            f"input too large for the current context budget: ~{input_tokens} "
            f"estimated tokens exceeds the ~{budget}-token ceiling for "
            f"num_ctx={num_ctx}. Split the file into smaller chunks and process "
            "each separately."
        )

    try:
        result = call_ollama(
            get_ollama_host(), get_ollama_model(), system_prompt, input_text, num_ctx
        )
    except OllamaError as e:
        return error_payload(str(e))

    done_reason = result.get("done_reason")
    if done_reason not in (None, "stop"):
        return error_payload(
            f"ollama generation did not complete cleanly (done_reason={done_reason}) "
            "— output is likely truncated. Try a smaller input or a larger num_ctx."
        )

    output_text = result.get("response", "")
    ok, reason = op["validate"](input_text, output_text, output_real, instruction)
    if not ok:
        reject_path = output_real + ".rejected"
        try:
            write_text_no_symlink(reject_path, output_text)
        except OSError:
            reject_path = None
        return error_payload(
            f"output failed validation: {reason}",
            extra={"rejected_output_path": reject_path},
        )

    try:
        write_path = choose_write_path(output_real, overwrite)
    except SidecarError as e:
        return error_payload(str(e))
    try:
        write_text_no_symlink(write_path, output_text)
    except OSError as e:
        return error_payload(f"failed to write output: {e}")

    return {
        "status": "success",
        "message": f"processed '{operation}' -> {write_path}",
        "operation": operation,
        "input_bytes": len(input_text.encode("utf-8")),
        "output_bytes": len(output_text.encode("utf-8")),
        "output_path": write_path,
    }


# ---------------------------------------------------------------------------
# MCP stdio transport — newline-delimited JSON-RPC 2.0. No dependency on the
# MCP SDK; this is the whole protocol surface this server needs.
# ---------------------------------------------------------------------------


def rpc_result(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def rpc_error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def handle_message(msg):
    method = msg.get("method")
    id_ = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        # This server implements exactly one protocol version — it doesn't
        # branch on what the client requests, so it must not echo the
        # client's requested version back as if it negotiated it.
        return rpc_result(
            id_,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return rpc_result(id_, {})
    if method == "tools/list":
        return rpc_result(id_, {"tools": [TOOL_DEFINITION]})
    if method == "tools/call":
        name = params.get("name")
        if name != TOOL_DEFINITION["name"]:
            return rpc_error(id_, -32602, f"unknown tool: {name}")
        args = params.get("arguments") or {}
        payload = handle_process_local_file(args)
        return rpc_result(
            id_,
            {
                "content": [{"type": "text", "text": json.dumps(payload)}],
                "isError": payload.get("status") != "success",
            },
        )
    if id_ is None:
        # Unknown notification — no response expected either way.
        return None
    return rpc_error(id_, -32601, f"method not found: {method}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps(rpc_error(None, -32700, "parse error")), flush=True)
            continue
        try:
            response = handle_message(msg)
        except Exception as e:  # noqa: BLE001 — last-resort guard, never crash the loop
            print(f"{SERVER_NAME}: unhandled error: {e}", file=sys.stderr)
            response = rpc_error(msg.get("id"), -32603, f"internal error: {e}")
        if response is not None:
            print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
