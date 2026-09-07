"""Exercise the actual merge helper with isolated GitHub transport stubs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "plugins/babysitter/scripts/merge-pr.sh"
HEAD = "a" * 40


@unittest.skipUnless(shutil.which("jq"), "jq required by the shipped helper")
class MergeTest(unittest.TestCase):
    def run_helper(self, mode, extra_args=()):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, body in {
                "gh": "#!/bin/sh\nprintf fixture-token",
                "curl": '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
a = sys.argv[1:]
mode = os.environ["MERGE_FIXTURE_MODE"]
body = json.loads(a[a.index("--data")+1]) if "--data" in a else {}
with open(os.environ["MERGE_FIXTURE_LOG"], "a") as log:
    log.write(json.dumps(body)+"\\n")
query = body.get("query", "")
code = "200"
if "node(id:" in query:
    payload = {"data":{"node":{"id":"T1", "pullRequest":{"id":"OTHER" if mode=="foreign" else "PR1", "headRefOid":"a"*40}}}}
elif query.startswith("query"):
    threads = []
    if mode == "marker":
        threads = [{"id":"T1", "isResolved":False, "comments":{"nodes":[{"body":"[babysitter] fixed"}]}}]
    pr = {"id":"PR1", "state":"OPEN", "headRefOid":"a"*40, "mergeable":"MERGEABLE",
          "mergeStateStatus":"CLEAN", "autoMergeRequest":None,
          "reviewThreads":{"nodes":threads,"pageInfo":{"hasNextPage":mode=="truncated"}},
          "commits":{"nodes":[{"commit":{"statusCheckRollup":{"state":"SUCCESS"}}}]}}
    if mode == "null": pr = None
    if mode == "failing": pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["state"] = "FAILURE"
    if mode == "already-auto": pr["autoMergeRequest"] = {"enabledAt":"fixture-time"}
    payload = {"data":{"repository":{"pullRequest":pr}}}
elif "resolveReviewThread" in query:
    payload = {"data":{"resolveReviewThread":{"thread":{"id":"T1", "isResolved":True}}}}
elif "enablePullRequestAutoMerge" in query:
    payload = {"data":{"enablePullRequestAutoMerge":{"pullRequest":{"autoMergeRequest":{"enabledAt":"fixture-time"}}}}}
elif query:
    payload = {"errors":[{"message":"unexpected mutation"}]}
else:
    if mode == "transport": sys.exit(7)
    code = {"race":"409", "unexpected":"500", "method":"405"}.get(mode, "200")
    if mode == "method" and body.get("merge_method") == "merge": code = "200"
    payload = {"merged": code == "200" and mode != "false-success"}
Path(a[a.index("-o")+1]).write_text(json.dumps(payload))
print(code, end="")
''',
            }.items():
                path = root / name
                path.write_text(body)
                path.chmod(0o755)
            log = root / "calls.jsonl"
            env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                   "MERGE_FIXTURE_MODE": mode, "MERGE_FIXTURE_LOG": str(log)}
            result = subprocess.run(["bash", str(SCRIPT), "--repo", "fixture/repo", "--pr", "1", *extra_args],
                                    env=env, capture_output=True, text=True, timeout=20)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            return result, calls

    def test_success_pins_head_and_requires_merged_true(self):
        result, calls = self.run_helper("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MERGED", result.stdout)
        self.assertEqual(calls[-1], {"merge_method": "squash", "sha": HEAD})

    def test_reply_marker_cannot_resolve_thread_or_arm_merge(self):
        result, calls = self.run_helper("marker")
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn("reason=unanswered_threads", result.stdout)
        self.assertEqual(len(calls), 1)

    def test_incomplete_snapshot_cannot_merge(self):
        for mode, code in (("truncated", 4), ("null", 3)):
            with self.subTest(mode=mode):
                result, calls = self.run_helper(mode)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(len(calls), 1)

    def test_changed_head_never_retries_or_arms_automerge(self):
        result, calls = self.run_helper("race")
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn("reason=head_changed", result.stdout)
        self.assertEqual(len(calls), 2)

    def test_unknown_outcomes_stop_without_retry(self):
        for mode in ("transport", "unexpected", "false-success"):
            with self.subTest(mode=mode):
                result, calls = self.run_helper(mode)
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertNotIn("MERGED", result.stdout)
                self.assertEqual(len(calls), 2)

    def test_method_fallback_preserves_checked_head(self):
        result, calls = self.run_helper("method")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c["sha"] for c in calls[1:]], [HEAD, HEAD])

    def test_automerge_checks_head_when_armed(self):
        result, calls = self.run_helper("failing")
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn("automerge=armed", result.stdout)
        self.assertIn("expectedHeadOid:$sha", calls[-1]["query"])
        self.assertEqual(calls[-1]["variables"]["sha"], HEAD)

    def test_no_auto_prevents_arming_and_rejects_existing_request(self):
        for mode in ("failing", "already-auto"):
            with self.subTest(mode=mode):
                result, calls = self.run_helper(mode, ["--no-auto"])
                self.assertEqual(result.returncode, 4, result.stderr)
                self.assertEqual(len(calls), 1)

    def test_explicit_resolution_checks_owner_and_head_without_merging(self):
        args = ["--resolve-thread", "T1", "--verified-head", HEAD]
        result, calls = self.run_helper("resolve", args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESOLVED", result.stdout)
        self.assertEqual(len(calls), 3)
        self.assertIn("resolveReviewThread", calls[-1]["query"])
        result, calls = self.run_helper("foreign", args)
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertEqual(len(calls), 2)
        result, calls = self.run_helper("resolve", ["--resolve-thread", "T1", "--verified-head", "b" * 40])
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
