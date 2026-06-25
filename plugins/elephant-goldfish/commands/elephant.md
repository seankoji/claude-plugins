---
description: >
  Create, validate, or maintain a repo's elephant.md — a durable, authoritative design doc
  (Rensin's Elephant-Goldfish model) that lets a zero-context "goldfish" session re-bootstrap the
  project without token-heavy code re-reading. Fans out parallel haiku discovery scouts; a single
  opus author writes the doc; a different-lineage Gemini goldfish (via `agy`) validates it in a
  closed loop. Bare = create-if-absent then goldfish-gate (or just gate an existing doc);
  `reconcile` = drift pass; `regenerate` = rebuild from code; any other text = manual goldfish
  failure report to fold in.
argument-hint: '[reconcile | regenerate | <goldfish failure report>]'
---

# /elephant — the durable design doc, self-validating

Arguments: `$ARGUMENTS`

You are a documentation architect. Your job is to write, **validate**, or update `elephant.md` — a
durable, authoritative design document that lets a zero-context "goldfish" session re-bootstrap
this project without reading all the code. "Design is the new code." One repo = one `elephant.md`
at the repo root, and it is always the authoritative source of truth.

The doc is only trustworthy once a **cold, independent reader** can bootstrap from it. That reader
is a different-lineage model (Gemini, via the `agy` CLI) running read-only — different priors than
this Claude author, and a separate process so it cannot see this session's context. Feeding the
elephant and testing it against that goldfish is now built into this command.

---

## Configuration

- `MAX_GOLDFISH_ITERS` (default **5**) — hard cap on judge → patch → re-judge rounds.
- `GOLDFISH_AFTER_CREATE` (default **true**) — after CREATE, run the goldfish gate to validate the
  fresh doc. A doc that has never been cold-read is a hypothesis, not a source of truth. Set
  `false` for pure create-only (what a bare run on a missing doc would otherwise do).
- `GOLDFISH_JUDGE` (default `${CLAUDE_PLUGIN_ROOT}/scripts/goldfish-judge.sh`) — path to the
  cold-judge helper. Bundled with this plugin; override via environment if needed.
- `AGY_MODEL` (default `gemini-3.1-pro`) — the judge model. **Must be a Gemini model.** `agy` can
  also run Claude; a Claude judge shares this author's priors and reintroduces the clone problem.
  `agy`'s default is already Gemini, so the lineage requirement holds even if the flag is wrong.

The judge is read-only and different-lineage **by design** — that, plus its being a separate cold
process, is what makes a PASS mean something. Do not weaken either property.

---

## Step 0 — Locate the repo root

Run `git rev-parse --show-toplevel`.

- If it succeeds, that path is `<root>`.
- If it fails (not a git repo), fall back to `pwd` as `<root>` and print one warning line:
  `⚠ Not inside a git repository — scoping to cwd: <root>`

Set `<doc>` = `<root>/elephant.md`. Note whether `<doc>` currently exists (`DOC_EXISTS`). If it
does, read its **full contents** now; you will need them in the authoring step.

---

## Step 1 — Resolve mode

Trim `$ARGUMENTS` (remove leading/trailing whitespace and a single pair of wrapping quotes if
present). Then apply these rules **in order**:

1. **Empty** (nothing or only whitespace) →
   - `DOC_EXISTS == false` → **CREATE** (then run the **Goldfish Gate** unless
     `GOLDFISH_AFTER_CREATE == false`)
   - `DOC_EXISTS == true`  → **GOLDFISH GATE** (validate the existing doc — this slot no longer
     runs RECONCILE)
2. **Exactly the single token `reconcile`** (case-insensitive, no other tokens) → **RECONCILE**
3. **Exactly the single token `regenerate`** (case-insensitive, no other tokens) → **REGENERATE**
   (`regenerate the auth section` has multiple tokens → goes to FEEDBACK)
4. **Any other non-empty text** → **FEEDBACK** (the full trimmed string is a goldfish's failure
   report; keep it verbatim for the author)

Print one operator-visible line before continuing:
`🐘 mode: <MODE> · doc: <doc>`

**Dispatch:** CREATE, RECONCILE, REGENERATE, and FEEDBACK run Steps 2–7 as normal. CREATE then
enters the Goldfish Gate (below) by default. A bare run on an existing doc runs **only** the
Goldfish Gate, which itself drives the FEEDBACK path each failing round.

---

## The Goldfish Gate  (GOLDFISH mode; also runs after CREATE unless disabled)

Run `<doc>` against the cold, different-lineage judge and fold failures back in until it passes or
a guard trips. **Editor = the Opus author** (Steps 2–4 in FEEDBACK mode). **Judge = `agy`/Gemini,
read-only**, via `$GOLDFISH_JUDGE`. This is the closed loop: judge → patch → re-judge the *whole*
doc, never a partial re-check.

Set up once per gate run (Bash):

```bash
GOLDFISH_JUDGE="${GOLDFISH_JUDGE:-${CLAUDE_PLUGIN_ROOT}/scripts/goldfish-judge.sh}"
RUNDIR=".goldfish-runs/$(date +%Y%m%d-%H%M%S)"; mkdir -p "$RUNDIR"
# SEEN holds spec-content hashes across rounds, for oscillation/stall detection.
hash_doc() { { sha256sum "$1" 2>/dev/null || shasum -a 256 "$1" 2>/dev/null || cksum "$1"; } | awk '{print $1}'; }
SEEN="$(hash_doc "<doc>")"
```

Loop, at most `MAX_GOLDFISH_ITERS` rounds:

1. `iter++`; print `🐟 goldfish pass <iter> (judge: agy/$AGY_MODEL)`.
2. **Judge** (Bash). The helper is fail-closed: empty output or a missing verdict is an error, not
   a pass.
   ```bash
   REPORT_OUT="$RUNDIR/judge-<iter>.md" AGY_MODEL="$AGY_MODEL" \
     bash "$GOLDFISH_JUDGE" "<doc>"; RC=$?
   ```
3. **Branch on `RC`:**
   - `RC == 0` → **READY.** Print `✓ goldfish PASS after <iter> pass(es)`. Stop the loop; go to
     Step 7. Drift is a *separate* question — if they want prose-vs-code sync, point them at
     `/elephant-goldfish:elephant reconcile`. Do **not** silently run RECONCILE here.
   - `RC == 2` → **judge error / empty / no verdict.** **Abort to a human.** Do not loop, do not
     treat as a pass. Print the helper's message and `"$RUNDIR/judge-<iter>.md"`. Stop.
   - `RC == 10` → **NOT READY.** The file `"$RUNDIR/judge-<iter>.md"` is the goldfish failure
     report. Run the **FEEDBACK path**: execute Steps 2–4 with `MODE = FEEDBACK` and `$ARGUMENTS`
     = the verbatim contents of that report. Scout E targets exactly the named gaps; the Opus
     author patches **only** those gaps; the main session **Writes** `<doc>`; each fix is logged
     under `## Goldfish traps`. Then, if in a repo, commit `<doc>`. Snapshot:
     `cp "<doc>" "$RUNDIR/iter-<iter>.elephant.md"`.
4. **Oscillation/stall guard** (Bash): `H="$(hash_doc "<doc>")"`. If `H` already appears in `SEEN`
   (the patch changed nothing, or the loop is cycling A→B→A), **abort to a human** — the loop
   cannot make progress, which usually means the doc is underspecified in a way looping will not
   fix. Print `$RUNDIR` and stop. Otherwise append `H` to `SEEN`.
5. **Re-judge the whole doc:** loop back to step 1.

If the loop reaches `MAX_GOLDFISH_ITERS` still NOT READY → **abort to a human** with the last
report path. Then proceed to Step 7 reporting the non-convergence.

> The judge invents nothing in your repo — it only reads and reports. The **author** invents the
> decisions that close gaps. Every round is a commit and a snapshot, so before you trust the
> result, `diff` the gate's changes and read what it decided. A PASS means *a cold reader can
> bootstrap from this doc*, not that every invented decision is the one you'd have made.

---

## Step 2 — Parallel discovery

Launch the scouts **in a single message** (one Agent tool call block) so they run concurrently.
Each scout is `model: haiku`, `subagent_type: Explore` (fall back to `general-purpose` if Explore
is unavailable). Every scout returns a **compact structured digest only** — `path` or `path:line`
references, never pasted file bodies or raw command output. The whole point of the elephant is to
avoid token-heavy re-reading; discovery must not itself blow up context.

### Scout A — Stack & build
Find: language(s), package manifests (`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`,
`Makefile`, etc.), CI config (`.github/workflows/`, `.circleci/`, etc.), entry points, and the
build/test/lint/run commands. Return as `{stack, manifests, entry_points, commands, ci}`.

### Scout B — Architecture
Find: top-level directory/module map, major components and how they relate, data flow, key
boundaries, the file or directory that owns each component. Return as
`{components: [{name, role_one_line, owns_path, talks_to: [...]}], data_flow_notes}`.

### Scout C — Integrations & deps
Find: external services, APIs, datastores, notable third-party libraries, environment variables /
secrets surface, config files. Return as `{external_services, notable_deps, env_vars, config_files}`.

### Scout D — Existing docs & tests
Find: `README.md`, `CLAUDE.md`, `AGENTS.md`, `docs/`, ADRs, existing `elephant.md` (pass its full
contents through if present — do not summarise), and the test layout (framework, coverage shape).
Return as `{docs_summary, existing_elephant, test_layout}`.

### Scout E (FEEDBACK mode only) — Targeted gap scout
Aimed squarely at the area named in the goldfish's failure report. Read exactly the files/symbols
the failure report implicates and return ground truth: what the doc said vs what the code actually
does at that spot, with `path:line` refs.

Collect all scout digests into a single findings bundle. Do not re-read what the scouts already
covered.

> Isolation note: the scout digests and this session's reasoning are **the author's** context.
> They are never handed to the goldfish judge — the judge sees only `<doc>` and the repo it reads
> read-only, exactly what a real future cold session would have. Do not pass scout output to `agy`.

---

## Step 3 — Opus author

Spawn **one** Agent with `model: claude-opus-4-8`. Because the author never sees the live
transcript, the prompt you give it must include **everything** it needs:

1. The selected **mode** and its directive (from Step 4 below)
2. The **full current `elephant.md` contents** (if `DOC_EXISTS`)
3. All **scout digests** from the findings bundle
4. The verbatim **goldfish failure report** (FEEDBACK mode only)
5. The **section template** from Step 5 below
6. The **honesty guardrails** from Step 6 below

The author returns the **complete proposed `elephant.md` content** as plain text.

The **main session writes the file** — do not have the author write it directly:
- CREATE / REGENERATE: use Write to overwrite `<doc>`
- RECONCILE / FEEDBACK: use Write to apply the author's output to `<doc>`

---

## Step 4 — Per-mode directives for the author

Include the relevant directive in the author's prompt verbatim.

### CREATE
No prior doc exists. Write a fresh `elephant.md` from the discovery bundle using the section
template. All four canonical sections must be populated. No `## Drift` section (nothing yet to
diverge from). Mark any inferred-but-unconfirmed rationale as `_(inferred from code — confirm)_`.
After writing, control returns to the **Goldfish Gate** to validate the new doc (unless
`GOLDFISH_AFTER_CREATE == false`).

### RECONCILE
**Treat the existing doc as authoritative.** "Design is the new code" — the doc says what the
project *should* be. Update prose to match current reality where the code confirms the design.
Add or refresh a `## Drift` callout listing every place code has diverged from the documented
design as: `doc says X / code does Y (path:line)`. If no drift: `No drift detected as of <date>`.
**Never silently discard human-written rationale or the `## Alternatives` section.** Preserve
them; only append or correct. Mark superseded rationale as superseded with a reason — do not delete.

### REGENERATE
Rebuild `## The Problem`, `## The Technical Plan`, and `## Detailed Implementation` from code
analysis (overwrite). **But fold back still-valid human rationale and `## Alternatives` from the
old doc.** This is a rebuild, not a wipe. Pass the old doc contents to the author so it can diff
intent, not just text.

### FEEDBACK
`$ARGUMENTS` (or, inside the Goldfish Gate, the judge's failure report) is a goldfish's failure
report. Patch the doc to close **exactly those gaps** — do not rewrite unrelated sections. Record
each closed gap under `## Goldfish traps` as a dated bullet:
> `<date> — goldfish: <what went wrong>. fix: <what was added/clarified and where>.`
Ground every patch in the targeted scout's findings (Scout E), not guesswork.

---

## Step 5 — Section template

The author emits `elephant.md` with this structure. The four canonical sections are **always**
present. `## Drift` and `## Goldfish traps` are mode-conditional. `## Open questions` is the
honesty pressure-valve — anything unverified goes there, not into the authoritative body.

```markdown
# <Project name> — Elephant (design of record)
<!-- Authoritative design doc. "Design is the new code." A zero-context session re-bootstraps
     from this file alone. Last reconciled: <date> against <git sha or "pre-git">. -->

## The Problem
<!-- Business/product context: what this exists to solve, for whom, and why.
     The "why", not the "how". -->

## The Technical Plan
<!-- Component architecture + relationships. Each component cites the path that owns it.
     Include a component list with one-line roles and data flow notes. -->

## Alternatives
<!-- Approaches CONSIDERED AND REJECTED, each with the reasoning. Human rationale is
     preserved verbatim across reconciles; mark superseded items as superseded, never delete. -->

## Detailed Implementation
<!-- Step-by-step with explicit file listings. Key files/dirs, one-line roles, build/test/run
     commands, entry points, external integrations. The map a goldfish follows to find the code. -->

## Drift
<!-- RECONCILE only. "doc says X / code does Y (path:line)" for each divergence.
     "No drift detected as of <date>." if clean. Omit section in CREATE/REGENERATE/FEEDBACK. -->

## Goldfish traps
<!-- Dated log of gaps a zero-context reader hit, and the doc change that closes each. Now
     populated automatically by the Goldfish Gate as well as by manual FEEDBACK runs.
     "None recorded yet." if there have been no failures. -->

## Open questions / unverified
<!-- Anything the scouts could not confirm. Kept OUT of the authoritative body above.
     "None." if scouts resolved everything. -->

---
*Refresh: `/elephant-goldfish:elephant` (create-if-absent then goldfish-gate, or gate an existing doc) ·
`/elephant-goldfish:elephant reconcile` (drift) · `/elephant-goldfish:elephant regenerate` (rebuild from code) ·
`/elephant-goldfish:elephant <what went wrong>` (fold in a manual goldfish report)*
```

---

## Step 6 — Honesty guardrails

Pass these to the author and enforce them in the main session when reviewing the output:

- Every claim about the code carries a **file reference** (`path` or `path:line`). Unreferenced
  architectural claims are not allowed.
- Distinguish **observed** (grounded in a scout finding) from **inferred** (author's reading of
  the digests). Label inferences explicitly.
- Never invent components, services, or files. If a scout couldn't confirm something, it goes
  under `## Open questions / unverified`, not into the authoritative body.
- Preserve human rationale verbatim where it still holds. Mark superseded rationale as superseded
  with a reason. Do not delete.
- Keep it scannable — this is a bootstrap doc, not API reference. Short, purposeful prose.
- **Judge integrity:** the goldfish judge must stay different-lineage (Gemini), read-only, and fed
  only `<doc>` + the repo. Never hand it the scout digests or this session's reasoning — that is
  context the real future goldfish will not have, and it would pass docs a true cold reader fails.

---

## Step 7 — Closing summary

After the run, reply with a compact summary block:

```
🐘 elephant.md <created|gated|reconciled|regenerated|patched> · <doc>
Mode: <MODE>   Scouts: <N> haiku   Author: opus   Judge: agy/<AGY_MODEL>
Goldfish: <PASS after N pass(es) | ABORTED: stalled | ABORTED: max iters | ABORTED: judge error | n/a>
Drift items flagged: <N>              (RECONCILE only)
Goldfish traps closed this run: <N>   (GOLDFISH/FEEDBACK only)
Sections: Problem · Technical Plan · Alternatives · Detailed Implementation [· Drift] [· Goldfish traps]
Run dir: <.goldfish-runs/... | n/a>
```

Then one reminder line: if `elephant.md` is in a tracked repo, commit it — it is a first-class
deliverable alongside the code, not a generated artefact to gitignore. If the gate aborted, the
doc still has open gaps; the run dir holds the last judge report.
