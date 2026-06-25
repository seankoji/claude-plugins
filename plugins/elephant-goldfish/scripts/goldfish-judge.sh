#!/usr/bin/env bash
#
# goldfish-judge.sh — ONE cold "goldfish" comprehension pass over a bootstrap doc.
#
# Runs the judge on a DIFFERENT-LINEAGE model (Gemini via the Antigravity CLI `agy`)
# so it does not share the Claude author's priors — that is what stops the loop from
# certifying its own guesses. Read-only: it reads the doc and the files it references
# and changes nothing.
#
# This is the COMPREHENSION test (can a cold reader bootstrap the project from this
# doc + its refs?), not the readiness test. It judges sufficiency, not writing quality.
#
# Exit codes:
#   0  = READY
#   10 = NOT READY   (report contains the gaps; feed it to /elephant FEEDBACK)
#   2  = judge error / empty output / no VERDICT line  (FAIL-CLOSED — never a pass)
#   1  = usage
#
# WHY THE PSEUDO-TTY + MARKER CHECK
#   agy changes behaviour by whether stdout is a TTY, and under a pipe it can return
#   exit 0 with EMPTY output. In a gate, empty -> "no gaps" -> a false READY, the worst
#   possible failure. So we (a) hand it a pseudo-TTY and (b) refuse to call anything
#   READY unless a literal VERDICT line is present. Worst case is a false NOT-READY.
#
# VERIFY AGAINST YOUR agy  ──────────────────────────────────────────────────────────
#   The CLI is new and its flags move. Run `agy --help` and confirm the two marked
#   flags below (read-only tool mode, and the model selector). agy's DEFAULT model is
#   already Gemini, so the different-lineage property holds even if --model is wrong or
#   dropped. Do NOT point it at a Claude model — that reintroduces the clone problem.

set -euo pipefail

DOC="${1:-elephant.md}"
REPORT_OUT="${REPORT_OUT:-}"                 # optional: also write the report here
AGY_MODEL="${AGY_MODEL:-gemini-3.1-pro}"     # VERIFY: any Gemini model name

# agy flags for a read-only, scriptable judge. VERIFY the read-only flag spelling.
# Read-only / zero-trust = it may use read tools (follow path:line refs) but cannot
# modify the repo and will not stall on a write-permission prompt.
AGY_JUDGE_FLAGS=(--model "$AGY_MODEL" --tool-permission read-only)

[ -f "$DOC" ] || { echo "goldfish-judge: doc not found: $DOC" >&2; exit 1; }
command -v agy >/dev/null 2>&1 || { echo "goldfish-judge: 'agy' not on PATH" >&2; exit 2; }

# Portable pseudo-TTY (BSD `script` and GNU `script` take different argument orders).
pty_run() {
  if [ "$(uname)" = "Darwin" ]; then
    script -q /dev/null "$@"
  else
    script -qec "$(printf '%q ' "$@")" /dev/null
  fi
}

PROMPT="$(cat <<PROMPT_EOF
You are a brand-new engineer with zero prior knowledge of this project. The only
things you may read are the design document at "$DOC" and any source files it
references; you have READ-ONLY access to this repository and must change nothing.
Assume nothing that is not grounded in what you actually read.

Your job: decide whether this document ALONE lets a zero-context reader bootstrap the
project — understand what it is, how it is architected, and where in the code each
major component lives. Read "$DOC", then open the files it references to verify its
claims against the real code.

OUTPUT FORMAT — follow exactly:
- The VERY FIRST LINE must be exactly:  VERDICT: READY   or   VERDICT: NOT READY
- READY means that from this document and its referenced files alone you can (a)
  explain what the project does and why, (b) describe its architecture and how the
  components relate, and (c) navigate to the code that owns each component. Give a
  3-5 sentence summary that demonstrates this.
- NOT READY means it failed one of those. List each failure as a numbered, specific
  gap: what you could not determine or verify, the exact section or claim involved,
  why a cold reader is blocked, and what the document must add or correct. Where a
  doc claim contradicts the code you read, cite path:line.
- Judge sufficiency for bootstrapping, not writing quality. Do not propose redesigns.
  A near-universal convention a competent engineer already knows is not a gap.
PROMPT_EOF
)"

report="$(pty_run agy "${AGY_JUDGE_FLAGS[@]}" -p "$PROMPT" 2>&1 || true)"

# ── Fail-closed guards ───────────────────────────────────────────────────────────
if [ -z "${report//[[:space:]]/}" ]; then
  echo "goldfish-judge: agy produced empty output (non-TTY drop, auth, or quota). NOT a pass." >&2
  exit 2
fi

verdict="$(printf '%s\n' "$report" | grep -m1 -E '^[[:space:]]*VERDICT:' || true)"
[ -n "$REPORT_OUT" ] && printf '%s\n' "$report" > "$REPORT_OUT"

if [ -z "$verdict" ]; then
  echo "goldfish-judge: no VERDICT line in agy output. NOT a pass. See report." >&2
  exit 2
fi

printf '%s\n' "$report"   # full report to stdout for the caller / log

if   printf '%s' "$verdict" | grep -qiE 'NOT[[:space:]]+READY'; then exit 10
elif printf '%s' "$verdict" | grep -qiE 'READY';                then exit 0
else echo "goldfish-judge: unrecognized verdict: $verdict" >&2;      exit 2
fi
