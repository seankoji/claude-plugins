#!/usr/bin/env python3
"""Emit a complete review contract, omitting only non-contract GOAL narrative."""
import re
import sys
from pathlib import Path

def project(text):
    lines = text.splitlines(keepends=True)
    keep = False
    selected = []
    for line in lines:
        heading = re.match(r'^##\s+(.+)', line)
        if heading:
            name = heading.group(1).strip().lower()
            keep = name.startswith('definition of done') or name.startswith('global constraints')
        if keep:
            selected.append(line)
    if not selected:
        return text, False
    result = ''.join(selected)
    return result, result != text

if __name__ == '__main__':
    value, omitted = project(Path(sys.argv[1]).read_text())
    if omitted:
        print('Non-contract GOAL narrative omitted; Definition of Done and Global Constraints retained verbatim.')
        print('review context: non-contract narrative omitted', file=sys.stderr)
    sys.stdout.write(value)
