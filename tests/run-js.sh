#!/usr/bin/env bash
# Runs the node:test suite under tests/js/ (currently just imps-run.workflow.js's
# pure dispatch-reconciliation logic). Separate from tests/run.sh (bash-fixture
# harness for scripts/*.sh) and tests/run-python.sh (Python unittest) — this one is
# Node-only, using the built-in node:test runner, no new dependencies.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

node --test "$ROOT/tests/js/**/*.test.js"
exit $?
