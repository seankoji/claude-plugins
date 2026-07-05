#!/usr/bin/env python3
"""Final banner for /imps:imps — reads the wrangler's run_complete checkpoint JSON from
stdin and prints the all-imps-back header line.

Usage: echo '<run_complete json>' | final-banner.py
"""
import json, sys

checkpoint = json.load(sys.stdin)
run_stats  = checkpoint.get('run_stats') or {}
tasks      = run_stats.get('tasks')
if not tasks:
    # fallback: synthesize a roster from model_counts
    counts = run_stats.get('model_counts') or {}
    tasks, i = [], 1
    for model in ('haiku', 'sonnet', 'opus'):
        for _ in range(counts.get(model, 0)):
            tasks.append({'id': i, 'model': model}); i += 1
n = len(tasks)

# imp/spirit/daemon-themed Nerd Font glyphs, assigned by task id (cycling)
# ghost · skull · devil · bat · spider · skull-crossbones · grave-stone · coffin
IMPS   = ['\U000F02A0', '\U000F068C', '\U000F0556', '\U000F0B5F',
          '\U000F11D5', '\U000F0680', '\U000F0BAB', '\U000F1322']
SLEEP  = '\U000F04B2'
TOWER  = '♜'
ATTY   = sys.stdout.isatty()
BG     = '\033[40m' if ATTY else ''
RST    = '\033[0m' if ATTY else ''
TWRC   = '\033[38;5;245m' if ATTY else ''
PINK   = '\033[38;5;211m' if ATTY else ''   # opus
YELLOW = '\033[93m'       if ATTY else ''   # sonnet
GREEN  = '\033[92m'       if ATTY else ''   # haiku / default

def model_color(m):
    m = (m or '').lower()
    if 'opus' in m:   return PINK
    if 'sonnet' in m: return YELLOW
    return GREEN

cap_spec = {'H': 0x210B, 'I': 0x2110, 'L': 0x2112, 'R': 0x211B,
            'B': 0x212C, 'E': 0x2130, 'F': 0x2131, 'M': 0x2133}
low_spec = {'e': 0x212F, 'g': 0x210A, 'h': 0x210E, 'o': 0x2134}

def italic(s):
    out = []
    for c in s:
        if 'A' <= c <= 'Z': out.append(chr(cap_spec.get(c, 0x1D434 + ord(c) - ord('A'))))
        elif 'a' <= c <= 'z': out.append(chr(low_spec.get(c, 0x1D44E + ord(c) - ord('a'))))
        else: out.append(c)
    return ''.join(out)

def colored_imp(t):
    idx = (t['id'] - 1) % len(IMPS)
    return f'{model_color(t.get("model",""))}{IMPS[idx]}{RST}{BG}'

label = italic(f'all {n} imp{"s" if n != 1 else ""} back')
imps  = ' '.join(colored_imp(t) for t in tasks)
print(f'{BG} {TWRC}{TOWER}{RST}{BG} {imps} {SLEEP}  {label}{RST}')
