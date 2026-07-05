#!/usr/bin/env bash
# Gate-phase helper for /ape:forage — clones the selected candidates in the
# background and reports the tail of a log, as a single preapprovable command.
#
# Usage: clone-candidates.sh <workspace-dir> <url> <name> <sparse:0|1> [<url> <name> <sparse:0|1> ...]
set -uo pipefail

workspace="$1"; shift

mkdir -p "$workspace/repos"
log="$workspace/repos/clone.log"
: > "$log"

while [ "$#" -ge 3 ]; do
  url="$1"; name="$2"; sparse="$3"; shift 3
  if [ "$sparse" = "1" ]; then
    git clone --depth 1 --filter=blob:none --sparse "$url" "$workspace/repos/$name" >> "$log" 2>&1 &
  else
    git clone --depth 1 --filter=blob:none "$url" "$workspace/repos/$name" >> "$log" 2>&1 &
  fi
done

wait
echo "--- clone.log (tail) ---"
tail -n 40 "$log"
