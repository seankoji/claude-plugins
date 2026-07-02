---
name: imps:status
description: Emit a heartbeat for active /imps:imps runs. Self-reschedules via ScheduleWakeup; stops when no run-state files remain (ignores /imps:prs's own .prs.json files). Shows only still-working imps with a one-liner each.
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
session that launched `/imps:imps`.

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

a. Read the file: `tasks`, `workflow_task_id`, `workflow_run_id`, `workflow_output_file`,
   `repo`, `dispatched_at`, `poll_interval_seconds`

b. **Preferred — grep the output file (zero log ingestion, works cross-session).**
   If `workflow_output_file` is non-null, extract only the marker lines with Bash:
   ```bash
   grep -oE 'imp:(start|done) #[0-9]+' "<workflow_output_file>" 2>/dev/null | sort -u
   ```
   - Command succeeds with output → parse `imp:done #N` into `completed_ids` and
     `imp:start #N` into `started_ids`; use
     `{"completed": completed_ids, "started": started_ids}`. Skip step c.
   - File exists but no matches → use string `"not_ready"` (the workflow may still be
     initializing). Skip step c.
   - File missing/unreadable → fall through to step c.

   Never `cat`, `Read`, or otherwise ingest the output file — the grep above is the
   only permitted access; workflow logs must not enter this context.

c. **Fallback — TaskOutput** (session-scoped; only when step b was unavailable).
   If `workflow_task_id` is non-null:
   1. Load `TaskOutput` schema: call `ToolSearch` with `query: "select:TaskOutput"`
   2. Call `TaskOutput` with `{ taskId: "<workflow_task_id>", block: false }`
   3. Scan the returned text for `imp:done #(\d+)` → `completed_ids` and
      `imp:start #(\d+)` → `started_ids`. Discard the rest — never quote workflow log
      text in your reply.
   4. Classify the result:
      - **"No task found"** or any cross-session error: use string `"cross-session"`.
        TaskOutput is session-scoped — it cannot query workflow IDs started in a different
        Claude Code session. The heartbeat shows these imps with a tracking-unavailable note.
      - **Success, but zero `imp:done` AND zero `imp:start` lines** (output empty or very
        short — no signal yet): use string `"not_ready"`. The workflow may still be
        initializing.
      - **Success with any `imp:start` or `imp:done` lines**: use
        `{"completed": completed_ids, "started": started_ids}` (both can be empty lists).
        This is the normal in-flight case — partial visibility is better than none.
      - **Any other error** (timeout, transient): use `{"completed": [], "started": []}` —
        show all imps as active.

d. If both `workflow_output_file` and `workflow_task_id` are null: use
   `{"completed": [], "started": []}`

Build a JSON object mapping each slug to its value — a dict with `completed`/`started`
lists, or one of the sentinel strings `"cross-session"` or `"not_ready"`. For example:
```json
{"my-repo": {"completed": [1, 3], "started": [2, 4]}, "other-project": "not_ready", "foreign-run": "cross-session"}
```

---

## Step 3 — Emit the heartbeat

Run this Bash command. Substitute `IMP_STATUS` with the JSON object from Step 2 (as a
single-quoted shell string). Do not add extra shell escaping — construct the JSON string
cleanly before passing it.

```bash
IMP_STATUS='{"my-repo": {"completed": [1, 3], "started": [2]}}' python3 - <<'PYEOF'
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
DIM       = '\033[2m'

def model_color(m):
    m = (m or '').lower()
    if 'opus' in m:   return PINK
    if 'sonnet' in m: return YELLOW
    return GREEN

def colored_imp(t, dim=False):
    idx = (t['id'] - 1) % len(IMPS)
    prefix = DIM if dim else model_color(t.get('model', ''))
    return f'{prefix}{IMPS[idx]}{RST}'

try:
    # Exclude *.prs.json: those are /imps:prs PR-monitor state files, not run
    # state, and can be written into this same dir moments after a run's own
    # state file is deleted — matching them here would make the heartbeat
    # treat an unrelated PR-monitor file as a still-running imps run.
    files = sorted(
        f for f in os.listdir(state_dir)
        if f.endswith('.json') and not f.endswith('.prs.json')
    )
except FileNotFoundError:
    raise SystemExit(0)

if not files:
    raise SystemExit(0)

multi = len(files) > 1

for fname in files:
    slug = fname.replace('.json', '')
    with open(os.path.join(state_dir, fname)) as f:
        state = json.load(f)

    status_raw    = per_run.get(slug, {})
    cross_session = status_raw == 'cross-session'
    not_ready     = status_raw == 'not_ready'
    if cross_session or not_ready:
        completed, started = set(), set()
    elif isinstance(status_raw, list):   # backward compat with old format
        completed, started = set(status_raw), set()
    else:
        completed = set(status_raw.get('completed', []))
        started   = set(status_raw.get('started', [])) - completed
    tasks         = state.get('tasks', [])

    secs = None
    try:
        dt      = datetime.fromisoformat(state['dispatched_at'].replace('Z', '+00:00'))
        secs    = int((datetime.now(timezone.utc) - dt).total_seconds())
        elapsed = f'{secs // 60}m {secs % 60}s'
    except Exception:
        elapsed = '?'

    active   = [t for t in tasks if t['id'] not in completed]
    running  = [t for t in active if t['id'] in started]
    waiting  = [t for t in active if t['id'] not in started]
    n        = len(active)
    n_done   = len(tasks) - n
    total    = len(tasks)
    # running imps show full color; waiting imps show dim
    bats     = '  '.join(
        colored_imp(t, dim=(t['id'] not in started)) for t in active
    )

    if cross_session:
        note = '[x-session · tracking unavailable]'
    elif not_ready:
        note = 'no signal yet — polling'
    else:
        parts = []
        if n_done:        parts.append(f'{n_done} done')
        if running:       parts.append(f'{len(running)} running')
        if waiting:       parts.append(f'{len(waiting)} waiting')
        note = ' · '.join(parts) if parts else 'dispatched'

    prefix = f'{state.get("repo", slug)} · ' if multi else ''
    print(f'{bats}  {n}/{total} imps still out · {prefix}{elapsed} — {note}')

    # Staleness guard: a hung/crashed workflow otherwise shows "still working" forever.
    # Warn once elapsed passes a generous multiple of the poll interval (≈12 polls, min 1 h).
    poll        = state.get('poll_interval_seconds', 300) or 300
    stale_after = max(3600, poll * 12)
    if active and secs is not None and secs > stale_after:
        print(f'  ⚠ no completion after {secs / 3600:.1f}h (> {stale_after // 3600}h '
              f'threshold) — workflow may be hung or crashed; check /workflows, or abandon '
              f'the run via `/imps:imps` to start fresh')

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
