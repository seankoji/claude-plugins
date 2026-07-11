#!/usr/bin/env python3
"""ollama-sidecar MCP server.

For transforms jq/Python can express exactly, use those instead — deterministic,
instant, free, and verifiable. This server's own "deterministic" operations already
cover the common mechanical cases (dedup, sort, filter, decode, hash, format
conversions) with no model call. Its "llm" operations exist for the narrower remainder:
input too irregular for a fixed rule (messy unstructured text, ad-hoc YAML, "does this
look like a secret") where interpreting it requires judgment, not just a mechanical
pass — offloaded to a local/LAN Ollama model rather than spending Claude's own context
on it. Either way, Claude exchanges only file paths and an operation name over MCP (a
few dozen tokens) — file contents are read and written entirely on this machine and
never cross into the assistant's context, in either direction.

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
  * Two operation "kinds":
      - "llm": sends input_text to Ollama with a strict system prompt, then
        validates the response. Used when interpreting the input requires
        judgment (extracting structure from messy text, summarizing,
        spotting things that look like secrets).
      - "deterministic": pure Python, no Ollama call, no network round-trip.
        Used for transforms with one unambiguous correct answer (dedup,
        sort, filter, decode, hash, format conversions backed by the
        standard library). Faster, free, and needs no Ollama instance.
        The two yaml ops additionally use an OPTIONAL external binary (yq)
        when it's on PATH — still deterministic, still local; json_to_yaml
        fails with a structured "brew install yq" hint without it, and
        yaml_to_json falls back to the llm path instead.
  * Two LLM tiers, because the right box depends on the job:
      - "deep": the primary endpoint — typically the biggest model your
        local machine can hold. Low throughput is fine; judgment-heavy,
        low-volume operations default here.
      - "fast": an optional second endpoint — typically a smaller model
        that fits entirely in a GPU's VRAM on a LAN box, generating tokens
        several times faster. Bulk, output-heavy operations default here.
        Every "fast" setting falls back to its "deep" value when unset, so
        single-endpoint installs behave exactly as before.
"""

import base64
import binascii
import csv
import datetime
import hashlib
import html.parser
import io
import json
import os
import plistlib
import re
import shutil
import sqlite3
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree
from xml.parsers.expat import ExpatError

try:
    import tomllib  # Python 3.11+
except ImportError:  # older interpreter — toml_to_json degrades to a structured error
    tomllib = None

# ---------------------------------------------------------------------------
# Configuration — env-driven, all with hardcoded local-only defaults so the
# server is correct out of the box and can be pointed elsewhere (e.g. the
# LAN PC) purely via the plugin's userConfig, no code change.
# ---------------------------------------------------------------------------

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:14b"
DEFAULT_NUM_CTX = 16384
# The deep tier gets a longer leash: a 30B+ model on unified memory is slow
# by design — that's the tier's whole trade. The fast tier should answer
# quickly or it's misconfigured.
DEFAULT_TIMEOUT_DEEP = 300
DEFAULT_TIMEOUT_FAST = 120
YQ_TIMEOUT_SECONDS = 60
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ollama-sidecar"
# Kept in lockstep with plugin.json / marketplace.json.
SERVER_VERSION = "0.2.0"


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


def _env_int(name, default):
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


LLM_TIERS = ("deep", "fast")


