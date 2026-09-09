"""Offline evaluation uses actual outputs; malformed or missing evidence never passes."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "plugins/prompt-builder/scripts/evaluate.py"
spec = importlib.util.spec_from_file_location("prompt_evaluate", SCRIPT)
evaluator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluator)


class EvaluationTest(unittest.TestCase):
    def setUp(self):
        self.suite = {"cases": [{"id": kind, "kind": kind, "input": f"fixture {kind}",
                                "checks": [{"equals": "OK"}]} for kind in ("normal", "edge", "adversarial")]}
        self.baseline = {"prompt": "Return OK.", "model": "fixture-model", "settings": {},
                         "inputs": {c["id"]: c["input"] for c in self.suite["cases"]},
                         "outputs": {c["id"]: "OK" for c in self.suite["cases"]}}
        self.candidate = copy.deepcopy(self.baseline)
        self.candidate["prompt"] = "A much longer prompt that also returns OK."

    def evaluate(self):
        return evaluator.evaluate(self.suite, self.baseline, self.candidate)

    def test_tie_keeps_shorter_baseline(self):
        self.assertEqual(self.evaluate()["winner"], "baseline")

    def test_candidate_must_fix_failure_without_regression(self):
        self.baseline["outputs"]["edge"] = "wrong"
        self.assertEqual(self.evaluate()["winner"], "candidate")
        self.candidate["outputs"]["adversarial"] = "wrong"
        report = self.evaluate()
        self.assertEqual(report["winner"], "neither")
        self.assertEqual(report["cases"][-1]["candidate"]["failed_checks"], [1])

    def test_shorter_passing_candidate_wins(self):
        self.candidate["prompt"] = "OK"
        self.assertEqual(self.evaluate()["winner"], "candidate")

    def test_missing_extra_and_non_string_outputs_rejected(self):
        for outputs in ({}, {"extra": "OK", **self.baseline["outputs"]},
                        {**self.baseline["outputs"], "edge": None}):
            with self.subTest(outputs=outputs), self.assertRaises(ValueError):
                self.candidate["outputs"] = outputs
                self.evaluate()

    def test_mismatched_model_or_settings_rejected(self):
        for key, value in (("model", "other-model"), ("settings", {"temperature": 1})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.candidate = {**self.baseline, key: value}
                self.evaluate()

    def test_outputs_from_changed_inputs_are_not_comparable(self):
        self.candidate["inputs"]["edge"] = "a different case"
        with self.assertRaisesRegex(ValueError, "inputs must match"):
            self.evaluate()

    def test_empty_checks_duplicate_ids_and_unknown_predicates_rejected(self):
        for change in ({"checks": []}, {"id": "normal"}, {"checks": [{"typo": "OK"}]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                suite = copy.deepcopy(self.suite)
                suite["cases"][-1].update(change)
                evaluator.evaluate(suite, self.baseline, self.candidate)

    def test_predicates(self):
        self.assertTrue(evaluator.check_output(' {"b": 2, "a": 1} ', {"json_equals": {"a": 1, "b": 2}}))
        self.assertFalse(evaluator.check_output("true", {"json_equals": 1}))
        self.assertFalse(evaluator.check_output("not JSON", {"json_equals": {}}))
        self.assertTrue(evaluator.check_output("hello world", {"contains": "world"}))
        self.assertFalse(evaluator.check_output("secret", {"not_contains": "secret"}))

    def test_cli_reports_hashes_and_nonzero_for_failed_suite(self):
        self.candidate["outputs"]["edge"] = "bad"
        self.baseline["outputs"]["edge"] = "bad"
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f"{name}.json" for name in ("suite", "baseline", "candidate")]
            for path, data in zip(paths, (self.suite, self.baseline, self.candidate)):
                path.write_text(json.dumps(data))
            result = subprocess.run([sys.executable, str(SCRIPT), *map(str, paths)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(set(json.loads(result.stdout)["sha256"]), {"suite", "baseline", "candidate"})


if __name__ == "__main__":
    unittest.main()
