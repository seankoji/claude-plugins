#!/usr/bin/env bash
#
# goldfish-judge.sh — ONE cold "goldfish" comprehension pass over a bootstrap doc.
#
# Runs the judge on a DIFFERENT-LINEAGE model (Gemini via the Antigravity CLI `agy`)
# so it does not share the Claude author's priors — that is what stops the loop from
# certifying its own guesses. The judge runs SANDBOXED with no filesystem access; the
# doc is fed inline. It reads NOTHING but this one document and changes nothing.
#
# This is the COMPREHENSION test (can a cold reader bootstrap the project from this
# doc ALONE?), not the readiness test. It judges sufficiency, not writing quality.
# No-repo-access is deliberate: if the doc omits something, a true zero-context reader
# does not know it either, so giving the judge the code would let the doc cheat its gaps.
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

# Isolation flag for a scriptable, no-filesystem-access judge. The CLI's flags move between
# releases, so it is env-overridable: AGY_READONLY_FLAG. As of agy 1.0.12 the sandboxed,
# non-stalling mode is `--sandbox` (terminal + filesystem restrictions; won't stall on a
# permission prompt). The doc is fed inline in the prompt, so the judge needs NO file access
# at all — sandbox is a feature, not a limitation. Do NOT use `--dangerously-skip-permissions`
# — it grants writes, defeats the cold-read isolation, and trips safety classifiers. If a
# future agy renames the flag, override without editing this file:
#   AGY_READONLY_FLAG='--whatever-the-new-flag-is' bash goldfish-judge.sh elephant.md
AGY_READONLY_FLAG="${AGY_READONLY_FLAG:---sandbox}"
AGY_JUDGE_FLAGS=(--model "$AGY_MODEL" $AGY_READONLY_FLAG)

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

# Cold-read isolation via a virtual workspace. We copy ONLY the doc into a fresh scratch
# dir and run agy sandboxed from inside it, so the judge's workspace contains nothing but
# this one file. By construction it can read the doc but NOT the repo source — if the doc
# omits something, a true zero-context reader does not know it either, so the doc must be
# self-sufficient. Isolating to the scratch dir also means agy does NOT pick up the repo's
# own gemini.md/agents.md (which it reads at workspace root even under --sandbox), so the
# project cannot bias its own judge. We copy rather than symlink: a symlink target outside
# the workspace is unreadable under the sandbox.
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/goldfish.XXXXXX")"
trap 'rm -rf "$SCRATCH"' EXIT
DOC_BASENAME="$(basename "$DOC")"
cp "$DOC" "$SCRATCH/$DOC_BASENAME"

PROMPT="$(cat <<PROMPT_EOF
You are a brand-new engineer with zero prior knowledge of this project. The ONLY file you
can read is "$DOC_BASENAME" in your workspace — the design document. You have NO access to
the source repository or any other file. If the document does not tell you something, you
do not know it — do not assume, infer from convention, or imagine code you cannot see.

Read "$DOC_BASENAME", then decide whether this document ALONE lets a zero-context reader
bootstrap the project — understand what it is and why, how it is architected and how the
components relate, and where in the code each major component lives.

OUTPUT FORMAT — follow exactly:
- The VERY FIRST LINE must be exactly:  VERDICT: READY   or   VERDICT: NOT READY
- READY means that from THIS DOCUMENT ALONE you can (a) explain what the project does and
  why, (b) describe its architecture and how the components relate, and (c) know which file
  or directory owns each component because the document names specific paths. You are judging
  whether the doc TELLS you where to look — you cannot and need not open those paths. Give a
  3-5 sentence summary that demonstrates (a), (b), and (c).
- NOT READY means it failed one of those. List each failure as a numbered, specific gap:
  what you could not determine, the exact section or claim involved, why a cold reader is
  blocked, and what the document must add or correct.
- Judge sufficiency for bootstrapping, not writing quality. Do not propose redesigns. A
  near-universal convention a competent engineer already knows is not a gap. A component
  whose owning path the doc names is navigable even though you cannot open it.
PROMPT_EOF
)"

# Run from inside the scratch dir so agy's workspace root IS the scratch dir (single file).
report="$(cd "$SCRATCH" && pty_run agy "${AGY_JUDGE_FLAGS[@]}" -p "$PROMPT" 2>&1 || true)"

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
