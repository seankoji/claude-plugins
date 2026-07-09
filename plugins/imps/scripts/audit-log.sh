#!/usr/bin/env bash
# audit-log.sh — append one structured entry to the shared cross-plugin audit log.
#
# Every self-improvement / reflection command in this marketplace (imps, prompt-builder,
# claude-tuneup, ...) calls this once per run so entries land in one queryable,
# append-only JSONL file instead of each plugin growing its own differently-shaped
# free-text log. Schema mirrors the {command, duration_ms, cost_estimate_usd,
# exit_status} shape from maestro's audit.jsonl (github.com/sharpdeveye/maestro).
#
# This file is bundled identically into every plugin's scripts/ dir — plugins in this
# marketplace are installed independently, so there is no cross-plugin runtime path to
# require a shared lib from (see AGENTS.md). Keep the copies byte-identical;
# tests/run.sh diffs them against each other.
#
# Usage:
#   audit-log.sh --plugin <name> --command <slash-command> --exit-status <status> \
#     --duration-ms <int> [--notes <text>] [--cost-usd <number>] [--scope <user|project>]
#
#   status: completed | partial | failed | cancelled
#   scope, if omitted, is auto-detected: "project" inside a git repo, else "user"
#
# Best-effort by design: a missing `jq`, an unwritable log dir, or a write failure warns
# on stderr and exits 0 rather than breaking the caller's primary command — this is
# telemetry, not a gate. Malformed arguments (bad enum, non-numeric duration) exit 1,
# since those are bugs in the calling command, not the environment.
set -uo pipefail

AUDIT_FILE="${AUDIT_LOG_FILE:-$HOME/.claude/audit.jsonl}"

plugin="" command="" exit_status="" duration_ms="" notes="" cost_usd="" scope=""

while [ $# -gt 0 ]; do
  case "$1" in
    --plugin) plugin="${2:-}"; shift 2 ;;
    --command) command="${2:-}"; shift 2 ;;
    --exit-status) exit_status="${2:-}"; shift 2 ;;
    --duration-ms) duration_ms="${2:-}"; shift 2 ;;
    --notes) notes="${2:-}"; shift 2 ;;
    --cost-usd) cost_usd="${2:-}"; shift 2 ;;
    --scope) scope="${2:-}"; shift 2 ;;
    *) echo "audit-log: unknown argument: $1" >&2; exit 1 ;;
  esac
done

case "$exit_status" in
  completed|partial|failed|cancelled) ;;
  *) echo "audit-log: --exit-status must be one of completed|partial|failed|cancelled, got '$exit_status'" >&2; exit 1 ;;
esac

case "$duration_ms" in
  ''|*[!0-9]*) echo "audit-log: --duration-ms must be a non-negative integer, got '$duration_ms'" >&2; exit 1 ;;
esac

[ -n "$plugin" ] || { echo "audit-log: --plugin is required" >&2; exit 1; }
[ -n "$command" ] || { echo "audit-log: --command is required" >&2; exit 1; }

if [ -n "$scope" ]; then
  case "$scope" in
    user|project) ;;
    *) echo "audit-log: --scope must be user or project, got '$scope'" >&2; exit 1 ;;
  esac
else
  if git rev-parse --show-toplevel >/dev/null 2>&1; then scope="project"; else scope="user"; fi
fi

project="null"
if [ "$scope" = "project" ]; then
  toplevel="$(git rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$toplevel" ] && project="\"$(basename "$toplevel")\""
fi

cost_json="null"
if [ -n "$cost_usd" ]; then
  case "$cost_usd" in
    ''|*[!0-9.]*) echo "audit-log: --cost-usd must be numeric, got '$cost_usd' — logging as null" >&2 ;;
    *) cost_json="$cost_usd" ;;
  esac
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "audit-log: 'jq' not on PATH — skipping structured log entry" >&2
  exit 0
fi

if ! mkdir -p "$(dirname "$AUDIT_FILE")" 2>/dev/null; then
  echo "audit-log: cannot create $(dirname "$AUDIT_FILE") — skipping structured log entry" >&2
  exit 0
fi

id="a-$(od -An -N4 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n')"
[ -n "$id" ] || id="a-$$${RANDOM:-0}"
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if ! jq -nc \
  --arg id "$id" \
  --arg ts "$ts" \
  --arg plugin "$plugin" \
  --arg command "$command" \
  --arg scope "$scope" \
  --argjson project "$project" \
  --arg exit_status "$exit_status" \
  --argjson duration_ms "$duration_ms" \
  --argjson cost_estimate_usd "$cost_json" \
  --arg notes "${notes:0:200}" \
  '{id:$id, ts:$ts, plugin:$plugin, command:$command, scope:$scope, project:$project,
    exit_status:$exit_status, duration_ms:$duration_ms, cost_estimate_usd:$cost_estimate_usd,
    notes:$notes}' >> "$AUDIT_FILE" 2>/dev/null; then
  echo "audit-log: failed to write to $AUDIT_FILE" >&2
  exit 0
fi
