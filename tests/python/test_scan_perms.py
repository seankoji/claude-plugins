#!/usr/bin/env python3
"""Unit tests for plugins/claude-tuneup/scripts/scan_perms.py.

Stdlib unittest only — no pytest, no new dependencies. All filesystem access
is confined to tempdirs; the real ~/.claude/projects is never touched (the
module's PROJECTS_DIR is monkeypatched to a tempdir for every scan test).

The module under test is loaded by file path (its directory, "claude-
tuneup", has a hyphen and isn't an importable package name) — same pattern
as test_ollama_sidecar.py.
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_PATH = os.path.join(
    _HERE, "..", "..", "plugins", "claude-tuneup", "scripts", "scan_perms.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("scan_perms", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scan_perms = _load_module()


def _tool_use_line(name, input_dict):
    return json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name, "input": input_dict}]},
        }
    )


def _write_transcript(dir_path, filename, lines):
    path = os.path.join(dir_path, filename)
    with open(path, "w") as f:
        for line in lines:
            f.write(line + "\n")
    return path


class FirstRealTokenTest(unittest.TestCase):
    def test_simple_head_and_sub(self):
        self.assertEqual(scan_perms.first_real_token("git status"), ([], "git", "status"))

    def test_head_only_no_sub(self):
        self.assertEqual(scan_perms.first_real_token("ls"), ([], "ls", None))

    def test_empty_command(self):
        self.assertEqual(scan_perms.first_real_token(""), ([], "", None))

    def test_sudo_leader_kept(self):
        self.assertEqual(
            scan_perms.first_real_token("sudo systemctl restart nginx"),
            (["sudo"], "systemctl", "restart"),
        )

    def test_timeout_leader_walked_past_not_folded(self):
        # timeout carries a variable duration argument; it's walked past but
        # not added to `leaders` (see the docstring in scan_perms.py).
        self.assertEqual(
            scan_perms.first_real_token("timeout 180 systemctl restart"),
            ([], "systemctl", "restart"),
        )

    def test_env_leader_with_var_assignments_walked_past(self):
        self.assertEqual(
            scan_perms.first_real_token("env FOO=bar BAZ=qux npm test"),
            ([], "npm", "test"),
        )

    def test_env_var_prefix_stripped(self):
        self.assertEqual(scan_perms.first_real_token("FOO=bar git status"), ([], "git", "status"))

    def test_sub_starting_with_dash_ignored(self):
        self.assertEqual(scan_perms.first_real_token("grep -rn foo"), ([], "grep", None))

    def test_path_head_basenamed(self):
        self.assertEqual(scan_perms.first_real_token("/usr/bin/git status"), ([], "git", "status"))


class FirstSegmentTest(unittest.TestCase):
    def test_splits_on_ampersand(self):
        self.assertEqual(scan_perms.first_segment("git status && git log"), "git status")

    def test_splits_on_pipe(self):
        self.assertEqual(scan_perms.first_segment("cat file | grep x"), "cat file")

    def test_splits_on_semicolon(self):
        self.assertEqual(scan_perms.first_segment("ls; pwd"), "ls")

    def test_no_separator_returns_whole(self):
        self.assertEqual(scan_perms.first_segment("git status"), "git status")


class FindRecentTranscriptsTest(unittest.TestCase):
    def test_missing_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            missing = os.path.join(d, "does-not-exist")
            with mock.patch.object(scan_perms, "PROJECTS_DIR", scan_perms.Path(missing)):
                self.assertEqual(scan_perms.find_recent_transcripts(50), [])

    def test_respects_limit_and_mtime_order(self):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i in range(3):
                p = os.path.join(d, f"t{i}.jsonl")
                with open(p, "w") as f:
                    f.write("{}\n")
                os.utime(p, (time.time() + i, time.time() + i))
                paths.append(p)
            with mock.patch.object(scan_perms, "PROJECTS_DIR", scan_perms.Path(d)):
                result = scan_perms.find_recent_transcripts(2)
            self.assertEqual(len(result), 2)
            # most-recently-modified file (t2) must come first.
            self.assertEqual(os.path.basename(str(result[0])), "t2.jsonl")


class IterToolUsesTest(unittest.TestCase):
    def test_extracts_bash_tool_use(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_transcript(d, "a.jsonl", [_tool_use_line("Bash", {"command": "git status"})])
            blocks = list(scan_perms.iter_tool_uses([scan_perms.Path(path)]))
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0]["input"]["command"], "git status")

    def test_skips_non_assistant_messages(self):
        with tempfile.TemporaryDirectory() as d:
            lines = [
                json.dumps({"type": "user", "message": {"content": "hi"}}),
                _tool_use_line("Bash", {"command": "ls"}),
            ]
            path = _write_transcript(d, "a.jsonl", lines)
            blocks = list(scan_perms.iter_tool_uses([scan_perms.Path(path)]))
            self.assertEqual(len(blocks), 1)

    def test_malformed_json_line_does_not_crash_and_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            lines = [
                "{not valid json!!",
                _tool_use_line("Bash", {"command": "git status"}),
            ]
            path = _write_transcript(d, "a.jsonl", lines)
            blocks = list(scan_perms.iter_tool_uses([scan_perms.Path(path)]))
            self.assertEqual(len(blocks), 1)

    def test_message_null_does_not_crash(self):
        # An aborted turn can produce "message": null in a valid-JSON line.
        with tempfile.TemporaryDirectory() as d:
            lines = [
                json.dumps({"type": "assistant", "message": None}),
                _tool_use_line("Bash", {"command": "git status"}),
            ]
            path = _write_transcript(d, "a.jsonl", lines)
            blocks = list(scan_perms.iter_tool_uses([scan_perms.Path(path)]))
            self.assertEqual(len(blocks), 1)

    def test_content_not_a_list_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            lines = [
                json.dumps({"type": "assistant", "message": {"content": "plain text"}}),
                _tool_use_line("Bash", {"command": "git status"}),
            ]
            path = _write_transcript(d, "a.jsonl", lines)
            blocks = list(scan_perms.iter_tool_uses([scan_perms.Path(path)]))
            self.assertEqual(len(blocks), 1)

    def test_empty_line_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            lines = ["", _tool_use_line("Bash", {"command": "git status"})]
            path = _write_transcript(d, "a.jsonl", lines)
            blocks = list(scan_perms.iter_tool_uses([scan_perms.Path(path)]))
            self.assertEqual(len(blocks), 1)

    def test_missing_transcript_file_does_not_crash(self):
        missing = scan_perms.Path("/nonexistent/path/that/does/not/exist.jsonl")
        blocks = list(scan_perms.iter_tool_uses([missing]))
        self.assertEqual(blocks, [])


class MainEndToEndTest(unittest.TestCase):
    """Drive main() against a synthetic transcript dir (never the real
    ~/.claude) and inspect the printed tables."""

    def _run_main_capturing_stdout(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            scan_perms.main()
        return buf.getvalue()

    def test_safe_read_only_pattern_surfaces_in_bash_table(self):
        with tempfile.TemporaryDirectory() as d:
            lines = [_tool_use_line("Bash", {"command": "git status"})] * 3
            _write_transcript(d, "a.jsonl", lines)
            with mock.patch.object(scan_perms, "PROJECTS_DIR", scan_perms.Path(d)):
                output = self._run_main_capturing_stdout()
            self.assertIn("git status", output)
            self.assertIn("    3  git status", output)

    def test_write_capable_pattern_is_reported_as_its_own_distinct_row(self):
        # scan_perms.py itself does not classify safe-vs-unsafe (that
        # judgment happens downstream, in the command that consumes this
        # table) — but a write-capable command like `rm -rf` must never be
        # merged into the same counter bucket as an unrelated read-only
        # command like `git status`. Head+subcommand granularity is what
        # lets the downstream reviewer tell them apart and decline to
        # propose the write pattern for the allowlist.
        with tempfile.TemporaryDirectory() as d:
            lines = (
                [_tool_use_line("Bash", {"command": "git status"})] * 2
                + [_tool_use_line("Bash", {"command": "rm -rf /tmp/scratch"})] * 5
            )
            _write_transcript(d, "a.jsonl", lines)
            with mock.patch.object(scan_perms, "PROJECTS_DIR", scan_perms.Path(d)):
                output = self._run_main_capturing_stdout()
            self.assertIn("    2  git status", output)
            self.assertIn("    5  rm", output)
            self.assertNotIn("git status" + " rm", output)

    def test_malformed_and_empty_transcript_lines_do_not_crash_main(self):
        with tempfile.TemporaryDirectory() as d:
            lines = [
                "",
                "{broken json",
                json.dumps({"type": "assistant", "message": None}),
                json.dumps({"type": "assistant", "message": {"content": "not-a-list"}}),
                _tool_use_line("Bash", {"command": ""}),
                _tool_use_line("Bash", {"command": 123}),
                _tool_use_line("Bash", {}),
                _tool_use_line("Bash", {"command": "git status"}),
            ]
            _write_transcript(d, "a.jsonl", lines)
            with mock.patch.object(scan_perms, "PROJECTS_DIR", scan_perms.Path(d)):
                output = self._run_main_capturing_stdout()
            self.assertIn("git status", output)

    def test_no_transcripts_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            empty = os.path.join(d, "empty")
            os.makedirs(empty)
            with mock.patch.object(scan_perms, "PROJECTS_DIR", scan_perms.Path(empty)):
                with self.assertRaises(SystemExit) as ctx:
                    with contextlib.redirect_stderr(io.StringIO()):
                        scan_perms.main()
            self.assertEqual(ctx.exception.code, 1)

    def test_mcp_tool_use_counted(self):
        with tempfile.TemporaryDirectory() as d:
            lines = [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {"type": "tool_use", "name": "mcp__github__get_me", "input": {}}
                            ]
                        },
                    }
                )
            ] * 4
            _write_transcript(d, "a.jsonl", lines)
            with mock.patch.object(scan_perms, "PROJECTS_DIR", scan_perms.Path(d)):
                output = self._run_main_capturing_stdout()
            self.assertIn("mcp__github__get_me", output)


if __name__ == "__main__":
    unittest.main()
