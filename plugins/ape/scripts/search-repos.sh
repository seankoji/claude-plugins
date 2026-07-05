#!/usr/bin/env bash
# Phase 1 helper for gibbon-scout — runs one or more gh search queries as a
# single preapprovable command instead of a multi-line compound bash block.
#
# Usage: search-repos.sh "<query 1>" ["<query 2>" ...]
set -uo pipefail

for q in "$@"; do
  echo "=== $q ==="
  gh search repos "$q" --limit 15 --json fullName,description,stargazersCount,updatedAt,license,url 2>&1
done
