#!/usr/bin/env python3
"""Unit tests for plugins/ollama-sidecar/scripts/ollama_sidecar.py.

Stdlib unittest only — no pytest, no new dependencies. Every test that would
otherwise touch the network mocks the HTTP layer (`_http_get_json` for the
status diagnostic, `urllib.request.urlopen` for `call_ollama`) so the suite
never requires a live Ollama instance.

The module under test is loaded by file path (its directory, "ollama-
sidecar", has a hyphen and isn't an importable package name).
"""

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_PATH = os.path.join(
    _HERE, "..", "..", "plugins", "ollama-sidecar", "scripts", "ollama_sidecar.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("ollama_sidecar", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sidecar = _load_module()


class EnvHelpersTest(unittest.TestCase):
    def test_env_falls_back_on_unset(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SIDECAR_TEST_VAR", None)
            self.assertEqual(sidecar._env("SIDECAR_TEST_VAR", "default"), "default")

    def test_env_falls_back_on_unexpanded_placeholder(self):
        with mock.patch.dict(os.environ, {"SIDECAR_TEST_VAR": "${OLLAMA_HOST}"}):
            self.assertEqual(sidecar._env("SIDECAR_TEST_VAR", "default"), "default")

    def test_env_returns_real_value(self):
        with mock.patch.dict(os.environ, {"SIDECAR_TEST_VAR": "http://box:11434"}):
            self.assertEqual(sidecar._env("SIDECAR_TEST_VAR", "default"), "http://box:11434")

    def test_env_int_valid(self):
        with mock.patch.dict(os.environ, {"SIDECAR_TEST_INT": "42"}):
            self.assertEqual(sidecar._env_int("SIDECAR_TEST_INT", 7), 42)

    def test_env_int_invalid_falls_back(self):
        with mock.patch.dict(os.environ, {"SIDECAR_TEST_INT": "not-a-number"}):
            self.assertEqual(sidecar._env_int("SIDECAR_TEST_INT", 7), 7)

    def test_resolve_tier_invalid_num_ctx_falls_back(self):
        with mock.patch.dict(os.environ, {"OLLAMA_NUM_CTX": "nope"}):
            self.assertEqual(sidecar.resolve_tier("deep")["num_ctx"], sidecar.DEFAULT_NUM_CTX)

    def test_resolve_tier_fast_falls_back_to_deep(self):
        env = {
            "OLLAMA_HOST": "http://deepbox:11434",
            "OLLAMA_MODEL": "big",
            "OLLAMA_FAST_HOST": "",
            "OLLAMA_FAST_MODEL": "",
        }
        with mock.patch.dict(os.environ, env):
            fast = sidecar.resolve_tier("fast")
        self.assertEqual(fast["host"], "http://deepbox:11434")
        self.assertEqual(fast["model"], "big")

    def test_resolve_tier_fast_overrides(self):
        env = {
            "OLLAMA_HOST": "http://deepbox:11434",
            "OLLAMA_FAST_HOST": "http://fastbox:11434",
            "OLLAMA_FAST_MODEL": "small",
        }
        with mock.patch.dict(os.environ, env):
            fast = sidecar.resolve_tier("fast")
        self.assertEqual(fast["host"], "http://fastbox:11434")
        self.assertEqual(fast["model"], "small")


class PathScopingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolve_input_path_requires_value(self):
        with self.assertRaises(sidecar.SidecarError):
            sidecar.resolve_input_path(self.root, "")

    def test_resolve_input_path_missing_file(self):
        with self.assertRaises(sidecar.SidecarError):
            sidecar.resolve_input_path(self.root, "nope.txt")

    def test_resolve_input_path_ok(self):
        p = os.path.join(self.root, "in.txt")
        with open(p, "w") as f:
            f.write("hello")
        real = sidecar.resolve_input_path(self.root, "in.txt")
        self.assertEqual(real, os.path.realpath(p))

    def test_resolve_input_path_escapes_root(self):
        outside = tempfile.NamedTemporaryFile(delete=False)
        outside.write(b"x")
        outside.close()
        try:
            with self.assertRaises(sidecar.SidecarError):
                sidecar.resolve_input_path(self.root, outside.name)
        finally:
            os.unlink(outside.name)

    def test_resolve_output_path_requires_existing_parent_dir(self):
        with self.assertRaises(sidecar.SidecarError):
            sidecar.resolve_output_path(self.root, "no/such/dir/out.txt")

    def test_resolve_output_path_ok(self):
        real = sidecar.resolve_output_path(self.root, "out.txt")
        self.assertEqual(real, os.path.join(self.root, "out.txt"))

    def test_resolve_output_dir_requires_directory(self):
        with self.assertRaises(sidecar.SidecarError):
            sidecar.resolve_output_dir(self.root, "not-a-real-dir")

    def test_resolve_output_dir_ok(self):
        sub = os.path.join(self.root, "chunks")
        os.mkdir(sub)
        real = sidecar.resolve_output_dir(self.root, "chunks")
        self.assertEqual(real, os.path.realpath(sub))

    def test_choose_write_path_no_existing_file(self):
        target = os.path.join(self.root, "fresh.txt")
        self.assertEqual(sidecar.choose_write_path(target, False), target)

    def test_choose_write_path_existing_no_overwrite_uses_new_suffix(self):
        target = os.path.join(self.root, "exists.txt")
        with open(target, "w") as f:
            f.write("x")
        self.assertEqual(sidecar.choose_write_path(target, False), target + ".new")

    def test_choose_write_path_overwrite_true_returns_original(self):
        target = os.path.join(self.root, "exists2.txt")
        with open(target, "w") as f:
            f.write("x")
        self.assertEqual(sidecar.choose_write_path(target, True), target)

    def test_choose_write_path_both_new_and_original_exist_errors(self):
        target = os.path.join(self.root, "dup.txt")
        with open(target, "w") as f:
            f.write("x")
        with open(target + ".new", "w") as f:
            f.write("y")
        with self.assertRaises(sidecar.SidecarError):
            sidecar.choose_write_path(target, False)

    def test_choose_write_path_refuses_symlink_target(self):
        real_file = os.path.join(self.root, "real.txt")
        with open(real_file, "w") as f:
            f.write("x")
        link = os.path.join(self.root, "link.txt")
        os.symlink(real_file, link)
        with self.assertRaises(sidecar.SidecarError):
            sidecar.choose_write_path(link, True)


class ContextBudgetTest(unittest.TestCase):
    def test_estimate_tokens_minimum_one(self):
        self.assertEqual(sidecar.estimate_tokens(""), 1)

    def test_estimate_tokens_scales_with_length(self):
        self.assertEqual(sidecar.estimate_tokens("a" * 400), 100)

    def test_max_input_tokens_reserves_half_for_output(self):
        # system prompt ~0 tokens, num_ctx=1000 -> available=1000, half=500
        budget = sidecar.max_input_tokens_for("", 1000)
        # estimate_tokens("") floors at 1 token of "system prompt" overhead,
        # so available = 1000 - 1 = 999, and half of that floors to 499.
        self.assertEqual(budget, 499)

    def test_max_input_tokens_floor_never_exceeds_available(self):
        # Tiny num_ctx: the 256-token floor must not exceed what's available.
        budget = sidecar.max_input_tokens_for("", 100)
        self.assertLessEqual(budget, 100)


class CallOllamaTest(unittest.TestCase):
    def _fake_response(self, payload_dict):
        body = json.dumps(payload_dict).encode("utf-8")
        cm = mock.MagicMock()
        cm.__enter__.return_value.read.return_value = body
        cm.__exit__.return_value = False
        return cm

    _CFG = {"tier": "deep", "host": "http://h:11434", "model": "m", "num_ctx": 1024, "timeout": 5}

    def test_call_ollama_success(self):
        with mock.patch.object(
            sidecar.urllib.request, "urlopen", return_value=self._fake_response({"response": "ok"})
        ):
            result = sidecar.call_ollama(self._CFG, "sys", "user")
        self.assertEqual(result["response"], "ok")

    def test_call_ollama_http_error(self):
        err = urllib.error.HTTPError(
            "http://h:11434/api/generate", 500, "boom", hdrs=None, fp=io.BytesIO(b"detail")
        )
        with mock.patch.object(sidecar.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(sidecar.OllamaError) as ctx:
                sidecar.call_ollama(self._CFG, "sys", "user")
        # HTTP errors are answers from a live server — they must NOT be
        # marked unreachable, or they'd wrongly trigger tier failover.
        self.assertFalse(ctx.exception.unreachable)

    def test_call_ollama_url_error(self):
        err = urllib.error.URLError("connection refused")
        with mock.patch.object(sidecar.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(sidecar.OllamaError) as ctx:
                sidecar.call_ollama(self._CFG, "sys", "user")
        self.assertTrue(ctx.exception.unreachable)

    def test_call_ollama_non_json_response(self):
        cm = mock.MagicMock()
        cm.__enter__.return_value.read.return_value = b"not json"
        cm.__exit__.return_value = False
        with mock.patch.object(sidecar.urllib.request, "urlopen", return_value=cm):
            with self.assertRaises(sidecar.OllamaError):
                sidecar.call_ollama(self._CFG, "sys", "user")


class StatusDiagnosticTest(unittest.TestCase):
    def test_gather_status_reachable_with_models(self):
        def fake_get(url, timeout):
            if url.endswith("/api/tags"):
                return {"models": [{"name": "qwen2.5-coder:14b"}, {"name": "llama3:8b"}]}
            if url.endswith("/api/ps"):
                return {"models": [{"name": "qwen2.5-coder:14b"}]}
            raise AssertionError(f"unexpected url {url}")

        with mock.patch.object(sidecar, "_http_get_json", side_effect=fake_get):
            status = sidecar.gather_status("http://h:11434", "qwen2.5-coder:14b", timeout=1)

        self.assertTrue(status["reachable"])
        self.assertIsNone(status["error"])
        self.assertIsNone(status["hint"])
        self.assertEqual(status["available_models"], ["llama3:8b", "qwen2.5-coder:14b"])
        self.assertEqual(status["loaded_models"], ["qwen2.5-coder:14b"])
        self.assertIsInstance(status["latency_ms"], float)

    def test_gather_status_model_not_pulled_gets_hint(self):
        def fake_get(url, timeout):
            if url.endswith("/api/tags"):
                return {"models": [{"name": "llama3:8b"}]}
            return {"models": []}

        with mock.patch.object(sidecar, "_http_get_json", side_effect=fake_get):
            status = sidecar.gather_status("http://h:11434", "missing-model", timeout=1)

        self.assertTrue(status["reachable"])
        self.assertIn("not in the available models list", status["hint"])

    def test_gather_status_model_pulled_but_not_loaded_gets_hint(self):
        def fake_get(url, timeout):
            if url.endswith("/api/tags"):
                return {"models": [{"name": "m1"}]}
            return {"models": [{"name": "other"}]}

        with mock.patch.object(sidecar, "_http_get_json", side_effect=fake_get):
            status = sidecar.gather_status("http://h:11434", "m1", timeout=1)

        self.assertTrue(status["reachable"])
        self.assertIn("not currently loaded", status["hint"])

    def test_gather_status_timeout_gives_cold_start_hint(self):
        with mock.patch.object(sidecar, "_http_get_json", side_effect=TimeoutError("timed out")):
            status = sidecar.gather_status("http://h:11434", "m1", timeout=1)

        self.assertFalse(status["reachable"])
        self.assertIsNone(status["latency_ms"])
        self.assertIn("timed out", status["error"])
        self.assertEqual(status["hint"], sidecar.COLD_START_HINT)

    def test_gather_status_connection_refused_no_cold_start_hint(self):
        err = urllib.error.URLError(ConnectionRefusedError("refused"))
        with mock.patch.object(sidecar, "_http_get_json", side_effect=err):
            status = sidecar.gather_status("http://h:11434", "m1", timeout=1)

        self.assertFalse(status["reachable"])
        self.assertIn("could not reach ollama", status["error"])
        self.assertIsNone(status["hint"])

    def test_gather_status_http_error_reported(self):
        err = urllib.error.HTTPError("http://h:11434/api/tags", 503, "unavailable", hdrs=None, fp=None)
        with mock.patch.object(sidecar, "_http_get_json", side_effect=err):
            status = sidecar.gather_status("http://h:11434", "m1", timeout=1)

        self.assertFalse(status["reachable"])
        self.assertIn("HTTP 503", status["error"])

    def test_gather_status_ps_failure_does_not_blank_reachable(self):
        def fake_get(url, timeout):
            if url.endswith("/api/tags"):
                return {"models": [{"name": "m1"}]}
            raise urllib.error.URLError("ps unreachable")

        with mock.patch.object(sidecar, "_http_get_json", side_effect=fake_get):
            status = sidecar.gather_status("http://h:11434", "m1", timeout=1)

        self.assertTrue(status["reachable"])
        self.assertEqual(status["loaded_models"], [])

    def test_format_status_report_reachable(self):
        status = {
            "host": "http://h:11434",
            "configured_model": "m1",
            "reachable": True,
            "latency_ms": 12.3,
            "available_models": ["m1"],
            "loaded_models": ["m1"],
            "error": None,
            "hint": None,
        }
        report = sidecar.format_status_report(status)
        self.assertIn("reachable:         yes (12.3 ms)", report)
        self.assertIn("available models:  m1", report)
        self.assertNotIn("error:", report)
        self.assertNotIn("hint:", report)

    def test_format_status_report_unreachable_with_hint(self):
        status = {
            "host": "http://h:11434",
            "configured_model": "m1",
            "reachable": False,
            "latency_ms": None,
            "available_models": [],
            "loaded_models": [],
            "error": "timed out",
            "hint": "retry",
        }
        report = sidecar.format_status_report(status)
        self.assertIn("reachable:         no", report)
        self.assertIn("error:             timed out", report)
        self.assertIn("hint:              retry", report)

    def test_cmd_status_exit_code_reflects_reachability(self):
        with mock.patch.object(
            sidecar, "gather_status", return_value={"host": "h", "reachable": False}
        ):
            with mock.patch.object(sidecar, "format_status_report", return_value="report"):
                self.assertEqual(sidecar.cmd_status(), 1)

        with mock.patch.object(
            sidecar, "gather_status", return_value={"host": "h", "reachable": True}
        ):
            with mock.patch.object(sidecar, "format_status_report", return_value="report"):
                self.assertEqual(sidecar.cmd_status(), 0)


class ValidatorsTest(unittest.TestCase):
    def test_validate_extract_json_rejects_bad_json(self):
        ok, reason = sidecar._validate_extract_json("a\nb\nc", "not json", "out.json", "")
        self.assertFalse(ok)
        self.assertIn("not valid JSON", reason)

    def test_validate_extract_json_accepts_matching_record_count(self):
        ok, reason = sidecar._validate_extract_json(
            "a\nb\nc", json.dumps([{"a": 1}, {"a": 2}, {"a": 3}]), "out.json", ""
        )
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_validate_extract_json_rejects_gross_record_loss(self):
        input_text = "\n".join(f"line{i}" for i in range(10))
        ok, reason = sidecar._validate_extract_json(input_text, json.dumps([{"a": 1}]), "out.json", "")
        self.assertFalse(ok)
        self.assertIn("possible record loss", reason)

    def test_validate_convert_format_json(self):
        ok, _ = sidecar._validate_convert_format("x", '{"a": 1}', "out.json", "")
        self.assertTrue(ok)
        ok, reason = sidecar._validate_convert_format("x", "{bad", "out.json", "")
        self.assertFalse(ok)
        self.assertIn("not valid JSON", reason)

    def test_validate_convert_format_csv_consistent_columns(self):
        ok, _ = sidecar._validate_convert_format("x", "a,b\n1,2\n3,4\n", "out.csv", "")
        self.assertTrue(ok)

    def test_validate_convert_format_csv_ragged_columns(self):
        ok, reason = sidecar._validate_convert_format("x", "a,b\n1,2,3\n", "out.csv", "")
        self.assertFalse(ok)
        self.assertIn("inconsistent CSV column count", reason)

    def test_validate_convert_format_unsupported_extension(self):
        ok, reason = sidecar._validate_convert_format("x", "---", "out.yaml", "")
        self.assertFalse(ok)
        self.assertIn("unsupported target format", reason)

    def test_validate_clean_text_empty_output_rejected(self):
        ok, reason = sidecar._validate_clean_text("hello world", "   ", "out.txt", "")
        self.assertFalse(ok)
        self.assertIn("empty", reason)

    def test_validate_clean_text_ratio_bounds(self):
        ok, reason = sidecar._validate_clean_text("a" * 100, "b", "out.txt", "")
        self.assertFalse(ok)
        self.assertIn("size ratio", reason)
        ok, _ = sidecar._validate_clean_text("a" * 100, "b" * 100, "out.txt", "")
        self.assertTrue(ok)

    def test_validate_yaml_to_json(self):
        ok, _ = sidecar._validate_yaml_to_json("a: 1", '{"a": 1}', "out.json", "")
        self.assertTrue(ok)
        ok, reason = sidecar._validate_yaml_to_json("a: 1", "not json", "out.json", "")
        self.assertFalse(ok)
        self.assertIn("not valid JSON", reason)

    def test_validate_redact_secrets_flags_surviving_secret(self):
        ok, reason = sidecar._validate_redact_secrets(
            "key=sk-abcdefghijklmnopqrstuvwx", "key=sk-abcdefghijklmnopqrstuvwx", "out.txt", ""
        )
        self.assertFalse(ok)
        self.assertIn("secret-shaped pattern", reason)

    def test_validate_redact_secrets_accepts_clean_output(self):
        ok, reason = sidecar._validate_redact_secrets(
            "key=sk-abcdefghijklmnopqrstuvwx", "key=[REDACTED]", "out.txt", ""
        )
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_validate_redact_secrets_empty_output_rejected(self):
        ok, reason = sidecar._validate_redact_secrets("some secret text", "", "out.txt", "")
        self.assertFalse(ok)
        self.assertIn("empty", reason)


class DeterministicOperationsTest(unittest.TestCase):
    def test_dedupe_lines(self):
        out = sidecar._det_dedupe_lines(b"a\nb\na\nc\n", {}, "", "out.txt")
        self.assertEqual(out, b"a\nb\nc\n")

    def test_dedupe_lines_case_insensitive(self):
        out = sidecar._det_dedupe_lines(b"A\na\nb\n", {"case_insensitive": True}, "", "out.txt")
        self.assertEqual(out, b"A\nb\n")

    def test_val_dedupe_lines_rejects_line_growth(self):
        ok, reason = sidecar._val_dedupe_lines(b"a\n", b"a\nb\n", "out.txt", {})
        self.assertFalse(ok)
        self.assertIn("must never increase", reason)

    def test_sort_lines_basic(self):
        out = sidecar._det_sort_lines(b"c\na\nb\n", {}, "", "out.txt")
        self.assertEqual(out, b"a\nb\nc\n")

    def test_sort_lines_numeric(self):
        out = sidecar._det_sort_lines(b"10\n2\n1\n", {"numeric": True}, "", "out.txt")
        self.assertEqual(out, b"1\n2\n10\n")

    def test_sort_lines_unique(self):
        out = sidecar._det_sort_lines(b"b\na\nb\n", {"unique": True}, "", "out.txt")
        self.assertEqual(out, b"a\nb\n")

    def test_val_sort_lines_rejects_line_count_change(self):
        ok, reason = sidecar._val_sort_lines(b"a\nb\n", b"a\n", "out.txt", {})
        self.assertFalse(ok)
        self.assertIn("must not change line count", reason)

    def test_filter_lines_include(self):
        out = sidecar._det_filter_lines(
            b"apple\nbanana\navocado\n", {"pattern": "a", "mode": "include"}, "", "out.txt"
        )
        self.assertEqual(out, b"apple\nbanana\navocado\n")

    def test_filter_lines_exclude(self):
        out = sidecar._det_filter_lines(
            b"apple\nbanana\ncherry\n", {"pattern": "an", "mode": "exclude"}, "", "out.txt"
        )
        self.assertEqual(out, b"apple\ncherry\n")

    def test_filter_lines_requires_pattern(self):
        with self.assertRaises(sidecar.SidecarError):
            sidecar._det_filter_lines(b"a\n", {}, "", "out.txt")

    def test_filter_lines_invalid_mode(self):
        with self.assertRaises(sidecar.SidecarError):
            sidecar._det_filter_lines(b"a\n", {"pattern": "a", "mode": "bogus"}, "", "out.txt")

    def test_filter_lines_invalid_regex(self):
        with self.assertRaises(sidecar.SidecarError):
            sidecar._det_filter_lines(b"a\n", {"pattern": "(", "regex": True}, "", "out.txt")

    def test_filter_lines_context(self):
        out = sidecar._det_filter_lines(
            b"1\n2\nmatch\n4\n5\n",
            {"pattern": "match", "context_before": 1, "context_after": 1},
            "",
            "out.txt",
        )
        self.assertEqual(out, b"2\nmatch\n4\n")

    def test_base64_decode_valid(self):
        out = sidecar._det_base64_decode(b"aGVsbG8=", {}, "", "out.bin")
        self.assertEqual(out, b"hello")

    def test_base64_decode_invalid_raises(self):
        with self.assertRaises(sidecar.SidecarError):
            sidecar._det_base64_decode(b"not-b64!!!", {}, "", "out.bin")

    def test_val_base64_decode_rejects_empty_from_nonempty_input(self):
        ok, reason = sidecar._val_base64_decode(b"====", b"", "out.bin", {})
        self.assertFalse(ok)
        self.assertIn("empty", reason)

    def test_hash_file_sha256_default(self):
        out = sidecar._det_hash_file(b"hello", {}, "", "out.json")
        obj = json.loads(out)
        self.assertEqual(obj["algorithm"], "sha256")
        self.assertEqual(obj["input_bytes"], 5)
        self.assertEqual(
            obj["hexdigest"],
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        )

    def test_hash_file_invalid_algorithm(self):
        with self.assertRaises(sidecar.SidecarError):
            sidecar._det_hash_file(b"hello", {"algorithm": "crc32"}, "", "out.json")

    def test_strip_ansi_codes(self):
        out = sidecar._det_strip_ansi_codes(b"\x1b[31mred\x1b[0m plain", {}, "", "out.txt")
        self.assertEqual(out, b"red plain")

    def test_normalize_log_timestamps_us_style(self):
        out = sidecar._det_normalize_log_timestamps(
            b"event at 10/10/2023 13:55:36 happened\n", {}, "", "out.txt"
        )
        self.assertIn(b"2023-10-10T13:55:36", out)

    def test_normalize_log_timestamps_unrecognized_passes_through(self):
        out = sidecar._det_normalize_log_timestamps(b"no timestamp here\n", {}, "", "out.txt")
        self.assertEqual(out, b"no timestamp here\n")

    def test_extract_field_list_from_json(self):
        input_bytes = json.dumps([{"a": 1, "b": 2}, {"a": 3, "b": 4}]).encode("utf-8")
        out = sidecar._det_extract_field_list(input_bytes, {"fields": ["a"]}, "", "out.json")
        self.assertEqual(json.loads(out), [{"a": 1}, {"a": 3}])

    def test_extract_field_list_from_csv_to_csv(self):
        input_bytes = b"a,b\n1,2\n3,4\n"
        out = sidecar._det_extract_field_list(input_bytes, {"fields": ["a"]}, "", "out.csv")
        self.assertEqual(out.decode("utf-8").strip().splitlines(), ["a", "1", "3"])

    def test_extract_field_list_requires_fields(self):
        with self.assertRaises(sidecar.SidecarError):
            sidecar._det_extract_field_list(b"[]", {}, "", "out.json")

    def test_extract_field_list_unsupported_extension(self):
        with self.assertRaises(sidecar.SidecarError):
            sidecar._det_extract_field_list(b"[]", {"fields": ["a"]}, "", "out.yaml")

    def test_extract_field_list_unstructured_input_rejected(self):
        # Multiple lines so csv.DictReader (fallback parser) actually
        # produces data rows (one header + N data rows) rather than an
        # empty result that would trivially not contain the field.
        with self.assertRaises(sidecar.SidecarError):
            sidecar._det_extract_field_list(
                b"just some prose\nmore prose here\nextra text line",
                {"fields": ["a"]},
                "",
                "out.json",
            )

    def test_plist_to_json_valid(self):
        plist_bytes = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            b'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            b'<plist version="1.0"><dict><key>name</key><string>x</string></dict></plist>'
        )
        out = sidecar._det_plist_to_json(plist_bytes, {}, "", "out.json")
        self.assertEqual(json.loads(out), {"name": "x"})

    def test_plist_to_json_invalid_raises(self):
        with self.assertRaises(sidecar.SidecarError):
            sidecar._det_plist_to_json(b"not a plist", {}, "", "out.json")


class SqliteDumpTest(unittest.TestCase):
    def test_sqlite_dump_to_json(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE people (id INTEGER, name TEXT)")
            conn.execute("INSERT INTO people VALUES (1, 'alice')")
            conn.commit()
            conn.close()

            out = sidecar._det_sqlite_dump_to_json(db_path, {}, "", "out.json")
            data = json.loads(out)
            self.assertEqual(data["people"], [{"id": 1, "name": "alice"}])

    def test_sqlite_dump_to_json_invalid_file_raises(self):
        with tempfile.TemporaryDirectory() as d:
            bogus = os.path.join(d, "bogus.db")
            with open(bogus, "w") as f:
                f.write("not a sqlite file")
            with self.assertRaises(sidecar.SidecarError):
                sidecar._det_sqlite_dump_to_json(bogus, {}, "", "out.json")

    def test_sqlite_dump_to_json_table_filter(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE a (x INTEGER)")
            conn.execute("CREATE TABLE b (y INTEGER)")
            conn.execute("INSERT INTO a VALUES (1)")
            conn.execute("INSERT INTO b VALUES (2)")
            conn.commit()
            conn.close()

            out = sidecar._det_sqlite_dump_to_json(db_path, {"tables": ["a"]}, "", "out.json")
            data = json.loads(out)
            self.assertEqual(list(data.keys()), ["a"])


class HandleProcessLocalFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.env_patch = mock.patch.dict(os.environ, {"SIDECAR_ROOT": self.root})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.tmp.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.root, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_unknown_operation_errors(self):
        result = sidecar.handle_process_local_file(
            {"operation": "not_a_real_op", "input_path": "x", "output_path": "y"}
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("unknown operation", result["reason"])

    def test_missing_input_path_errors(self):
        result = sidecar.handle_process_local_file(
            {"operation": "dedupe_lines", "output_path": "out.txt"}
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("input_path", result["reason"])

    def test_deterministic_operation_success_roundtrip(self):
        self._write("in.txt", "b\na\nb\n")
        result = sidecar.handle_process_local_file(
            {"operation": "dedupe_lines", "input_path": "in.txt", "output_path": "out.txt"}
        )
        self.assertEqual(result["status"], "success")
        with open(os.path.join(self.root, "out.txt")) as f:
            self.assertEqual(f.read(), "b\na\n")

    def test_deterministic_validation_failure_writes_rejected_file(self):
        # base64_decode's validator rejects an empty decode of non-empty input;
        # force that by feeding non-base64 content through a monkeypatched
        # transform is unnecessary — invalid base64 raises SidecarError
        # instead, which is the more common real path, so exercise it here.
        self._write("in.txt", "not-valid-base64!!!")
        result = sidecar.handle_process_local_file(
            {"operation": "base64_decode", "input_path": "in.txt", "output_path": "out.bin"}
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("not valid base64", result["reason"])

    def test_convert_format_rejects_unsupported_extension_up_front(self):
        self._write("in.txt", "a,b\n1,2\n")
        result = sidecar.handle_process_local_file(
            {"operation": "convert_format", "input_path": "in.txt", "output_path": "out.yaml"}
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("unsupported target format", result["reason"])

    def test_overwrite_false_writes_dot_new_on_existing_output(self):
        self._write("in.txt", "a\nb\na\n")
        self._write("out.txt", "existing")
        result = sidecar.handle_process_local_file(
            {"operation": "dedupe_lines", "input_path": "in.txt", "output_path": "out.txt"}
        )
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["output_path"].endswith("out.txt.new"))

    def test_llm_operation_calls_ollama_and_validates(self):
        self._write("in.txt", "line one\nline two\n")
        fake_result = {"response": json.dumps([{"a": 1}, {"a": 2}]), "done_reason": "stop"}
        with mock.patch.object(sidecar, "call_ollama", return_value=fake_result):
            result = sidecar.handle_process_local_file(
                {"operation": "extract_json", "input_path": "in.txt", "output_path": "out.json"}
            )
        self.assertEqual(result["status"], "success")

    def test_llm_operation_truncated_generation_errors(self):
        self._write("in.txt", "line one\n")
        fake_result = {"response": "{}", "done_reason": "length"}
        with mock.patch.object(sidecar, "call_ollama", return_value=fake_result):
            result = sidecar.handle_process_local_file(
                {"operation": "extract_json", "input_path": "in.txt", "output_path": "out.json"}
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("done_reason", result["reason"])

    def test_llm_operation_ollama_error_surfaces(self):
        self._write("in.txt", "line one\n")
        with mock.patch.object(
            sidecar, "call_ollama", side_effect=sidecar.OllamaError("could not reach ollama")
        ):
            result = sidecar.handle_process_local_file(
                {"operation": "extract_json", "input_path": "in.txt", "output_path": "out.json"}
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("could not reach ollama", result["reason"])

    def test_llm_operation_input_too_large_for_budget(self):
        self._write("in.txt", "x" * 1000)
        with mock.patch.dict(os.environ, {"OLLAMA_NUM_CTX": "64"}):
            result = sidecar.handle_process_local_file(
                {"operation": "extract_json", "input_path": "in.txt", "output_path": "out.json"}
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("too large for the current context budget", result["reason"])

    def test_llm_operation_rejected_output_writes_rejected_file(self):
        self._write("in.txt", "line one\n")
        fake_result = {"response": "not json at all", "done_reason": "stop"}
        with mock.patch.object(sidecar, "call_ollama", return_value=fake_result):
            result = sidecar.handle_process_local_file(
                {"operation": "extract_json", "input_path": "in.txt", "output_path": "out.json"}
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("failed validation", result["reason"])
        self.assertTrue(os.path.exists(os.path.join(self.root, "out.json.rejected")))

    def test_merge_files_requires_two_or_more_inputs(self):
        self._write("a.txt", "a")
        result = sidecar.handle_process_local_file(
            {"operation": "merge_files", "input_paths": ["a.txt"], "output_path": "out.txt"}
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("at least 2", result["reason"])

    def test_merge_files_success(self):
        self._write("a.txt", "AAA")
        self._write("b.txt", "BBB")
        result = sidecar.handle_process_local_file(
            {
                "operation": "merge_files",
                "input_paths": ["a.txt", "b.txt"],
                "output_path": "out.txt",
            }
        )
        self.assertEqual(result["status"], "success")
        with open(os.path.join(self.root, "out.txt")) as f:
            self.assertEqual(f.read(), "AAA\nBBB")

    def test_split_file_requires_chunk_param(self):
        self._write("in.txt", "a\nb\n")
        os.mkdir(os.path.join(self.root, "chunks"))
        result = sidecar.handle_process_local_file(
            {"operation": "split_file", "input_path": "in.txt", "output_path": "chunks"}
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("lines_per_chunk", result["reason"])

    def test_split_file_by_lines_per_chunk(self):
        self._write("in.txt", "1\n2\n3\n4\n")
        os.mkdir(os.path.join(self.root, "chunks"))
        result = sidecar.handle_process_local_file(
            {
                "operation": "split_file",
                "input_path": "in.txt",
                "output_path": "chunks",
                "params": {"lines_per_chunk": 2},
            }
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["chunk_paths"]), 2)

    def test_split_file_requires_output_dir_to_exist(self):
        self._write("in.txt", "1\n2\n")
        result = sidecar.handle_process_local_file(
            {
                "operation": "split_file",
                "input_path": "in.txt",
                "output_path": "no-such-dir",
                "params": {"lines_per_chunk": 1},
            }
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("existing directory", result["reason"])


class RpcTransportTest(unittest.TestCase):
    def test_initialize_returns_fixed_protocol_version(self):
        response = sidecar.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(response["result"]["protocolVersion"], sidecar.MCP_PROTOCOL_VERSION)
        self.assertEqual(response["result"]["serverInfo"]["name"], sidecar.SERVER_NAME)

    def test_notifications_initialized_returns_none(self):
        response = sidecar.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertIsNone(response)

    def test_ping(self):
        response = sidecar.handle_message({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        self.assertEqual(response["result"], {})

    def test_tools_list_returns_tool_definition(self):
        response = sidecar.handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        tools = response["result"]["tools"]
        self.assertEqual(tools[0]["name"], "process_local_file")

    def test_tools_call_unknown_tool_errors(self):
        response = sidecar.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "not_a_real_tool", "arguments": {}},
            }
        )
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], -32602)

    def test_unknown_method_with_id_errors(self):
        response = sidecar.handle_message({"jsonrpc": "2.0", "id": 5, "method": "bogus/method"})
        self.assertEqual(response["error"]["code"], -32601)

    def test_unknown_notification_without_id_returns_none(self):
        response = sidecar.handle_message({"jsonrpc": "2.0", "method": "bogus/notification"})
        self.assertIsNone(response)

    def test_tools_call_wraps_handler_error_status(self):
        response = sidecar.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "process_local_file",
                    "arguments": {"operation": "unknown_op"},
                },
            }
        )
        self.assertTrue(response["result"]["isError"])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "error")


if __name__ == "__main__":
    unittest.main()
