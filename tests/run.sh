#!/usr/bin/env bash
# Behavioral test harness for plugins/*/scripts/*.sh — runs fixtures against
# the real scripts and diffs actual output against golden files. Static
# manifest/schema checks live in .github/workflows/validate.yml; this covers
# what those can't: does the script actually do the right thing when run.
#
# Two fixture kinds, each a leaf directory under tests/fixtures/:
#
#   exec/<plugin>/<script>/<case>/
#     Runs the real script end-to-end with external commands (gh, git)
#     replaced by tests/lib/stubs/* on PATH, from a fresh empty $PWD.
#       args             one CLI arg per line (optional)
#       stdout           exact expected stdout (optional)
#       stdout.contains  one grep -E pattern per line, each must match
#                        somewhere in actual stdout — use instead of `stdout`
#                        when the real output has non-deterministic parts
#                        (e.g. `ls -la` timestamps) (optional)
#       stderr           exact expected stderr (optional)
#       exit_code        expected exit code, default 0 (optional)
#
#   unit/<plugin>/<script>/<function>/<case>/
#     Sources the script with __SOURCED__=1 (see the guard comment in
#     goldfish-judge.sh — this stops execution before the script's "do the
#     thing" tail) and calls one function directly.
#       arg      passed as "$1" to the function (mutually exclusive w/ stdin)
#       stdin    piped to the function's stdin (mutually exclusive w/ arg)
#       expected exact expected stdout
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STUBS="$ROOT/tests/lib/stubs"
pass=0
fail=0

report() {
  local name="$1" ok="$2" detail="${3:-}"
  if [ "$ok" = 1 ]; then
    echo "ok   $name"
    pass=$((pass + 1))
  else
    echo "FAIL $name"
    [ -n "$detail" ] && printf '%s\n' "$detail"
    fail=$((fail + 1))
  fi
}

run_exec_case() {
  local case_dir="$1" rel plugin script target name
  rel="${case_dir#"$ROOT"/tests/fixtures/exec/}"
  IFS=/ read -r plugin script _ <<<"$rel"
  target="$ROOT/plugins/$plugin/scripts/$script"
  name="exec/$rel"

  local args=()
  [ -f "$case_dir/args" ] && mapfile -t args <"$case_dir/args"

  local test_home out err exit_code ok=1 detail=""
  test_home="$(mktemp -d)"
  out="$(mktemp)"
  err="$(mktemp)"
  # HOME is pinned to the disposable test_home so any script that defaults to a
  # $HOME/... path (e.g. audit-log.sh's ~/.claude/audit.jsonl) can't touch the real
  # user's home directory during a test run.
  ( cd "$test_home" && HOME="$test_home" PATH="$STUBS:$PATH" bash "$target" "${args[@]+"${args[@]}"}" >"$out" 2>"$err" )
  exit_code=$?

  local want_exit=0
  [ -f "$case_dir/exit_code" ] && want_exit="$(cat "$case_dir/exit_code")"
  [ "$exit_code" = "$want_exit" ] || { ok=0; detail="$detail
exit code: want $want_exit, got $exit_code"; }

  if [ -f "$case_dir/stdout" ]; then
    diff -u "$case_dir/stdout" "$out" >/tmp/ape-test-diff.$$ 2>&1 || { ok=0; detail="$detail
$(cat /tmp/ape-test-diff.$$)"; }
  elif [ -f "$case_dir/stdout.contains" ]; then
    while IFS= read -r pattern; do
      [ -z "$pattern" ] && continue
      grep -qE "$pattern" "$out" || { ok=0; detail="$detail
missing pattern in stdout: $pattern"; }
    done <"$case_dir/stdout.contains"
  fi

  if [ -f "$case_dir/stderr" ]; then
    diff -u "$case_dir/stderr" "$err" >/tmp/ape-test-diff.$$ 2>&1 || { ok=0; detail="$detail
$(cat /tmp/ape-test-diff.$$)"; }
  fi

  report "$name" "$ok" "$detail"
  rm -rf "$test_home" "$out" "$err" /tmp/ape-test-diff.$$
}

run_unit_case() {
  local case_dir="$1" rel plugin script func target name
  rel="${case_dir#"$ROOT"/tests/fixtures/unit/}"
  IFS=/ read -r plugin script func _ <<<"$rel"
  target="$ROOT/plugins/$plugin/scripts/$script"
  name="unit/$rel"

  local actual expected ok=1 detail=""
  if [ -f "$case_dir/arg" ]; then
    actual="$( (__SOURCED__=1; source "$target"; "$func" "$(cat "$case_dir/arg")") 2>&1 )"
  elif [ -f "$case_dir/stdin" ]; then
    actual="$( (__SOURCED__=1; source "$target"; "$func") <"$case_dir/stdin" 2>&1 )"
  else
    report "$name" 0 "no arg or stdin fixture"
    return
  fi

  expected="$(cat "$case_dir/expected" 2>/dev/null || true)"
  [ "$actual" = "$expected" ] || { ok=0; detail="want: $expected
got:  $actual"; }
  report "$name" "$ok" "$detail"
}

shopt -s globstar nullglob
for case_dir in "$ROOT"/tests/fixtures/exec/**/; do
  case_dir="${case_dir%/}"
  [ -f "$case_dir/args" ] || [ -f "$case_dir/stdout" ] || [ -f "$case_dir/stdout.contains" ] || [ -f "$case_dir/exit_code" ] || continue
  run_exec_case "$case_dir"
done
for case_dir in "$ROOT"/tests/fixtures/unit/**/; do
  case_dir="${case_dir%/}"
  [ -f "$case_dir/arg" ] || [ -f "$case_dir/stdin" ] || continue
  run_unit_case "$case_dir"
done

# Cross-plugin consistency: audit-log.sh is bundled identically into every plugin that
# uses it (no shared runtime path exists between independently-installed plugins — see
# AGENTS.md). Diff the copies so a future edit to one doesn't silently drift from the
# rest. Discovered dynamically so a new adopter is automatically covered.
audit_log_copies=("$ROOT"/plugins/*/scripts/audit-log.sh)
if [ -f "${audit_log_copies[0]:-}" ]; then
  first="${audit_log_copies[0]}"
  consistent=1 detail=""
  for other in "${audit_log_copies[@]:1}"; do
    if ! diff -q "$first" "$other" >/dev/null 2>&1; then
      consistent=0
      detail="$detail
${other#"$ROOT"/} differs from ${first#"$ROOT"/}"
    fi
  done
  report "consistency/audit-log.sh" "$consistent" "$detail"
fi

echo "---"
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
