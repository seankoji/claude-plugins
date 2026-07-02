#!/usr/bin/env python3
"""Scan recent Claude Code transcripts for Bash + MCP tool-call frequencies.

Prints three tables that help build a permissions allowlist:
  1. Top Bash patterns (leading-command + first-subcommand) across recent sessions
  2. Top MCP tool names
  3. SSH-remote subcommand drill (ssh <host> '<cmd> ...' patterns)
  4. sudo-via-ssh drill — what sudo is actually doing remotely

Scans the 50 most-recent .jsonl files across all ~/.claude/projects/ subdirs.
Companion to the /claude-tuneup slash command, which bundles this script and
invokes it automatically via `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scan_perms.py`.
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
SCAN_LIMIT = 50  # most-recent jsonl files
TOP_BASH = 40
TOP_MCP = 30
TOP_SSH = 40
TOP_SUDO = 30

PREFIX_STRIP = re.compile(r"^(?:[A-Z_][A-Z0-9_]*=\S+\s+)+")
LEADERS = {"sudo", "time", "nice", "timeout", "env", "exec", "command"}


def first_real_token(cmd):
    """Walk past leader tokens (sudo/time/nice/timeout/env/exec/command) to find
    the real command + first subcommand, but keep the leaders around so the
    caller can fold them back into the reported pattern. Stripping them
    entirely (as this used to do) produced allowlist rules like
    `Bash(systemctl restart *)` that never match the real `sudo systemctl
    restart ...` invocation that prompted in the first place.

    `timeout`/`env` are walked past but NOT folded into `leaders`: each carries
    a variable argument (duration, VAR=val pairs) that isn't part of the
    invocation's stable shape, so folding just the bare leader word back in
    would reconstruct a pattern (`timeout systemctl restart`) that is not a
    prefix of the real command (`timeout 180 systemctl restart ...`) and would
    never match Claude's exact-prefix permission matcher. Falling through to
    the bare head+sub keeps the rule at least prefix-matching."""
    cmd = PREFIX_STRIP.sub("", cmd.strip())
    parts = cmd.split()
    leaders = []
    if not parts:
        return ([], "", None)
    while parts and parts[0] in LEADERS:
        if parts[0] == "timeout" and len(parts) >= 2:
            parts = parts[2:]
        elif parts[0] == "env":
            parts = parts[1:]
            while parts and "=" in parts[0] and not parts[0].startswith("-"):
                parts = parts[1:]
        else:
            leaders.append(parts[0])
            parts = parts[1:]
    if not parts:
        return (leaders, "", None)
    head = parts[0].rsplit("/", 1)[-1]
    sub = parts[1] if len(parts) > 1 else None
    if sub and (sub.startswith("-") or not re.match(r"^[a-zA-Z0-9_-]+$", sub)):
        sub = None
    return (leaders, head, sub)


def first_segment(cmd):
    return re.split(r"\s*(?:&&|\|\||;|\|)\s*", cmd, maxsplit=1)[0]


def find_recent_transcripts(limit):
    files = []
    if not PROJECTS_DIR.exists():
        return files
    for p in PROJECTS_DIR.rglob("*.jsonl"):
        try:
            files.append((p.stat().st_mtime, p))
        except OSError:
            continue
    files.sort(reverse=True)
    return [p for _, p in files[:limit]]


def iter_tool_uses(transcripts):
    for path in transcripts:
        try:
            with open(path, "r") as f:
                for raw in f:
                    try:
                        msg = json.loads(raw)
                        if msg.get("type") != "assistant":
                            continue
                        content = msg.get("message", {}).get("content")
                        if not isinstance(content, list):
                            continue
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                yield block
                    except (json.JSONDecodeError, AttributeError, TypeError):
                        # A single malformed-but-valid-JSON line (e.g. an
                        # aborted turn with "message": null) shouldn't abort
                        # the scan of every remaining transcript.
                        continue
        except (FileNotFoundError, PermissionError):
            continue


def main():
    transcripts = find_recent_transcripts(SCAN_LIMIT)
    if not transcripts:
        print(f"No transcripts found under {PROJECTS_DIR}", file=sys.stderr)
        sys.exit(1)

    bash_patterns = Counter()
    mcp_calls = Counter()
    ssh_patterns = Counter()
    sudo_patterns = Counter()
    sudo_targets = Counter()

    for block in iter_tool_uses(transcripts):
        name = block.get("name", "")
        inp = block.get("input", {}) or {}
        if name == "Bash":
            cmd = inp.get("command", "")
            if not isinstance(cmd, str) or not cmd.strip():
                continue
            seg = first_segment(cmd.strip()).strip()
            leaders, head, sub = first_real_token(seg)
            if head:
                tokens = leaders + [head] + ([sub] if sub else [])
                bash_patterns[" ".join(tokens)] += 1

            # SSH remote-command drill
            parts = seg.split()
            if parts and parts[0] == "ssh" and len(parts) >= 2:
                host = parts[1]
                if len(parts) >= 3:
                    rf = parts[2]
                    rs = parts[3] if len(parts) >= 4 else None
                    if rf in ("sudo", "/usr/bin/sudo") and rs:
                        ssh_patterns[f"ssh {host} sudo {rs}"] += 1
                    elif rs and re.match(r"^[a-zA-Z0-9_.-]+$", rs) and not rs.startswith("-"):
                        ssh_patterns[f"ssh {host} {rf} {rs}"] += 1
                    else:
                        ssh_patterns[f"ssh {host} {rf}"] += 1

            # sudo-via-ssh drill
            m = re.search(r"""['"]\s*sudo\s+(\S+)(?:\s+(\S+))?""", cmd)
            if m and " ssh " in cmd:
                host_m = re.match(r"ssh\s+(\S+)", cmd.strip())
                host = host_m.group(1) if host_m else "unknown"
                sub_cmd = m.group(1)
                target = m.group(2) or ""
                sudo_patterns[f"{host} sudo {sub_cmd}"] += 1
                if sub_cmd in {"cat", "ls", "grep", "find", "sqlite3", "cp", "rm",
                                "head", "tail", "tee", "docker", "systemctl",
                                "/usr/local/bin/docker"} and target:
                    sudo_targets[f"{sub_cmd} {target}"] += 1
        elif name.startswith("mcp__"):
            mcp_calls[name] += 1

    print(f"=== Scanned {len(transcripts)} transcripts ===\n")

    print(f"=== TOP {TOP_BASH} BASH PATTERNS (leading cmd + first subcmd) ===")
    for pat, count in bash_patterns.most_common(TOP_BASH):
        print(f"{count:5d}  {pat}")

    print(f"\n=== TOP {TOP_MCP} MCP TOOLS ===")
    for name, count in mcp_calls.most_common(TOP_MCP):
        print(f"{count:5d}  {name}")

    print(f"\n=== SSH REMOTE-CMD DRILL (top {TOP_SSH}) ===")
    for pat, count in ssh_patterns.most_common(TOP_SSH):
        print(f"{count:5d}  {pat}")

    print(f"\n=== SUDO-VIA-SSH DRILL (top {TOP_SUDO} commands) ===")
    for pat, count in sudo_patterns.most_common(TOP_SUDO):
        print(f"{count:5d}  ssh {pat}")

    print(f"\n=== SUDO TARGETS (what file/subcmd is being acted on) ===")
    for pat, count in sudo_targets.most_common(40):
        print(f"{count:5d}  {pat}")


if __name__ == "__main__":
    main()
