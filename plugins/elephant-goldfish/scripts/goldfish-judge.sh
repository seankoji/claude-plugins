#!/usr/bin/env bash
#
# goldfish-judge.sh — ONE cold "goldfish" comprehension pass over a bootstrap doc.
#
# Runs the judge(s) on DIFFERENT-LINEAGE models so they do not share the Claude
# author's priors — that is what stops the loop from certifying its own guesses.
# The primary judge is Gemini via the Antigravity CLI (`agy`), which is required.
# An optional second opinion is available via `ollama` (set OLLAMA_MODEL).
# Both judges run SANDBOXED with no filesystem access; the doc is fed inline.
# Each reads NOTHING but this one document and changes nothing.
#
# This is the COMPREHENSION test (can a cold reader bootstrap the project from this
# doc ALONE?), not the readiness test. It judges sufficiency, not writing quality.
# No-repo-access is deliberate: if the doc omits something, a true zero-context reader
# does not know it either, so giving the judge the code would let the doc cheat its gaps.
#
# Exit codes:
#   0  = READY         (all run judges agree: READY)
#   10 = NOT READY     (at least one judge says NOT READY; report has the gaps)
#   2  = judge error   (empty output / no VERDICT line / requested CLI missing)
#   1  = usage
#
#   The 0/10/2 contract is preserved for callers regardless of how many judges run.
#   Consensus is fail-closed AND: READY only when every judge that ran says READY.
#   Worst case is always a false NOT-READY, never a false READY.
#
# WHY THE PSEUDO-TTY + MARKER CHECK (agy)
#   agy changes behaviour by whether stdout is a TTY, and under a pipe it can return
#   exit 0 with EMPTY output. In a gate, empty -> "no gaps" -> a false READY, the worst
#   possible failure. So we (a) hand it a pseudo-TTY and (b) refuse to call anything
#   READY unless a literal VERDICT line is present. Worst case is a false NOT-READY.
#
# WHY NO PSEUDO-TTY FOR OLLAMA
#   `ollama run` does not suppress output under a plain pipe, so no PTY is needed.
#   Using one would reintroduce the macOS control-byte artifact described below.
#
# WHY SANITIZE AFTER PTY CAPTURE
#   On macOS, `script -q /dev/null` echoes the TTY's literal control bytes — ^D (0x04)
#   plus two backspaces (0x08 0x08) — onto the first output line. This corrupts the
#   VERDICT anchor. sanitize() strips all control bytes except tab and newline so the
#   grep matches correctly. This also strips stray CRs that either script variant may
#   inject, so it hardens both the Darwin and Linux paths.
#
# VERIFY AGAINST YOUR agy  ──────────────────────────────────────────────────────────
#   The CLI is new and its flags move. Run `agy --help` and confirm the two marked
#   flags below (read-only tool mode, and the model selector). agy's DEFAULT model is
#   already Gemini, so the different-lineage property holds even if --model is wrong or
#   dropped. Do NOT point it at a Claude model — that reintroduces the clone problem.

set -euo pipefail

DOC="${1:-elephant.md}"
REPORT_OUT="${REPORT_OUT:-}"                 # optional: also write the combined report here
AGY_MODEL="${AGY_MODEL:-gemini-3.1-pro}"     # VERIFY: any Gemini model name
OLLAMA_MODEL="${OLLAMA_MODEL:-}"             # optional: any model name accepted by `ollama run`
                                             # honors OLLAMA_HOST for a remote instance
OLLAMA_NO_THINK="${OLLAMA_NO_THINK:-true}"  # prepend /no_think to suppress the <think> block on
                                             # qwen3 and other thinking models so VERDICT: is the
                                             # first output line; set false for non-thinking models
JUDGE_TIMEOUT="${JUDGE_TIMEOUT:-180}"       # seconds; guards agy/ollama against hanging forever
                                             # (auth prompt, network stall, model load)

# Portable timeout wrapper: prefers GNU coreutils `timeout`, falls back to `gtimeout`
# (Homebrew coreutils on macOS). If neither is on PATH, judge calls run unguarded — a warning
# is printed rather than refusing to run, since the fail-closed VERDICT-line check below still
# catches a hang's empty/partial output as ERROR once the process is eventually killed some
# other way; this only loses the bounded-wait guarantee, not the fail-closed correctness.
TIMEOUT_CMD=()
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_CMD=(timeout "$JUDGE_TIMEOUT")
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_CMD=(gtimeout "$JUDGE_TIMEOUT")
else
  echo "goldfish-judge: no 'timeout'/'gtimeout' on PATH — judge calls are NOT time-bounded." >&2
fi

# Isolation flag for a scriptable, no-filesystem-access judge. The CLI's flags move between
# releases, so it is env-overridable: AGY_READONLY_FLAG. As of agy 1.0.12 the sandboxed,
# non-stalling mode is `--sandbox` (terminal + filesystem restrictions; won't stall on a
# permission prompt). The doc is fed inline in the prompt, so the judge needs NO file access
# at all — sandbox is a feature, not a limitation. Do NOT use `--dangerously-skip-permissions`
# — it grants writes, defeats the cold-read isolation, and trips safety classifiers. If a
# future agy renames the flag, override without editing this file:
#   AGY_READONLY_FLAG='--whatever-the-new-flag-is' bash goldfish-judge.sh elephant.md
AGY_READONLY_FLAG="${AGY_READONLY_FLAG:---sandbox}"
# --new-project forces a fresh, empty agy project/conversation for this invocation. Without
# it, agy resolves project/conversation identity from its OWN persistent cross-session cache
# (~/.gemini/antigravity-cli/cache/projects.json etc.), which lives outside the scratch dir
# and is untouched by --sandbox — so a prior agy session tied to an ancestor path (even a
# bare "/tmp" entry) can leak unrelated project context into the verdict.
AGY_JUDGE_FLAGS=(--model "$AGY_MODEL" $AGY_READONLY_FLAG --new-project)

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

