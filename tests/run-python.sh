#!/usr/bin/env bash
# Runs the stdlib-unittest Python suite under tests/python/ (currently just
# ollama-sidecar's pure-function coverage). Separate from tests/run.sh,
# which drives the bash-fixture harness for scripts/*.sh — this one is
# Python-only, no pytest, no new dependencies. Exits non-zero on any
# failure so it composes into CI the same way tests/run.sh does.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 -m unittest discover -s "$ROOT/tests/python" -v
exit $?