def resolve_tier(tier):
    """Resolve one LLM tier to a concrete endpoint config.

    "deep" is the primary endpoint (the pre-tier OLLAMA_* names, so existing
    installs are automatically the deep tier). "fast" reads OLLAMA_FAST_*,
    each field falling back to the deep value when unset — so with no fast
    config at all, both tiers are the same endpoint and tier choice is a
    no-op."""
    host = _env("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
    model = _env("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    num_ctx = _env_int("OLLAMA_NUM_CTX", DEFAULT_NUM_CTX)
    timeout = _env_int("OLLAMA_TIMEOUT", DEFAULT_TIMEOUT_DEEP)
    if tier == "fast":
        host = _env("OLLAMA_FAST_HOST", host)
        model = _env("OLLAMA_FAST_MODEL", model)
        num_ctx = _env_int("OLLAMA_FAST_NUM_CTX", num_ctx)
        timeout = _env_int("OLLAMA_FAST_TIMEOUT", DEFAULT_TIMEOUT_FAST)
    return {"tier": tier, "host": host, "model": model, "num_ctx": num_ctx, "timeout": timeout}


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


def resolve_output_dir(root, output_dir_path):
    """Like resolve_output_path, but for split_file: the target is an
    existing directory that chunk files get written into, not a single
    file path."""
    if not output_dir_path:
        raise SidecarError("output_path (an existing directory) is required for split_file")
    if not os.path.isabs(output_dir_path):
        output_dir_path = os.path.join(root, output_dir_path)
    if not os.path.isdir(output_dir_path):
        raise SidecarError(
            f"output_path must be an existing directory for split_file: {output_dir_path}"
        )
    real = os.path.realpath(output_dir_path)
    _check_within_root(root, real, "output_path")
    return real


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


def write_bytes_no_symlink(path, data):
    """Binary-safe sibling of write_text_no_symlink — used by deterministic
    operations, whose output may not be valid UTF-8 (e.g. base64_decode)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


# ---------------------------------------------------------------------------
# Context-budget guard (LLM operations only). num_ctx has to hold system
# prompt + input + generated output, not just input — charging the guard
# against input alone re-admits silent truncation for same-size transforms.
# We reserve half the post-system-prompt budget for output, which is the
# right ballpark for clean_text/convert_format/extract_json/yaml_to_json/
# redact_secrets (all roughly input-sized).
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
# Ollama client (LLM operations only)
# ---------------------------------------------------------------------------


class OllamaError(Exception):
    """`unreachable=True` marks connection-level failures (host down, DNS,
    timeout) — the only class of error worth retrying on the other tier.
    HTTP errors (model not pulled, bad request) are real answers from a
    live server and must surface as-is."""

    def __init__(self, message, unreachable=False):
        super().__init__(message)
        self.unreachable = unreachable


def _tls_context_for(url):
    """Optional custom CA bundle for https Ollama endpoints (OLLAMA_TLS_CA =
    path to a PEM file). Lets a LAN reverse proxy with an mkcert/self-signed
    CA verify properly instead of forcing plain http or an insecure skip.
    Returns None for http URLs or when unconfigured (default verification)."""
    ca_file = _env("OLLAMA_TLS_CA", "")
    if not ca_file or not url.lower().startswith("https://"):
        return None
    try:
        return ssl.create_default_context(cafile=ca_file)
    except (OSError, ssl.SSLError) as e:
        raise OllamaError(f"could not load OLLAMA_TLS_CA bundle '{ca_file}': {e}")


def call_ollama(cfg, system_prompt, user_prompt):
    host, model = cfg["host"], cfg["model"]
    url = host.rstrip("/") + "/api/generate"
    tls_context = _tls_context_for(url)
    body = json.dumps(
        {
            "model": model,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
            "options": {"num_ctx": cfg["num_ctx"]},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout"], context=tls_context) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise OllamaError(
            f"ollama at {host} returned HTTP {e.code} for model '{model}': {detail[:300]}"
        )
    except urllib.error.URLError as e:
        raise OllamaError(f"could not reach ollama at {host}: {e.reason}", unreachable=True)
    except OSError as e:
        # Covers socket.timeout and other low-level connection failures.
        raise OllamaError(f"ollama request to {host} failed: {e}", unreachable=True)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise OllamaError(f"ollama returned a non-JSON response: {e}")


# ---------------------------------------------------------------------------
# LLM operations — each provides a prompt builder and a validator.
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


def _prompt_yaml_to_json(instruction, output_path):
    return _with_instruction(
        BASE_SYSTEM_PROMPT
        + " Operation: parse the YAML input and convert it to equivalent "
        "well-formed JSON, preserving structure and values exactly.",
        instruction,
    )


def _prompt_redact_secrets(instruction, output_path):
    return _with_instruction(
        BASE_SYSTEM_PROMPT
        + " Operation: find any values that look like credentials, API keys, "
        "access tokens, passwords, or private keys and replace each one with "
        "the literal text '[REDACTED]', leaving every other character of the "
        "input unchanged.",
        instruction,
    )


def _prompt_summarize(instruction, output_path):
    return _with_instruction(
        BASE_SYSTEM_PROMPT
        + " Operation: write a concise, factual summary of the input as plain "
        "text — key points, entities, numbers, and decisions, nothing invented.",
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


def _validate_yaml_to_json(input_text, output_text, output_path, instruction):
    try:
        json.loads(output_text)
    except json.JSONDecodeError as e:
        return False, f"output is not valid JSON: {e}"
    return True, None


# Known-secret-shaped substrings the model might have missed. This is a
# safety net, not a guarantee — it only catches secrets matching these
# specific well-known shapes (OpenAI/GitHub/AWS/Google/Slack tokens, PEM
# private key headers), same weak-content-fidelity caveat as clean_text.
_SECRET_LIKE_RE = re.compile(
    r"(sk-[A-Za-z0-9]{20,}"
    r"|gh[oprsu]_[A-Za-z0-9]{30,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_-]{30,}"
    r"|xox[baprs]-[0-9A-Za-z-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


def _validate_redact_secrets(input_text, output_text, output_path, instruction):
    if not output_text.strip():
        return False, "output is empty"
    ratio = len(output_text) / max(1, len(input_text))
    # Wider bounds than clean_text's: a correct redaction can legitimately
    # shrink small inputs a lot (a 40-char secret replaced by the 10-char
    # literal '[REDACTED]' more than halves a file that's mostly secret).
    # This only exists to catch wholesale deletion or runaway generation,
    # not to police how much a targeted redaction shrinks the text.
    if ratio < 0.1 or ratio > 2.0:
        return False, (
            f"output/input size ratio {ratio:.2f} is outside the sane bounds "
            "[0.1, 2.0] — likely wholesale deletion or runaway generation "
            "rather than a targeted redaction"
        )
    if _SECRET_LIKE_RE.search(output_text):
        return False, "output still contains a known secret-shaped pattern after redaction"
    return True, None


def _validate_summarize(input_text, output_text, output_path, instruction):
    if not output_text.strip():
        return False, "output is empty"
    # A "summary" of a big input that's bigger than the input means runaway
    # generation. Small inputs are exempt — a faithful summary of two lines
    # can legitimately be longer than them.
    if len(input_text) > 4000 and len(output_text) >= len(input_text):
        return False, (
            f"summary ({len(output_text)} chars) is not smaller than the input "
            f"({len(input_text)} chars) — likely runaway generation, not a summary"
        )
    return True, None


# ---------------------------------------------------------------------------
# Deterministic operations — pure Python, no Ollama call. Every transform
# raises SidecarError on malformed input (surfaced as status:"error" just
# like an LLM validation failure); every validator has the same signature
# as an LLM validator's, just over bytes instead of text, plus `params`
# instead of `instruction`.
# ---------------------------------------------------------------------------


def _det_dedupe_lines(input_bytes, params, instruction, output_path):
    text = input_bytes.decode("utf-8", errors="replace")
    case_insensitive = bool(params.get("case_insensitive", False))
    seen = set()
    out_lines = []
    for line in text.splitlines(keepends=True):
        key = line.rstrip("\n")
        if case_insensitive:
            key = key.lower()
        if key in seen:
            continue
        seen.add(key)
        out_lines.append(line)
    return "".join(out_lines).encode("utf-8")


def _val_dedupe_lines(input_bytes, output_bytes, output_path, params):
    n_in = len(input_bytes.decode("utf-8", errors="replace").splitlines())
    n_out = len(output_bytes.decode("utf-8", errors="replace").splitlines())
    if n_out > n_in:
        return False, (
            f"deduped output has more lines ({n_out}) than input ({n_in}) — "
            "dedup must never increase line count"
        )
    return True, None


def _det_sort_lines(input_bytes, params, instruction, output_path):
    text = input_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()
    numeric = bool(params.get("numeric", False))
    reverse = bool(params.get("reverse", False))
    unique = bool(params.get("unique", False))
    case_insensitive = bool(params.get("case_insensitive", False))

    def keyfn(s):
        if numeric:
            try:
                return (0, float(s.strip()), "")
            except ValueError:
                return (1, 0.0, s.lower() if case_insensitive else s)
        return (0, 0.0, s.lower() if case_insensitive else s)

    sorted_lines = sorted(lines, key=keyfn, reverse=reverse)
    if unique:
        seen = set()
        deduped = []
        for line in sorted_lines:
            key = line.lower() if case_insensitive else line
            if key in seen:
                continue
            seen.add(key)
            deduped.append(line)
        sorted_lines = deduped
    trailing = "\n" if text.endswith("\n") and sorted_lines else ""
    return ("\n".join(sorted_lines) + trailing).encode("utf-8")


def _val_sort_lines(input_bytes, output_bytes, output_path, params):
    n_in = len(input_bytes.decode("utf-8", errors="replace").splitlines())
    n_out = len(output_bytes.decode("utf-8", errors="replace").splitlines())
    if params.get("unique", False):
        if n_out > n_in:
            return False, f"unique-sorted output has more lines ({n_out}) than input ({n_in})"
    elif n_out != n_in:
        return False, (
            f"sorted output has {n_out} lines but input has {n_in} — sort must "
            "not change line count unless params.unique is set"
        )
    return True, None


def _det_filter_lines(input_bytes, params, instruction, output_path):
    pattern = params.get("pattern")
    if not pattern or not isinstance(pattern, str):
        raise SidecarError("params.pattern (a string) is required for filter_lines")
    mode = params.get("mode", "include")
    if mode not in ("include", "exclude"):
        raise SidecarError("params.mode must be 'include' or 'exclude'")
    use_regex = bool(params.get("regex", False))
    case_insensitive = bool(params.get("case_insensitive", False))
    try:
        before = int(params.get("context_before", 0) or 0)
        after = int(params.get("context_after", 0) or 0)
    except (TypeError, ValueError):
        raise SidecarError("params.context_before/context_after must be integers")
    if before < 0 or after < 0:
        raise SidecarError("params.context_before/context_after must be >= 0")

    text = input_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()
    flags = re.IGNORECASE if case_insensitive else 0
    if use_regex:
        try:
            rx = re.compile(pattern, flags)
        except re.error as e:
            raise SidecarError(f"params.pattern is not a valid regex: {e}")
        matcher = rx.search
    else:
        needle = pattern.lower() if case_insensitive else pattern

        def matcher(line):
            hay = line.lower() if case_insensitive else line
            return needle in hay

    keep = set()
    for i, line in enumerate(lines):
        matched = bool(matcher(line))
        if (matched and mode == "include") or (not matched and mode == "exclude"):
            for j in range(max(0, i - before), min(len(lines), i + after + 1)):
                keep.add(j)
    out_lines = [lines[i] for i in sorted(keep)]
    if not out_lines:
        return b""
    trailing = "\n" if text.endswith("\n") else ""
    return ("\n".join(out_lines) + trailing).encode("utf-8")


def _val_filter_lines(input_bytes, output_bytes, output_path, params):
    n_in = len(input_bytes.decode("utf-8", errors="replace").splitlines())
    n_out = len(output_bytes.decode("utf-8", errors="replace").splitlines())
    if n_out > n_in:
        return False, (
            f"filtered output has more lines ({n_out}) than input ({n_in}) — "
            "filtering must never add lines"
        )
    return True, None


def _det_base64_decode(input_bytes, params, instruction, output_path):
    cleaned = re.sub(rb"\s+", b"", input_bytes)
    url_safe = bool(params.get("url_safe", False))
    decoder = base64.urlsafe_b64decode if url_safe else base64.b64decode
    try:
        return decoder(cleaned)
    except (binascii.Error, ValueError) as e:
        raise SidecarError(f"input is not valid base64: {e}")


def _val_base64_decode(input_bytes, output_bytes, output_path, params):
    if len(output_bytes) == 0 and len(input_bytes.strip()) > 0:
        return False, "decoded output is empty but input was non-empty"
    return True, None


_HASH_ALGOS = {
    "sha256": hashlib.sha256,
    "sha1": hashlib.sha1,
    "md5": hashlib.md5,
    "sha512": hashlib.sha512,
}


def _det_hash_file(input_bytes, params, instruction, output_path):
    algo = params.get("algorithm", "sha256")
    if algo not in _HASH_ALGOS:
        raise SidecarError(f"params.algorithm must be one of {sorted(_HASH_ALGOS)}")
    digest = _HASH_ALGOS[algo](input_bytes).hexdigest()
    payload = {"algorithm": algo, "hexdigest": digest, "input_bytes": len(input_bytes)}
    return json.dumps(payload, indent=2).encode("utf-8")


def _val_hash_file(input_bytes, output_bytes, output_path, params):
    try:
        obj = json.loads(output_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return False, f"output is not valid JSON: {e}"
    if "hexdigest" not in obj:
        return False, "output missing 'hexdigest' field"
    return True, None


# CSI (Control Sequence Introducer) escape sequences — covers color codes,
# cursor movement, and the other common terminal escapes. Does not attempt
# to strip every obscure escape family (OSC, DCS, ...); documented as a
# known-formats limitation, same spirit as normalize_log_timestamps.
_ANSI_RE = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")


def _det_strip_ansi_codes(input_bytes, params, instruction, output_path):
    return _ANSI_RE.sub(b"", input_bytes)


def _val_strip_ansi_codes(input_bytes, output_bytes, output_path, params):
    if _ANSI_RE.search(output_bytes):
        return False, "output still contains ANSI escape sequences"
    return True, None


# Known timestamp shapes rewritten to ISO 8601. Anything not matching one of
# these stays untouched — this is a fixed pattern library, not a general
# date parser, so unrecognized formats silently pass through rather than
# erroring (documented limitation, same spirit as extract_json's line-based
# record counting).
_TS_PATTERNS = [
    # Apache/NCSA combined log: [10/Oct/2023:13:55:36 -0700]
    (re.compile(r"\[(\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4})\]"), "%d/%b/%Y:%H:%M:%S %z", True),
    # US-style: 10/10/2023 13:55:36
    (re.compile(r"\b(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})\b"), "%m/%d/%Y %H:%M:%S", True),
    # syslog: Oct 10 13:55:36 (no year in the source format — assume current)
    (re.compile(r"\b(\w{3}\s+\d{1,2} \d{2}:\d{2}:\d{2})\b"), "%b %d %H:%M:%S", False),
]


def _normalize_one_timestamp(raw, fmt, has_year):
    try:
        if has_year:
            dt = datetime.datetime.strptime(raw, fmt)
        else:
            dt = datetime.datetime.strptime(f"{datetime.datetime.now().year} {raw}", f"%Y {fmt}")
    except ValueError:
        return None
    return dt.isoformat()


def _det_normalize_log_timestamps(input_bytes, params, instruction, output_path):
    text = input_bytes.decode("utf-8", errors="replace")
    out_lines = []
    for line in text.splitlines(keepends=True):
        replaced = line
        for rx, fmt, has_year in _TS_PATTERNS:
            def _sub(m, _fmt=fmt, _has_year=has_year):
                iso = _normalize_one_timestamp(m.group(1), _fmt, _has_year)
                return iso if iso else m.group(0)

            replaced = rx.sub(_sub, replaced)
        out_lines.append(replaced)
    return "".join(out_lines).encode("utf-8")


def _val_normalize_log_timestamps(input_bytes, output_bytes, output_path, params):
    n_in = len(input_bytes.decode("utf-8", errors="replace").splitlines())
    n_out = len(output_bytes.decode("utf-8", errors="replace").splitlines())
    if n_out != n_in:
        return False, (
            f"output has {n_out} lines but input has {n_in} — timestamp "
            "normalization must be line-preserving"
        )
    return True, None


def _det_extract_field_list(input_bytes, params, instruction, output_path):
    fields = params.get("fields")
    if not fields or not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
        raise SidecarError("params.fields (a list of field name strings) is required for extract_fields")
    ext = os.path.splitext(output_path)[1].lower()
    if ext not in SUPPORTED_CONVERT_EXTENSIONS:
        raise SidecarError(
            f"unsupported output extension '{ext}' for extract_fields — use .json or .csv"
        )
    text = input_bytes.decode("utf-8", errors="replace")

    records = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            records = parsed
        elif isinstance(parsed, dict):
            list_values = [v for v in parsed.values() if isinstance(v, list)]
            if list_values:
                records = max(list_values, key=len)
    except json.JSONDecodeError:
        pass
    if records is None:
        try:
            records = list(csv.DictReader(io.StringIO(text)))
        except csv.Error:
            records = None
    if records is None:
        raise SidecarError(
            "input is not valid JSON (array of objects) or CSV — extract_fields "
            "requires already-structured input"
        )

    records = [r for r in records if isinstance(r, dict)]
    if records and not any(f in r for r in records for f in fields):
        # csv.DictReader will happily "parse" arbitrary text as single-column
        # CSV without raising csv.Error, so the parse succeeding above isn't
        # proof the input was genuinely structured. If none of the requested
        # fields appear as a key anywhere, treat it as unstructured input
        # rather than silently emitting an all-null projection.
        raise SidecarError(
            "none of params.fields were found as keys in any record — input may "
            "not be genuinely structured data, or the field names are wrong"
        )
    projected = [{f: r.get(f) for f in fields} for r in records]

    if ext == ".json":
        return json.dumps(projected, indent=2).encode("utf-8")
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for r in projected:
        writer.writerow(r)
    return buf.getvalue().encode("utf-8")


def _val_extract_field_list(input_bytes, output_bytes, output_path, params):
    ext = os.path.splitext(output_path)[1].lower()
    try:
        text = output_bytes.decode("utf-8")
        if ext == ".json":
            json.loads(text)
        else:
            list(csv.DictReader(io.StringIO(text)))
    except (json.JSONDecodeError, csv.Error, UnicodeDecodeError) as e:
        return False, f"output is not valid {ext}: {e}"
    return True, None


def _det_plist_to_json(input_bytes, params, instruction, output_path):
    try:
        obj = plistlib.loads(input_bytes)
    except (plistlib.InvalidFileException, ExpatError, ValueError, TypeError) as e:
        raise SidecarError(f"input is not a valid plist: {e}")

    def default(o):
        if isinstance(o, datetime.datetime):
            return o.isoformat()
        if isinstance(o, (bytes, bytearray)):
            return base64.b64encode(bytes(o)).decode("ascii")
        raise TypeError(f"object of type {type(o).__name__} is not JSON serializable")

    return json.dumps(obj, indent=2, default=default).encode("utf-8")


def _val_plist_to_json(input_bytes, output_bytes, output_path, params):
    try:
        json.loads(output_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return False, f"output is not valid JSON: {e}"
    return True, None


def _det_sqlite_dump_to_json(input_path, params, instruction, output_path):
    """Unlike every other deterministic operation, this one gets the
    resolved INPUT PATH rather than pre-read bytes (op["reads_own_input"] is
    True) — sqlite3 needs a real file to open (locking, page format), not an
    in-memory blob."""
    tables_filter = params.get("tables")
    uri = f"file:{input_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        raise SidecarError(f"input is not a valid sqlite database: {e}")
    try:
        cur = conn.cursor()
        try:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            table_names = [row[0] for row in cur.fetchall()]
        except sqlite3.DatabaseError as e:
            raise SidecarError(f"input is not a valid sqlite database: {e}")
        if tables_filter:
            table_names = [t for t in table_names if t in tables_filter]
        result = {}
        for t in table_names:
            quoted = '"' + t.replace('"', '""') + '"'
            cur.execute(f"SELECT * FROM {quoted}")
            cols = [d[0] for d in cur.description]
            result[t] = [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()
    return json.dumps(result, indent=2, default=str).encode("utf-8")


def _val_sqlite_to_json(input_path, output_bytes, output_path, params):
    try:
        json.loads(output_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return False, f"output is not valid JSON: {e}"
    return True, None


def _det_base64_encode(input_bytes, params, instruction, output_path):
    url_safe = bool(params.get("url_safe", False))
    encoder = base64.urlsafe_b64encode if url_safe else base64.b64encode
    return encoder(input_bytes) + b"\n"


def _val_base64_encode(input_bytes, output_bytes, output_path, params):
    url_safe = bool(params.get("url_safe", False))
    decoder = base64.urlsafe_b64decode if url_safe else base64.b64decode
    try:
        if decoder(output_bytes.strip()) != input_bytes:
            return False, "encoded output does not decode back to the input bytes"
    except (binascii.Error, ValueError) as e:
        return False, f"encoded output is not valid base64: {e}"
    return True, None


def _parse_json_input(input_bytes, operation):
    try:
        return json.loads(input_bytes.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise SidecarError(f"input is not valid JSON ({operation} requires it): {e}")


def _det_json_format(input_bytes, params, instruction, output_path):
    parsed = _parse_json_input(input_bytes, "json_format")
    sort_keys = bool(params.get("sort_keys", False))
    if params.get("minify", False):
        out = json.dumps(parsed, separators=(",", ":"), sort_keys=sort_keys)
    else:
        try:
            indent = int(params.get("indent", 2))
        except (TypeError, ValueError):
            raise SidecarError("params.indent must be an integer")
        if indent < 0:
            raise SidecarError("params.indent must be >= 0")
        out = json.dumps(parsed, indent=indent, sort_keys=sort_keys)
    return (out + "\n").encode("utf-8")


def _val_json_format(input_bytes, output_bytes, output_path, params):
    # Round-trip equality is checkable here, so check it — the strongest
    # validator in the file.
    try:
        if json.loads(output_bytes.decode("utf-8")) != json.loads(
            input_bytes.decode("utf-8", errors="replace")
        ):
            return False, "reformatted JSON does not parse back equal to the input"
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return False, f"output is not valid JSON: {e}"
    return True, None


def _det_jsonl_to_json(input_bytes, params, instruction, output_path):
    text = input_bytes.decode("utf-8", errors="replace")
    records = []
    for i, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise SidecarError(f"input line {i} is not valid JSON: {e}")
    if not records:
        raise SidecarError("input has no non-blank JSON lines")
    return json.dumps(records, indent=2).encode("utf-8")


def _val_jsonl_to_json(input_bytes, output_bytes, output_path, params):
    n_in = _count_input_records(input_bytes.decode("utf-8", errors="replace"))
    try:
        parsed = json.loads(output_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return False, f"output is not valid JSON: {e}"
    if not isinstance(parsed, list) or len(parsed) != n_in:
        return False, (
            f"output must be a JSON array with exactly one element per non-blank "
            f"input line ({n_in}), got {len(parsed) if isinstance(parsed, list) else type(parsed).__name__}"
        )
    return True, None


def _json_records(input_bytes, operation):
    """Shared 'array of objects' input shape: a JSON array, or an object
    whose largest list value is the record list (same convention as
    extract_fields)."""
    parsed = _parse_json_input(input_bytes, operation)
    if isinstance(parsed, dict):
        list_values = [v for v in parsed.values() if isinstance(v, list)]
        parsed = max(list_values, key=len) if list_values else None
    if not isinstance(parsed, list) or not parsed:
        raise SidecarError(
            f"{operation} requires a non-empty JSON array (or an object containing one)"
        )
    return parsed


def _det_json_to_jsonl(input_bytes, params, instruction, output_path):
    records = _json_records(input_bytes, "json_to_jsonl")
    return ("\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n").encode("utf-8")


def _val_json_to_jsonl(input_bytes, output_bytes, output_path, params):
    try:
        n_records = len(_json_records(input_bytes, "json_to_jsonl"))
    except SidecarError as e:
        return False, str(e)
    lines = [l for l in output_bytes.decode("utf-8", errors="replace").splitlines() if l.strip()]
    if len(lines) != n_records:
        return False, f"output has {len(lines)} JSON lines but input has {n_records} records"
    for i, line in enumerate(lines, start=1):
        try:
            json.loads(line)
        except json.JSONDecodeError as e:
            return False, f"output line {i} is not valid JSON: {e}"
    return True, None


def _det_csv_to_json(input_bytes, params, instruction, output_path):
    delimiter = params.get("delimiter", ",")
    if not isinstance(delimiter, str) or len(delimiter) != 1:
        raise SidecarError("params.delimiter must be a single character")
    # utf-8-sig so an Excel-style BOM doesn't end up inside the first header.
    text = input_bytes.decode("utf-8-sig", errors="replace")
    try:
        rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
    except csv.Error as e:
        raise SidecarError(f"input is not valid CSV: {e}")
    if not rows:
        raise SidecarError("input CSV has a header but no data rows")
    if any(None in r for r in rows):
        raise SidecarError("input CSV is ragged: some rows have more fields than the header")
    return json.dumps(rows, indent=2).encode("utf-8")


def _val_csv_to_json(input_bytes, output_bytes, output_path, params):
    try:
        parsed = json.loads(output_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return False, f"output is not valid JSON: {e}"
    if not isinstance(parsed, list) or not parsed:
        return False, "output must be a non-empty JSON array of row objects"
    return True, None


def _check_csv_consistent(text):
    """(ok, reason) — parses as CSV with the same column count on every row."""
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as e:
        return False, f"output is not valid CSV: {e}"
    if not rows:
        return False, "output CSV is empty"
    ncols = len(rows[0])
    for i, row in enumerate(rows):
        if len(row) != ncols:
            return False, (
                f"inconsistent CSV column count at row {i}: expected {ncols}, got {len(row)}"
            )
    return True, None


def _det_json_to_csv(input_bytes, params, instruction, output_path):
    records = _json_records(input_bytes, "json_to_csv")
    if not all(isinstance(r, dict) for r in records):
        raise SidecarError("json_to_csv requires every record to be a JSON object")
    fields = params.get("fields")
    if fields is not None and (
        not isinstance(fields, list) or not all(isinstance(f, str) for f in fields)
    ):
        raise SidecarError("params.fields must be a list of field name strings")
    if not fields:
        fields = []
        for r in records:
            for k in r:
                if k not in fields:
                    fields.append(k)

    def cell(v):
        # Nested structures survive as embedded JSON rather than Python repr.
        return json.dumps(v, separators=(",", ":")) if isinstance(v, (dict, list)) else v

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for r in records:
        writer.writerow({f: cell(r.get(f)) for f in fields})
    return buf.getvalue().encode("utf-8")


def _val_json_to_csv(input_bytes, output_bytes, output_path, params):
    return _check_csv_consistent(output_bytes.decode("utf-8", errors="replace"))


def _det_toml_to_json(input_bytes, params, instruction, output_path):
    if tomllib is None:
        raise SidecarError("toml_to_json needs Python 3.11+ (the stdlib tomllib module)")
    try:
        obj = tomllib.loads(input_bytes.decode("utf-8", errors="replace"))
    except tomllib.TOMLDecodeError as e:
        raise SidecarError(f"input is not valid TOML: {e}")
    # TOML datetimes/dates/times have no JSON form — stringify them.
    return json.dumps(obj, indent=2, default=str).encode("utf-8")


def _val_toml_to_json(input_bytes, output_bytes, output_path, params):
    try:
        json.loads(output_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return False, f"output is not valid JSON: {e}"
    return True, None


def _xml_to_obj(el):
    """Conventional XML→JSON mapping: attributes under '@attributes', text
    content under '#text' (or as the value itself for a leaf), repeated child
    tags collapse into a list. Lossy for mixed content (text interleaved
    with elements keeps only the leading run) — documented in the README."""
    obj = {}
    if el.attrib:
        obj["@attributes"] = dict(el.attrib)
    for child in el:
        val = _xml_to_obj(child)
        if child.tag in obj:
            existing = obj[child.tag]
            if isinstance(existing, list):
                existing.append(val)
            else:
                obj[child.tag] = [existing, val]
        else:
            obj[child.tag] = val
    text = (el.text or "").strip()
    if text:
        if obj:
            obj["#text"] = text
        else:
            return text
    return obj if obj else None


def _det_xml_to_json(input_bytes, params, instruction, output_path):
    # ElementTree expands internal entities without limit (billion-laughs),
    # and this server exists to chew on files nobody has vetted — refuse
    # DOCTYPE/ENTITY up front rather than risk OOMing the whole server.
    if re.search(rb"<!(DOCTYPE|ENTITY)\b", input_bytes, re.IGNORECASE):
        raise SidecarError(
            "xml_to_json refuses XML containing a DOCTYPE or ENTITY declaration "
            "(entity-expansion safety) — strip the prolog first if the file is trusted"
        )
    try:
        root = xml.etree.ElementTree.fromstring(input_bytes)
    except xml.etree.ElementTree.ParseError as e:
        raise SidecarError(f"input is not well-formed XML: {e}")
    return json.dumps({root.tag: _xml_to_obj(root)}, indent=2).encode("utf-8")


def _val_xml_to_json(input_bytes, output_bytes, output_path, params):
    try:
        json.loads(output_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return False, f"output is not valid JSON: {e}"
    return True, None


class _HTMLTextExtractor(html.parser.HTMLParser):
    _SKIP = {"script", "style", "head", "template", "noscript"}
    _BLOCK = {
        "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "header", "footer", "ul", "ol", "table",
        "blockquote", "pre", "hr", "dt", "dd",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def _det_html_to_text(input_bytes, params, instruction, output_path):
    parser = _HTMLTextExtractor()
    parser.feed(input_bytes.decode("utf-8", errors="replace"))
    parser.close()
    text = "".join(parser.parts)
    # Whitespace normalization (loses <pre> formatting — documented): collapse
    # intra-line runs, strip line edges, cap blank runs at one blank line.
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    out, blank_run = [], 0
    for line in lines:
        blank_run = blank_run + 1 if not line else 0
        if blank_run <= 1:
            out.append(line)
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return ("\n".join(out) + "\n" if out else "").encode("utf-8")


def _val_html_to_text(input_bytes, output_bytes, output_path, params):
    # No output scan: skipping script/style is structural in the extractor,
    # and legitimate prose can contain the literal text "<script>" (an HTML
    # page about HTML) once entities are decoded — a substring check here
    # would reject valid output.
    return True, None


def _det_regex_replace(input_bytes, params, instruction, output_path):
    pattern = params.get("pattern")
    if not pattern or not isinstance(pattern, str):
        raise SidecarError("params.pattern (a string) is required for regex_replace")
    replacement = params.get("replacement", "")
    if not isinstance(replacement, str):
        raise SidecarError("params.replacement must be a string")
    try:
        count = int(params.get("count", 0) or 0)
    except (TypeError, ValueError):
        raise SidecarError("params.count must be an integer")
    if count < 0:
        raise SidecarError("params.count must be >= 0 (0 = replace all)")
    flags = 0
    if params.get("case_insensitive"):
        flags |= re.IGNORECASE
    if params.get("multiline"):
        flags |= re.MULTILINE
    if params.get("dotall"):
        flags |= re.DOTALL
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        raise SidecarError(f"params.pattern is not a valid regex: {e}")
    text = input_bytes.decode("utf-8", errors="replace")
    try:
        out = rx.sub(replacement, text, count=count)
    except (re.error, IndexError) as e:
        raise SidecarError(f"params.replacement is not a valid template for this pattern: {e}")
    return out.encode("utf-8")


def _val_regex_replace(input_bytes, output_bytes, output_path, params):
    # Nothing structural to verify — the replacement itself may legitimately
    # add, remove, or reintroduce anything.
    return True, None


def _det_slice_lines(input_bytes, params, instruction, output_path):
    head, tail = params.get("head"), params.get("tail")
    start, end = params.get("start"), params.get("end")
    modes = sum([head is not None, tail is not None, start is not None or end is not None])
    if modes != 1:
        raise SidecarError(
            "slice_lines requires exactly one of: params.head, params.tail, "
            "or params.start/params.end (1-based, inclusive)"
        )
    text = input_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    try:
        if head is not None:
            head = int(head)
            if head <= 0:
                raise SidecarError("params.head must be > 0")
            picked = lines[:head]
        elif tail is not None:
            tail = int(tail)
            if tail <= 0:
                raise SidecarError("params.tail must be > 0")
            picked = lines[-tail:]
        else:
            start = int(start) if start is not None else 1
            end = int(end) if end is not None else len(lines)
            if start < 1 or end < start:
                raise SidecarError("params.start must be >= 1 and params.end >= params.start")
            picked = lines[start - 1 : end]
    except (TypeError, ValueError):
        raise SidecarError("params.head/tail/start/end must be integers")
    if not picked:
        raise SidecarError("the requested slice selects no lines")
    return "".join(picked).encode("utf-8")


def _val_slice_lines(input_bytes, output_bytes, output_path, params):
    n_in = len(input_bytes.decode("utf-8", errors="replace").splitlines())
    n_out = len(output_bytes.decode("utf-8", errors="replace").splitlines())
    if n_out > n_in:
        return False, f"slice output has more lines ({n_out}) than input ({n_in})"
    return True, None


def _det_text_stats(input_bytes, params, instruction, output_path):
    text = input_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()
    payload = {
        "bytes": len(input_bytes),
        "chars": len(text),
        "lines": len(lines),
        "non_blank_lines": len([l for l in lines if l.strip()]),
        "words": len(text.split()),
        "max_line_length": max((len(l) for l in lines), default=0),
    }
    return json.dumps(payload, indent=2).encode("utf-8")


def _val_text_stats(input_bytes, output_bytes, output_path, params):
    try:
        obj = json.loads(output_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return False, f"output is not valid JSON: {e}"
    missing = {"bytes", "chars", "lines", "words"} - set(obj)
    if missing:
        return False, f"output missing stats fields: {sorted(missing)}"
    return True, None


# ---------------------------------------------------------------------------
# yq-backed operations. yq (https://github.com/mikefarah/yq, `brew install yq`)
# is the one external binary this server will use — it makes YAML conversion
# deterministic, which is strictly better than asking a model to do it. It
# stays OPTIONAL: yaml_to_json falls back to the llm path without it, and
# json_to_yaml fails with a structured install hint.
# ---------------------------------------------------------------------------


def yq_available():
    return shutil.which("yq") is not None


def _run_yq(argv, input_bytes):
    if not yq_available():
        raise SidecarError(
            "the 'yq' binary is not on PATH — install it (`brew install yq`) to "
            "run this conversion deterministically"
        )
    try:
        proc = subprocess.run(
            ["yq"] + argv, input=input_bytes, capture_output=True, timeout=YQ_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        raise SidecarError(f"yq timed out after {YQ_TIMEOUT_SECONDS}s")
    except OSError as e:
        raise SidecarError(f"failed to run yq: {e}")
    if proc.returncode != 0:
        raise SidecarError(f"yq failed: {proc.stderr.decode('utf-8', errors='replace')[:300]}")
    return proc.stdout


def _det_yaml_to_json(input_bytes, params, instruction, output_path):
    # Single-document YAML only: multi-doc streams produce concatenated JSON
    # documents, which the validator rejects rather than silently merging.
    return _run_yq(["eval", "-o=json", "."], input_bytes)


def _val_yaml_to_json_det(input_bytes, output_bytes, output_path, params):
    try:
        json.loads(output_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return False, f"output is not valid JSON (multi-document YAML input?): {e}"
    return True, None


def _det_json_to_yaml(input_bytes, params, instruction, output_path):
    _parse_json_input(input_bytes, "json_to_yaml")  # fail fast with a clear message
    return _run_yq(["eval", "-P", "-o=yaml", "."], input_bytes)


def _val_json_to_yaml(input_bytes, output_bytes, output_path, params):
    # Strongest available check: yq the YAML back to JSON and require equality
    # with the original input.
    try:
        back = json.loads(_run_yq(["eval", "-o=json", "."], output_bytes).decode("utf-8"))
        original = json.loads(input_bytes.decode("utf-8", errors="replace"))
    except (SidecarError, json.JSONDecodeError, UnicodeDecodeError) as e:
        return False, f"could not round-trip the YAML output back to JSON: {e}"
    if back != original:
        return False, "YAML output does not round-trip to the original JSON"
    return True, None


OPERATIONS = {
    # LLM-backed — need judgment about the input's meaning; validators catch
    # format failures only, per the trust boundary documented in the README.
    # default_tier routes judgment-heavy/low-volume ops to "deep" (the big
    # local model) and bulk/output-heavy ops to "fast" (the high-throughput
    # endpoint); a per-call `tier` argument overrides.
    "extract_json": {
        "kind": "llm",
        "default_tier": "deep",
        "system_prompt": _prompt_extract_json,
        "validate": _validate_extract_json,
    },
    "convert_format": {
        "kind": "llm",
        "default_tier": "fast",
        "system_prompt": _prompt_convert_format,
        "validate": _validate_convert_format,
    },
    "clean_text": {
        "kind": "llm",
        "default_tier": "fast",
        "system_prompt": _prompt_clean_text,
        "validate": _validate_clean_text,
    },
    # Hybrid: runs deterministically through yq when it's on PATH (no model
    # call at all), and only falls back to the llm path without it.
    "yaml_to_json": {
        "kind": "llm",
        "default_tier": "fast",
        "system_prompt": _prompt_yaml_to_json,
        "validate": _validate_yaml_to_json,
        "det_transform": _det_yaml_to_json,
        "det_validate": _val_yaml_to_json_det,
    },
    "redact_secrets": {
        "kind": "llm",
        "default_tier": "deep",
        "system_prompt": _prompt_redact_secrets,
        "validate": _validate_redact_secrets,
    },
    "summarize": {
        "kind": "llm",
        "default_tier": "deep",
        "system_prompt": _prompt_summarize,
        "validate": _validate_summarize,
    },
    # Deterministic — pure Python, no Ollama call, no network round-trip.
    "dedupe_lines": {
        "kind": "deterministic",
        "transform": _det_dedupe_lines,
        "validate": _val_dedupe_lines,
    },
    "sort_lines": {
        "kind": "deterministic",
        "transform": _det_sort_lines,
        "validate": _val_sort_lines,
    },
    "filter_lines": {
        "kind": "deterministic",
        "transform": _det_filter_lines,
        "validate": _val_filter_lines,
    },
    "slice_lines": {
        "kind": "deterministic",
        "transform": _det_slice_lines,
        "validate": _val_slice_lines,
    },
    "regex_replace": {
        "kind": "deterministic",
        "transform": _det_regex_replace,
        "validate": _val_regex_replace,
    },
    "base64_decode": {
        "kind": "deterministic",
        "transform": _det_base64_decode,
        "validate": _val_base64_decode,
    },
    "base64_encode": {
        "kind": "deterministic",
        "transform": _det_base64_encode,
        "validate": _val_base64_encode,
    },
    "hash_file": {
        "kind": "deterministic",
        "transform": _det_hash_file,
        "validate": _val_hash_file,
    },
    "text_stats": {
        "kind": "deterministic",
        "transform": _det_text_stats,
        "validate": _val_text_stats,
    },
    "strip_ansi_codes": {
        "kind": "deterministic",
        "transform": _det_strip_ansi_codes,
        "validate": _val_strip_ansi_codes,
    },
    "normalize_log_timestamps": {
        "kind": "deterministic",
        "transform": _det_normalize_log_timestamps,
        "validate": _val_normalize_log_timestamps,
    },
    "extract_fields": {
        "kind": "deterministic",
        "transform": _det_extract_field_list,
        "validate": _val_extract_field_list,
    },
    "json_format": {
        "kind": "deterministic",
        "transform": _det_json_format,
        "validate": _val_json_format,
    },
    "jsonl_to_json": {
        "kind": "deterministic",
        "transform": _det_jsonl_to_json,
        "validate": _val_jsonl_to_json,
    },
    "json_to_jsonl": {
        "kind": "deterministic",
        "transform": _det_json_to_jsonl,
        "validate": _val_json_to_jsonl,
    },
    "csv_to_json": {
        "kind": "deterministic",
        "transform": _det_csv_to_json,
        "validate": _val_csv_to_json,
    },
    "json_to_csv": {
        "kind": "deterministic",
        "transform": _det_json_to_csv,
        "validate": _val_json_to_csv,
    },
    "json_to_yaml": {
        "kind": "deterministic",
        "transform": _det_json_to_yaml,
        "validate": _val_json_to_yaml,
    },
    "toml_to_json": {
        "kind": "deterministic",
        "transform": _det_toml_to_json,
        "validate": _val_toml_to_json,
    },
    "xml_to_json": {
        "kind": "deterministic",
        "transform": _det_xml_to_json,
        "validate": _val_xml_to_json,
    },
    "html_to_text": {
        "kind": "deterministic",
        "transform": _det_html_to_text,
        "validate": _val_html_to_text,
    },
    "plist_to_json": {
        "kind": "deterministic",
        "transform": _det_plist_to_json,
        "validate": _val_plist_to_json,
    },
    "sqlite_to_json": {
        "kind": "deterministic",
        "reads_own_input": True,
        "transform": _det_sqlite_dump_to_json,
        "validate": _val_sqlite_to_json,
    },
}

# Pre-0.3.0 names, kept working so existing transcripts/muscle memory don't
# break. The schema enum advertises only the canonical names.
OPERATION_ALIASES = {
    "sqlite_dump_to_json": "sqlite_to_json",
    "extract_field_list": "extract_fields",
}

# split_file and merge_files aren't in OPERATIONS: both have a shape the
# single-input/single-output generic dispatcher doesn't fit (multiple
# outputs, multiple inputs respectively), so they're handled by their own
# top-level functions. They're still part of the verified allowlist exposed
# in the tool schema's operation enum.
ALL_OPERATION_NAMES = sorted(list(OPERATIONS.keys()) + ["split_file", "merge_files"])


TOOL_DEFINITION = {
    "name": "process_local_file",
    "description": (
        "Run a text transform on a local file, entirely on this machine, when "
        "jq/Python can't express it as one deterministic pass. Only file paths "
        "and an operation name are exchanged — file contents never enter the "
        "assistant's context, and the response never contains file content, "
        "only a small status payload. If the transform IS a fixed rule (pick "
        "columns, dedupe, sort, slice, decode, hash, reformat by a known "
        "schema, convert between structured formats), prefer this tool's "
        "deterministic operations or a plain jq/Python one-liner run via Bash "
        "— both are strictly better than a model call: deterministic, "
        "instant, free, verifiable. Reach for the 'llm' operations "
        "(extract_json, convert_format, clean_text, redact_secrets, "
        "summarize) only when the input is genuinely too irregular for a "
        "fixed rule and interpreting it needs judgment. LLM operations route "
        "between two Ollama endpoints: 'deep' (bigger model, slower — "
        "judgment-heavy, low-volume work) and 'fast' (smaller model, high "
        "token throughput — bulk transforms); each operation has a sensible "
        "default and the 'tier' argument overrides it. The built-in "
        "validators catch FORMAT failures (bad JSON, ragged CSV, gross "
        "truncation or record-loss) — they do NOT verify content is "
        "semantically correct, so a wrong-but-well-formed output can still "
        "report 'success'. For tasks where subtle content fidelity matters, "
        "spot-check the output file directly instead of trusting a bare "
        "'success' status."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "input_path": {
                "type": "string",
                "description": (
                    "Path to the source file, absolute or relative to the project "
                    "root. Must resolve inside the project directory. Ignored by "
                    "merge_files, which uses input_paths instead."
                ),
            },
            "input_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "merge_files only: an ordered list of at least 2 source file "
                    "paths to concatenate, each absolute or relative to the "
                    "project root."
                ),
            },
            "output_path": {
                "type": "string",
                "description": (
                    "Path to write the result to, absolute or relative to the "
                    "project root. Must resolve inside the project directory. If "
                    "the target already exists and overwrite is not set, the "
                    "result is written to '<output_path>.new' instead. For "
                    "split_file, this must instead be an EXISTING DIRECTORY that "
                    "the numbered chunk files are written into."
                ),
            },
            "operation": {
                "type": "string",
                "enum": ALL_OPERATION_NAMES,
                "description": (
                    "extract_json: pull structured data into JSON [llm, deep]. "
                    "convert_format: convert to the format implied by "
                    "output_path's extension, .json or .csv [llm, fast]. "
                    "clean_text: deterministic-looking cleanup/reformatting per "
                    "instruction [llm, fast]. "
                    "redact_secrets: mask values that look like credentials/"
                    "tokens [llm, deep]. "
                    "summarize: write a concise factual summary of the input "
                    "[llm, deep]. "
                    "yaml_to_json: parse YAML input into JSON [deterministic "
                    "via yq when installed, else llm fast]. "
                    "json_to_yaml: convert JSON to YAML [local; requires yq]. "
                    "dedupe_lines: remove duplicate lines, preserving order "
                    "[local; params.case_insensitive]. "
                    "sort_lines: sort lines [local; params.numeric/reverse/"
                    "unique/case_insensitive]. "
                    "filter_lines: keep or drop lines matching a pattern, with "
                    "optional context lines [local; params.pattern (required), "
                    "params.mode 'include'|'exclude', params.regex, "
                    "params.case_insensitive, params.context_before/after]. "
                    "slice_lines: keep a line range, like head/tail/sed -n "
                    "[local; exactly one of params.head, params.tail, or "
                    "params.start/params.end (1-based, inclusive)]. "
                    "regex_replace: sed-like regex substitution [local; "
                    "params.pattern (required), params.replacement (default "
                    "''), params.count (0=all), params.case_insensitive/"
                    "multiline/dotall]. "
                    "base64_decode: decode base64 text to raw bytes [local; "
                    "params.url_safe]. "
                    "base64_encode: encode the file's bytes as base64 [local; "
                    "params.url_safe]. "
                    "hash_file: compute a checksum of the input file [local; "
                    "params.algorithm 'sha256'|'sha1'|'md5'|'sha512']. "
                    "text_stats: line/word/char/byte counts as JSON [local]. "
                    "strip_ansi_codes: remove ANSI terminal escape sequences "
                    "[local]. "
                    "normalize_log_timestamps: rewrite known timestamp formats "
                    "(Apache/NCSA, US-style, syslog) to ISO 8601 [local]. "
                    "extract_fields: project a subset of fields from "
                    "already-structured JSON/CSV input into JSON or CSV output "
                    "[local; params.fields (required list of field names)]. "
                    "json_format: pretty-print or minify JSON [local; params."
                    "indent (default 2), params.minify, params.sort_keys]. "
                    "jsonl_to_json: JSON Lines file into one JSON array "
                    "[local]. "
                    "json_to_jsonl: JSON array into JSON Lines [local]. "
                    "csv_to_json: CSV with a header row into a JSON array of "
                    "row objects [local; params.delimiter (default ',')]. "
                    "json_to_csv: JSON array of objects into CSV [local; "
                    "params.fields (optional column order)]. "
                    "toml_to_json: parse TOML into JSON [local]. "
                    "xml_to_json: well-formed XML into JSON ('@attributes'/"
                    "'#text' convention) [local]. "
                    "html_to_text: strip HTML to readable plain text [local]. "
                    "plist_to_json: convert an XML or binary macOS plist to "
                    "JSON [local]. "
                    "sqlite_to_json: dump a sqlite database's tables to "
                    "JSON [local; params.tables (optional allowlist)]. "
                    "split_file: split input_path into numbered chunk files "
                    "inside the output_path directory [local; params."
                    "lines_per_chunk or params.num_chunks, one required]. "
                    "merge_files: concatenate input_paths into output_path "
                    "[local; params.separator, default newline]."
                ),
            },
            "tier": {
                "type": "string",
                "enum": ["deep", "fast"],
                "description": (
                    "LLM operations only: which Ollama endpoint to use. "
                    "'deep' = the primary endpoint (largest model, low "
                    "throughput — judgment-heavy, low-volume work). 'fast' = "
                    "the secondary endpoint (smaller model, high token "
                    "throughput — bulk transforms with large outputs). Omit to "
                    "use the operation's default (shown in the operation "
                    "descriptions). If the chosen endpoint is unreachable and "
                    "the other tier points at a different host, the call "
                    "automatically fails over and reports it. Ignored by "
                    "deterministic operations."
                ),
            },
            "instruction": {
                "type": "string",
                "description": (
                    "Optional extra guidance for LLM-backed operations only "
                    "(e.g. which fields to extract). Ignored by deterministic "
                    "operations, which take params instead."
                ),
            },
            "params": {
                "type": "object",
                "description": (
                    "Operation-specific parameters for deterministic operations "
                    "and split_file/merge_files — see the operation enum "
                    "description for each operation's params. Ignored by "
                    "LLM-backed operations."
                ),
                "additionalProperties": True,
            },
            "overwrite": {
                "type": "boolean",
                "description": (
                    "If true, allow overwriting an existing output_path (or, "
                    "for split_file, existing chunk files). Default false "
                    "(writes to '<output_path>.new' instead)."
                ),
            },
        },
        "required": ["operation"],
        "additionalProperties": False,
    },
}


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def error_payload(reason, extra=None):
    payload = {"status": "error", "reason": reason}
    if extra:
        payload.update({k: v for k, v in extra.items() if v is not None})
    return payload


def handle_merge_files(args):
    input_paths = args.get("input_paths")
    if not input_paths or not isinstance(input_paths, list) or not all(
        isinstance(p, str) for p in input_paths
    ):
        return error_payload("input_paths (a non-empty list of file paths) is required for merge_files")
    if len(input_paths) < 2:
        return error_payload("merge_files requires at least 2 input_paths")

    params = args.get("params") or {}
    separator = params.get("separator", "\n")
    if not isinstance(separator, str):
        return error_payload("params.separator must be a string")
    overwrite = bool(args.get("overwrite", False))

    try:
        root = resolve_root()
        resolved = [resolve_input_path(root, p) for p in input_paths]
        output_real = resolve_output_path(root, args.get("output_path", ""))
    except SidecarError as e:
        return error_payload(str(e))

    chunks = []
    total_in = 0
    for p in resolved:
        try:
            with open(p, "rb") as f:
                data = f.read()
        except OSError as e:
            return error_payload(f"failed to read input_paths entry '{p}': {e}")
        chunks.append(data)
        total_in += len(data)

    output_data = separator.encode("utf-8").join(chunks)
    if len(output_data) < total_in:
        return error_payload(
            "merged output is smaller than the sum of inputs — refusing to write "
            "a possibly corrupted merge"
        )

    try:
        write_path = choose_write_path(output_real, overwrite)
    except SidecarError as e:
        return error_payload(str(e))
    try:
        write_bytes_no_symlink(write_path, output_data)
    except OSError as e:
        return error_payload(f"failed to write output: {e}")

    return {
        "status": "success",
        "message": f"merged {len(resolved)} files -> {write_path}",
        "operation": "merge_files",
        "input_bytes": total_in,
        "output_bytes": len(output_data),
        "output_path": write_path,
    }


def handle_split_file(args):
    params = args.get("params") or {}
    lines_per_chunk = params.get("lines_per_chunk")
    num_chunks = params.get("num_chunks")
    if not lines_per_chunk and not num_chunks:
        return error_payload("split_file requires params.lines_per_chunk or params.num_chunks")
    overwrite = bool(args.get("overwrite", False))

    try:
        root = resolve_root()
        input_real = resolve_input_path(root, args.get("input_path", ""))
        output_dir = resolve_output_dir(root, args.get("output_path", ""))
    except SidecarError as e:
        return error_payload(str(e))

    try:
        with open(input_real, "rb") as f:
            data = f.read()
    except OSError as e:
        return error_payload(f"failed to read input_path: {e}")
    if not data:
        return error_payload("input file is empty")

    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    if not lines:
        return error_payload("input file has no lines to split")

    if lines_per_chunk:
        try:
            lines_per_chunk = int(lines_per_chunk)
        except (TypeError, ValueError):
            return error_payload("params.lines_per_chunk must be an integer")
        if lines_per_chunk <= 0:
            return error_payload("params.lines_per_chunk must be > 0")
        chunks = [lines[i : i + lines_per_chunk] for i in range(0, len(lines), lines_per_chunk)]
    else:
        try:
            num_chunks = int(num_chunks)
        except (TypeError, ValueError):
            return error_payload("params.num_chunks must be an integer")
        if num_chunks <= 0:
            return error_payload("params.num_chunks must be > 0")
        num_chunks = min(num_chunks, len(lines))
        size = -(-len(lines) // num_chunks)  # ceil division
        chunks = [lines[i : i + size] for i in range(0, len(lines), size)]

    stem, ext = os.path.splitext(os.path.basename(input_real))
    written = []
    for idx, chunk_lines in enumerate(chunks, start=1):
        chunk_path = os.path.join(output_dir, f"{stem}.part{idx:03d}{ext}")
        try:
            write_path = choose_write_path(chunk_path, overwrite)
        except SidecarError as e:
            return error_payload(str(e))
        try:
            write_bytes_no_symlink(write_path, "".join(chunk_lines).encode("utf-8"))
        except OSError as e:
            return error_payload(f"failed to write chunk '{chunk_path}': {e}")
        written.append(write_path)

    return {
        "status": "success",
        "message": f"split into {len(written)} chunk(s) in {output_dir}",
        "operation": "split_file",
        "input_bytes": len(data),
        "chunk_paths": written,
        "output_path": output_dir,
    }


def handle_process_local_file(args):
    operation = args.get("operation")
    operation = OPERATION_ALIASES.get(operation, operation)
    if operation == "merge_files":
        return handle_merge_files(args)
    if operation == "split_file":
        return handle_split_file(args)
    if operation not in OPERATIONS:
        return error_payload(f"unknown operation '{operation}', must be one of {ALL_OPERATION_NAMES}")

    op = OPERATIONS[operation]
    instruction = args.get("instruction") or ""
    params = args.get("params") or {}
    overwrite = bool(args.get("overwrite", False))

    engine = None
    if op["kind"] == "llm" and "det_transform" in op and yq_available():
        # Hybrid operation (yaml_to_json): yq is on PATH, so run it
        # deterministically — no model call at all.
        op = {"kind": "deterministic", "transform": op["det_transform"], "validate": op["det_validate"]}
        engine = "yq"

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
            # potentially minutes-long model call, since this is knowable
            # from output_path alone.
            return error_payload(
                f"unsupported target format '{ext}' for convert_format — v1 "
                "supports only .json and .csv output_path extensions "
                "(stdlib-only, no YAML parser bundled)"
            )

    if op["kind"] == "llm":
        tier_name = args.get("tier") or op.get("default_tier", "deep")
        if tier_name not in LLM_TIERS:
            return error_payload(f"tier must be one of {list(LLM_TIERS)}, got '{tier_name}'")
        cfg = resolve_tier(tier_name)

        try:
            with open(input_real, "r", encoding="utf-8", errors="replace") as f:
                input_text = f.read()
        except OSError as e:
            return error_payload(f"failed to read input_path: {e}")

        if not input_text.strip():
            return error_payload("input file is empty")

        system_prompt = op["system_prompt"](instruction, output_real)
        budget = max_input_tokens_for(system_prompt, cfg["num_ctx"])
        input_tokens = estimate_tokens(input_text)
        if input_tokens > budget:
            return error_payload(
                f"input too large for the current context budget: ~{input_tokens} "
                f"estimated tokens exceeds the ~{budget}-token ceiling for "
                f"num_ctx={cfg['num_ctx']} (tier '{tier_name}'). Split the file into "
                "smaller chunks and process each separately."
            )

        fell_back_from = None
        try:
            result = call_ollama(cfg, system_prompt, input_text)
        except OllamaError as e:
            other = resolve_tier("fast" if tier_name == "deep" else "deep")
            if not (e.unreachable and other["host"] != cfg["host"]):
                return error_payload(str(e))
            # The chosen endpoint is down but the other tier is a different
            # host — fail over rather than fail, and say so in the payload.
            if input_tokens > max_input_tokens_for(system_prompt, other["num_ctx"]):
                return error_payload(
                    f"{e} — and the input is too large for the {other['tier']} "
                    "tier's num_ctx, so no failover was possible"
                )
            try:
                result = call_ollama(other, system_prompt, input_text)
            except OllamaError as e2:
                return error_payload(f"both tiers failed — {tier_name}: {e}; {other['tier']}: {e2}")
            fell_back_from, cfg = tier_name, other

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

        payload = {
            "status": "success",
            "message": f"processed '{operation}' -> {write_path}",
            "operation": operation,
            "tier": cfg["tier"],
            "model": cfg["model"],
            "host": cfg["host"],
            "input_bytes": len(input_text.encode("utf-8")),
            "output_bytes": len(output_text.encode("utf-8")),
            "output_path": write_path,
        }
        if fell_back_from:
            payload["fell_back_from"] = fell_back_from
        return payload

    # Deterministic path — no Ollama call.
    reads_own_input = op.get("reads_own_input", False)
    if reads_own_input:
        input_data = input_real  # the resolved path itself, not its bytes
        try:
            input_byte_count = os.path.getsize(input_real)
        except OSError as e:
            return error_payload(f"failed to stat input_path: {e}")
        if input_byte_count == 0:
            return error_payload("input file is empty")
    else:
        try:
            with open(input_real, "rb") as f:
                input_data = f.read()
        except OSError as e:
            return error_payload(f"failed to read input_path: {e}")
        if not input_data:
            return error_payload("input file is empty")
        input_byte_count = len(input_data)

    try:
        output_data = op["transform"](input_data, params, instruction, output_real)
    except SidecarError as e:
        return error_payload(str(e))

    ok, reason = op["validate"](input_data, output_data, output_real, params)
    if not ok:
        reject_path = output_real + ".rejected"
        try:
            write_bytes_no_symlink(reject_path, output_data)
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
        write_bytes_no_symlink(write_path, output_data)
    except OSError as e:
        return error_payload(f"failed to write output: {e}")

    payload = {
        "status": "success",
        "message": f"processed '{operation}' -> {write_path}",
        "operation": operation,
        "input_bytes": input_byte_count,
        "output_bytes": len(output_data),
        "output_path": write_path,
    }
    if engine:
        # Tells the caller the hybrid op ran deterministically (no model).
        payload["engine"] = engine
    return payload


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
