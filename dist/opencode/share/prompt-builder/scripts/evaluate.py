#!/usr/bin/env python3
"""Compare saved baseline/candidate outputs against a frozen, local JSON suite.

Usage: evaluate.py suite.json baseline.json candidate.json
No model calls, shell execution, or writes. Exit 0: a usable prompt; 1: neither
passes every case; 2: invalid or incomparable evidence. See the plugin README.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_output(output, check):
    require(isinstance(check, dict) and len(check) == 1, "each check needs one predicate")
    kind, expected = next(iter(check.items()))
    require(kind in {"equals", "contains", "not_contains", "json_equals"},
            f"unknown predicate: {kind}")
    if kind == "json_equals":
        try:
            # Compare canonical JSON, preserving the distinction between true and 1.
            return json.dumps(json.loads(output), sort_keys=True) == json.dumps(expected, sort_keys=True)
        except (ValueError, TypeError):
            return False
    require(isinstance(expected, str), f"{kind} expects a string")
    if kind == "equals":
        return output == expected
    require(bool(expected), f"{kind} expects a nonempty string")
    return (expected in output) if kind == "contains" else (expected not in output)


def evaluate(suite, baseline, candidate):
    require(isinstance(suite, dict) and isinstance(suite.get("cases"), list),
            "suite needs a cases array")
    cases = suite["cases"]
    require(len(cases) >= 3, "include at least normal, edge, and adversarial cases")
    ids, kinds = [], set()
    for case in cases:
        require(isinstance(case, dict), "each case must be an object")
        case_id = case.get("id")
        require(isinstance(case_id, str) and bool(case_id) and case_id not in ids,
                "case ids must be nonempty and unique")
        require(case.get("kind") in {"normal", "edge", "adversarial"}, "invalid case kind")
        require(isinstance(case.get("input"), str), f"input for {case_id} must be a string")
        require(isinstance(case.get("checks"), list) and bool(case["checks"]),
                f"missing checks for {case_id}")
        ids.append(case_id)
        kinds.add(case["kind"])
    require(kinds == {"normal", "edge", "adversarial"}, "include all three case kinds")
    for run in (baseline, candidate):
        require(isinstance(run, dict), "each run must be an object")
        for field in ("prompt", "model"):
            require(isinstance(run.get(field), str) and bool(run[field].strip()),
                    f"run needs a nonempty {field}")
        require(isinstance(run.get("settings"), dict), "record run settings ({} if defaults)")
        require(run.get("inputs") == {case["id"]: case["input"] for case in cases},
                "run inputs must match the frozen suite exactly")
        require(isinstance(run.get("outputs"), dict) and set(run["outputs"]) == set(ids),
                "outputs must match the case ids exactly")
        require(all(isinstance(v, str) for v in run["outputs"].values()),
                "outputs must be strings copied from actual runs")
    require(baseline["model"] == candidate["model"] and baseline["settings"] == candidate["settings"],
            "baseline and candidate must use the same model and settings")

    rows = []
    for case in cases:
        row = {"id": case["id"]}
        for label, run in (("baseline", baseline), ("candidate", candidate)):
            results = [check_output(run["outputs"][case["id"]], c) for c in case["checks"]]
            row[label] = {"passed": all(results), "failed_checks": [i + 1 for i, ok in enumerate(results) if not ok]}
        rows.append(row)
    passed = {label: sum(row[label]["passed"] for row in rows) for label in ("baseline", "candidate")}
    sizes = {label: len(run["prompt"].encode("utf-8")) for label, run in (("baseline", baseline), ("candidate", candidate))}
    winner = "neither"
    if passed["baseline"] == len(cases):
        winner = "baseline"
    if passed["candidate"] == len(cases) and (winner == "neither" or sizes["candidate"] < sizes["baseline"]):
        winner = "candidate"
    return {"winner": winner, "passed": passed, "total": len(cases), "prompt_bytes": sizes,
            "cases": rows, "scope": "supplied outputs on this suite; not proof of model execution or general quality"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", type=Path)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    try:
        evidence = {name: getattr(args, name).read_bytes() for name in ("suite", "baseline", "candidate")}
        report = evaluate(*(json.loads(evidence[name]) for name in ("suite", "baseline", "candidate")))
        report["sha256"] = {name: hashlib.sha256(data).hexdigest() for name, data in evidence.items()}
    except (OSError, ValueError, TypeError) as exc:
        print(f"evaluate.py: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 1 if report["winner"] == "neither" else 0


if __name__ == "__main__":
    sys.exit(main())
