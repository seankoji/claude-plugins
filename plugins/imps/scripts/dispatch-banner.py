#!/usr/bin/env python3
"""Dispatch banner for /imps:imps — reads the run state file and prints the imp roster.

Usage: dispatch-banner.py [slug]   (slug defaults to $SLUG, then basename of cwd)
"""
import os, json, sys

slug = (sys.argv[1] if len(sys.argv) > 1 else None) or os.environ.get('SLUG') \
    or os.path.basename(os.environ.get('CLAUDE_PROJECT_DIR') or os.getcwd())
state_path = os.path.expanduser(f'~/.claude/imps/runs/{slug}.json')
with open(state_path) as f:
    state = json.load(f)

tasks = state['tasks']
n     = len(tasks)
# imp/spirit/daemon-themed Nerd Font glyphs, assigned by task id (cycling)
# ghost · skull · devil · bat · spider · skull-crossbones · grave-stone · coffin
IMPS   = ['\U000F02A0', '\U000F068C', '\U000F0556', '\U000F0B5F',
          '\U000F11D5', '\U000F0680', '\U000F0BAB', '\U000F1322']
ATTY   = sys.stdout.isatty()
RST    = '\033[0m' if ATTY else ''
PINK   = '\033[38;5;211m' if ATTY else ''   # opus
YELLOW = '\033[93m'       if ATTY else ''   # sonnet
GREEN  = '\033[92m'       if ATTY else ''   # haiku / default

def model_color(m):
    m = (m or '').lower()
    if 'opus' in m:   return PINK
    if 'sonnet' in m: return YELLOW
    return GREEN

def colored_imp(t):
    idx = (t['id'] - 1) % len(IMPS)
    return f'{model_color(t.get("model",""))}{IMPS[idx]}{RST}'

bats = '  '.join(colored_imp(t) for t in tasks)
print(f'  {bats}  {n} imps handed to the wrangler')
for t in tasks:
    deps    = t.get('deps', [])
    dep_str = '  waits: ' + ', '.join(f'#{d}' for d in deps) if deps else ''
    label   = t.get('label', '')
    model   = t.get('model', '?').split('-')[1] if '-' in t.get('model', '') else t.get('model', '?')
    typ     = t.get('type', '?')
    print(f'  {colored_imp(t)}  #{t["id"]}  {label}  [{model} · {typ}{dep_str}]')
print()
print(f'progress: cat {state_path}  ·  type anything to keep working')
