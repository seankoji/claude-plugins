---
description: Emit a heartbeat for active /imps runs. Self-reschedules via ScheduleWakeup; stops when the state dir is empty. Shows only still-working imps with a one-liner each.
---

# /imps:status — imp roll-call

**Before executing any steps**, output the following intro block so the user knows what's happening:

> 🦇 **imps:status** — checking in on your swarm
>
> Polling active imps runs and showing which agents are still working. This command
> self-reschedules automatically until the run completes — no need to invoke it again manually.

---

This command is a self-pacing heartbeat. It reads active run state, emits one status
block per run (showing only imps still working), and reschedules itself via `ScheduleWakeup`.
When the run is over (state file deleted by the merge phase), it stops by not rescheduling.

**Important:** Do NOT invoke this via the `/loop` skill — `loop` uses `CronCreate` which
cannot self-cancel. Invoke it directly; it owns its own rescheduling.

This command does **not** merge. Merging is driven by the task notification in the main
session that launched /imps.

---

## Step 1 — Check for active runs

List `~/.claude/imps/runs/*.json`. Multiple projects can have concurrent runs.

```bash
ls ~/.claude/imps/runs/*.json 2>/dev/null || true
```

If the directory does not exist or contains no `.json` files:
```
󰭟  no active imps — stopping
```
Do **not** call `ScheduleWakeup`. Return immediately. The loop is now dead.

---

## Step 2 — Determine which imps are still working

For each state file, build a `completed_ids` set for that run:

a. Read the file: `tasks`, `workflow_task_id`, `workflow_run_id`, `repo`, `dispatched_at`, `poll_interval_seconds`

b. If `workflow_task_id` is non-null:
   1. Load `TaskOutput` schema: call `ToolSearch` with `query: "select:TaskOutput"`
   2. Call `TaskOutput` with `{ taskId: "<workflow_task_id>" }`
   3. Scan the returned text for lines matching the pattern `imp:done #(\d+)` — collect
      the integer N values as `completed_ids` for this run
   4. Classify the result:
      - **"No task found"** or any cross-session error: use string `"cross-session"`.
        TaskOutput is session-scoped — it cannot query workflow IDs started in a different
        Claude Code session. The heartbeat shows these imps with a tracking-unavailable note.
      - **Success, but zero `imp:done` lines** (whether `imp:start` lines are present or
        output is empty/very short): use string `"not_ready"`. The workflow is running but
        no imps have finished yet; logs don't expose per-imp completion at this tick.
      - **Success with one or more `imp:done` lines**: use the collected integer IDs as
        `completed_ids`.
      - **Any other error** (timeout, transient): use `[]` — show all imps as active.

c. If `workflow_task_id` is null: `completed_ids = []`

Build a JSON object mapping each slug to its value — a list of completed IDs, or one of
the sentinel strings `"cross-session"` or `"not_ready"`. For example:
```json
{"my-repo": [1, 3], "other-project": "not_ready", "foreign-run": "cross-session"}
```

---

## Step 3 — Emit the heartbeat

Run this Bash command. Substitute `IMP_STATUS` with the JSON object from Step 2 (as a
single-quoted shell string). Do not add extra shell escaping — construct the JSON string
cleanly before passing it.

```bash
IMP_STATUS='{"my-repo": [1, 3]}' python3 - <<'PYEOF'
import os, json
from datetime import datetime, timezone

per_run   = json.loads(os.environ.get('IMP_STATUS', '{}'))
state_dir = os.path.expanduser('~/.claude/imps/runs')
# imp/spirit/daemon-themed Nerd Font glyphs, assigned by task id (cycling)
# ghost · skull · devil · bat · spider · skull-crossbones · grave-stone · coffin
IMPS      = ['\U000F02A0', '\U000F068C', '\U000F0556', '\U000F0B5F',
             '\U000F11D5', '\U000F0680', '\U000F0BAB', '\U000F1322']
RST       = '\033[0m'
PINK      = '\033[38;5;211m'   # opus
YELLOW    = '\033[93m'         # sonnet
GREEN     = '\033[92m'         # haiku / default

def model_color(m):
    m = (m or '').lower()
    if 'opus' in m:   return PINK
    if 'sonnet' in m: return YELLOW
    return GREEN

def colored_imp(t):
    idx = (t['id'] - 1) % len(IMPS)
    return f'{model_color(t.get("model",""))}{IMPS[idx]}{RST}'

try:
    files = sorted(f for f in os.listdir(state_dir) if f.endswith('.json'))
except FileNotFoundError:
    raise SystemExit(0)

if not files:
    raise SystemExit(0)

multi = len(files) > 1

for fname in files:
    slug = fname.replace('.json', '')
    with open(os.path.join(state_dir, fname)) as f:
        state = json.load(f)

    completed_raw = per_run.get(slug, [])
    cross_session = completed_raw == 'cross-session'
    not_ready     = completed_raw == 'not_ready'
    completed     = set() if (cross_session or not_ready) else set(completed_raw)
    tasks         = state.get('tasks', [])

    try:
        dt      = datetime.fromisoformat(state['dispatched_at'].replace('Z', '+00:00'))
        secs    = int((datetime.now(timezone.utc) - dt).total_seconds())
        elapsed = f'{secs // 60}m {secs % 60}s'
    except Exception:
        elapsed = '?'

    active = [t for t in tasks if t['id'] not in completed]
    n      = len(active)
    total  = len(tasks)
    bats   = '  '.join(colored_imp(t) for t in active)

    if cross_session:
        note = '[x-session · tracking unavailable]'
    elif not_ready:
        plural = 'both' if n == 2 else ('all' if n > 2 else '')
        note   = (plural + ' ' if plural else '') + \
                 "still running. The workflow doesn't expose incremental logs (not_ready), " \
                 "so I can't see per-imp completion until the whole run finishes; " \
                 "the heartbeat shows " + (plural + ' ' if plural else '') + "active by default."
    else:
        done_n = total - n
        if done_n > 0:
            note = f'{done_n} done, {n} running'
        else:
            plural = 'both' if n == 2 else ('all' if n > 2 else '')
            note   = (plural + ' ' if plural else '') + 'still running'

    prefix = f'{state.get("repo", slug)} · ' if multi else ''
    print(f'{bats}  {n}/{total} imps still out · {prefix}{elapsed} — {note}')

    # Show blocked-on-deps detail only for imps waiting on unmet deps
    for t in active:
        left = [d for d in t.get('deps', []) if d not in completed]
        if left:
            label = t.get('label', '')
            print(f'  {colored_imp(t)}  #{t["id"]}  {label}  waits: {", ".join(f"#{d}" for d in left)}')
PYEOF
```

Do not emit any other text — the script output is the full status block.

---

## Step 4 — Reschedule via ScheduleWakeup

Read `poll_interval_seconds` from the state files. If multiple runs are active, use the
**minimum** interval across all of them. Fall back to `300` if the field is missing.

Call `ScheduleWakeup` with:
- `delaySeconds`: `<poll_interval_seconds from above>`
- `prompt`: `/imps:status`
- `reason`: `imps heartbeat — N imp(s) still out`

After the call, emit exactly one closing line:
```
Rescheduling in Xm.
```
where `X` is `poll_interval_seconds ÷ 60` (integer, round down). For intervals under
60 s use `Xm Ys` instead. This is what keeps the loop alive. Step 1 stops it by not
reaching this step.