# Strip all control bytes except tab (0x09) and newline (0x0A). Removes the ^D + backspace
# prefix that macOS `script` prepends to the first PTY output line, and any stray CRs.
sanitize() { LC_ALL=C tr -d '\000-\010\013-\037\177'; }

# Classify a sanitized report. Echoes one of: READY | NOT_READY | ERROR
classify() {
  local report="$1" verdict
  if [ -z "${report//[[:space:]]/}" ]; then echo ERROR; return; fi
  # Match VERDICT anywhere on its line, not only at line start: macOS `script` sometimes
  # renders the PTY EOT as the literal 2-char text "^D" (0x5E 0x44 — printable, so the
  # control-byte sanitize() cannot strip it) prepended to the verdict line.
  verdict="$(printf '%s\n' "$report" | grep -m1 -iE 'VERDICT:[[:space:]]*(NOT[[:space:]]+)?READY' || true)"
  if [ -z "$verdict" ]; then echo ERROR; return; fi
  if   printf '%s' "$verdict" | grep -qiE 'NOT[[:space:]]+READY'; then echo NOT_READY
  elif printf '%s' "$verdict" | grep -qiE 'READY';                then echo READY
  else echo ERROR; fi
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

# Shared judging criteria used by all judges (format-spec only; no file-access framing).
JUDGE_SPEC="$(cat <<'SPEC_EOF'
Decide whether this document ALONE lets a zero-context reader bootstrap the project —
understand what it is and why, how it is architected and how the components relate,
and where in the code each major component lives.

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
SPEC_EOF
)"

# agy prompt — workspace-file framing (agy can read the copied file; sandbox is enforced).
AGY_PROMPT="$(cat <<PROMPT_EOF
You are a brand-new engineer with zero prior knowledge of this project. The ONLY file you
can read is "$DOC_BASENAME" in your workspace — the design document. You have NO access to
the source repository or any other file. If the document does not tell you something, you
do not know it — do not assume, infer from convention, or imagine code you cannot see.

Read "$DOC_BASENAME", then $JUDGE_SPEC
PROMPT_EOF
)"

# ollama prompt — doc inlined (ollama reads no files; inlining is stronger cold isolation).
# /no_think prefix tells qwen3 and compatible models to skip their <think> block so the very
# first output token is VERDICT:, matching the format spec. Ignored by non-thinking models.
DOC_CONTENTS="$(cat "$DOC")"
_OLLAMA_NOTHINK_PREFIX=""
[ "${OLLAMA_NO_THINK}" = "true" ] && _OLLAMA_NOTHINK_PREFIX="/no_think
"
OLLAMA_PROMPT="$(cat <<PROMPT_EOF
${_OLLAMA_NOTHINK_PREFIX}You are a brand-new engineer with zero prior knowledge of this project. The ONLY information
you have is the design document pasted below. You have NO access to the source repository or
any other file. If the document does not tell you something, you do not know it — do not
assume, infer from convention, or imagine code you cannot see.

$JUDGE_SPEC

----- BEGIN DOCUMENT -----
$DOC_CONTENTS
----- END DOCUMENT -----
PROMPT_EOF
)"

# ── Judge 1: agy / Gemini (pseudo-TTY required; sanitize fixes the macOS PTY artifact) ──
agy_raw="$(cd "$SCRATCH" && pty_run "${TIMEOUT_CMD[@]+"${TIMEOUT_CMD[@]}"}" agy "${AGY_JUDGE_FLAGS[@]}" -p "$AGY_PROMPT" 2>&1 | sanitize || true)"
agy_class="$(classify "$agy_raw")"
[ "$agy_class" = ERROR ] && echo "goldfish-judge: agy judge produced no usable verdict. NOT a pass." >&2

combined="===== JUDGE 1: agy / $AGY_MODEL =====
$agy_raw"

# ── Judge 2: ollama (optional, sequential — no PTY, stderr quarantined) ─────────────────
ollama_class="READY"   # neutral default when Ollama is disabled; doesn't affect consensus
if [ -n "$OLLAMA_MODEL" ]; then
  if command -v ollama >/dev/null 2>&1; then
    ollama_raw="$("${TIMEOUT_CMD[@]+"${TIMEOUT_CMD[@]}"}" ollama run "$OLLAMA_MODEL" "$OLLAMA_PROMPT" 2>"$SCRATCH/ollama.err" | sanitize || true)"
    ollama_class="$(classify "$ollama_raw")"
    if [ "$ollama_class" = ERROR ]; then
      echo "goldfish-judge: ollama judge produced no usable verdict. NOT a pass." >&2
      tail -5 "$SCRATCH/ollama.err" >&2
    fi
    combined="$combined

===== JUDGE 2: ollama / $OLLAMA_MODEL =====
$ollama_raw"
  else
    echo "goldfish-judge: OLLAMA_MODEL set but 'ollama' not on PATH. NOT a pass." >&2
    ollama_class="ERROR"
  fi
fi

[ -n "$REPORT_OUT" ] && printf '%s\n' "$combined" > "$REPORT_OUT"
printf '%s\n' "$combined"

# ── Fail-closed AND consensus ─────────────────────────────────────────────────────────────
if [ "$agy_class" = ERROR ] || [ "$ollama_class" = ERROR ]; then
  echo "goldfish-judge: a judge errored / produced no verdict. NOT a pass. See report." >&2
  exit 2
fi
if [ "$agy_class" = NOT_READY ] || [ "$ollama_class" = NOT_READY ]; then exit 10; fi
exit 0
