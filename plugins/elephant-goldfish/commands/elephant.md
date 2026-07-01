---
description: >
  Create, validate, or maintain a repo's elephant.md — a durable, authoritative design doc
  (Rensin's Elephant-Goldfish model) that lets a zero-context "goldfish" session re-bootstrap the
  project without token-heavy code re-reading. Bare with an existing doc = interactive menu.
  Bare without a doc = CREATE. `update` = drift pass then goldfish gate; `check` = read-only drift
  report; `validate` = goldfish gate only; `regenerate` = rebuild from code then goldfish gate;
  any other text = manual goldfish failure report to fold in.
argument-hint: '[update | check | validate | regenerate | <goldfish failure report>]'
---

# /elephant — the durable design doc, self-validating

Arguments: `$ARGUMENTS`

**Before executing any steps**, output the following intro block so the user knows what's happening:

> 🐘 **elephant-goldfish** — keeping your design doc honest
>
> `elephant.md` is a durable design document that lets any new Claude session understand your
> project without reading all the code. This command creates, updates, or validates it using
> parallel discovery scouts and a cold Gemini reader — so a PASS means it actually works for
> someone walking in with zero context.

---

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
- `AGY_MODEL` (default `gemini-3.1-pro`) — the primary judge model. **Must be a Gemini model.**
  `agy` can also run Claude; a Claude judge shares this author's priors and reintroduces the clone
  problem. `agy`'s default is already Gemini, so the lineage requirement holds even if the flag is
  wrong.
- `OLLAMA_MODEL` (default unset) — when set, enables an optional second-opinion judge via the
  `ollama` CLI, run **sequentially after `agy`**. Set to any model name `ollama run` accepts (e.g.
  `qwen3:14b-q8_0`). Consensus is fail-closed AND: the gate passes only if **both** judges say
  READY. `OLLAMA_HOST` is honored for a remote Ollama instance. **Do not point this at a Claude
  model** — that reintroduces the clone problem.
- `OLLAMA_NO_THINK` (default `true`) — prepends `/no_think` to the Ollama prompt to suppress
  the `<think>` preamble on qwen3 and compatible thinking models, ensuring `VERDICT:` is the first
  output line. Set `false` for non-thinking models that don't recognise the token.
- `OLLAMA_HOST` — passed through to the `ollama` CLI to target a remote instance (e.g.
  `http://pc.robot.house:11434`).

All judges are read-only and different-lineage **by design** — that, plus their being separate cold
processes, is what makes a PASS mean something. Do not weaken either property.

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
   - `DOC_EXISTS == false` → **CREATE**
   - `DOC_EXISTS == true`  → **INTERACTIVE** (show menu — see below)
2. **Exactly the single token `update`** (case-insensitive, no other tokens) → **UPDATE**
3. **Exactly the single token `check`** (case-insensitive, no other tokens) → **CHECK**
4. **Exactly the single token `validate`** (case-insensitive, no other tokens) → **VALIDATE**
5. **Exactly the single token `regenerate`** (case-insensitive, no other tokens) → **REGENERATE**
   (`regenerate the auth section` has multiple tokens → goes to FEEDBACK)
6. **Any other non-empty text** → **FEEDBACK** (the full trimmed string is a goldfish's failure
   report; keep it verbatim for the author)

Print one operator-visible line before continuing:
`🐘 mode: <MODE> · doc: <doc>`

### INTERACTIVE mode

Use the `AskUserQuestion` tool to present the following single-select question:

> **Question:** "What would you like to do with elephant.md?"
> **Header:** "elephant.md"
> **Options:**
> 1. label: "Update" — description: "Sync the doc to code changes, then validate iteratively with the Goldfish judge"
> 2. label: "Regenerate" — description: "Rebuild the doc from scratch from code, then validate iteratively with the Goldfish judge"
> 3. label: "Check" — description: "Quick read-only drift check — reports gaps without writing anything"
> 4. label: "Validate" — description: "Run the Goldfish judge on the existing doc without any rewrite first"

Map the user's answer to the corresponding mode (**UPDATE**, **REGENERATE**, **CHECK**, or
**VALIDATE**) and print the resolved `🐘 mode:` line before continuing.

**Dispatch:**
- **CREATE**: scouts → author → write `<doc>` → Goldfish Gate (unless `GOLDFISH_AFTER_CREATE == false`)
- **UPDATE**: scouts → author (drift pass) → write `<doc>` → Goldfish Gate
- **REGENERATE**: scouts → author (rebuild) → write `<doc>` → Goldfish Gate
- **FEEDBACK**: scout E + scouts A–D → author (targeted patch) → write `<doc>` → Step 7
- **CHECK**: two haiku agents (citation checker + structure scanner) → merged report → Step 7 (no author, no writes, no gate)
- **VALIDATE**: skip Steps 2–3 → go directly to Goldfish Gate → Step 7

---

## The Goldfish Gate  (VALIDATE mode; also runs after CREATE, UPDATE, and REGENERATE)

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

1. `iter++`; print `🐟 goldfish pass <iter> (judge: agy/$AGY_MODEL${OLLAMA_MODEL:+ + ollama/$OLLAMA_MODEL})`.
2. **Judge** (Bash). The helper is fail-closed: empty output or a missing verdict from any judge is
   an error, not a pass. When `OLLAMA_MODEL` is set, both judges must agree READY.
   ```bash
   REPORT_OUT="$RUNDIR/judge-<iter>.md" AGY_MODEL="$AGY_MODEL" OLLAMA_MODEL="${OLLAMA_MODEL:-}" \
     bash "$GOLDFISH_JUDGE" "<doc>"; RC=$?
   ```
3. **Branch on `RC`:**
   - `RC == 0` → **READY.** Print `✓ goldfish PASS after <iter> pass(es)`. Stop the loop; go to
     Step 7. Drift is a *separate* question — if they want prose-vs-code sync, point them at
     `/elephant-goldfish:elephant update`. Do **not** silently run UPDATE here.
   - `RC == 2` → **judge error / empty / no verdict.** **Abort to a human.** Do not loop, do not
     treat as a pass. Print the helper's message and `"$RUNDIR/judge-<iter>.md"`. Stop.
   - `RC == 10` → **NOT READY.** The file `"$RUNDIR/judge-<iter>.md"` is the goldfish failure
     report. Run the **FEEDBACK path**: execute Steps 2–4 with `MODE = FEEDBACK` and `$ARGUMENTS`
     = the verbatim contents of that report. Scout E targets exactly the named gaps; the Opus
     author patches **only** those gaps, improving the prose of the relevant sections in place.
     The main session **Writes** `<doc>`. Then, if in a repo, commit `<doc>`. Snapshot:
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

## CHECK shortcut — two haiku agents in parallel

**CHECK mode bypasses Steps 2 and 3 entirely.** It covers two distinct failure modes that
require different agents:

| Agent | Catches |
|---|---|
| Citation checker | `doc says X / code now does Y` — stale or wrong claims |
| Structure scanner | `code has X / doc never mentions it` — undocumented additions |

Launch **both agents in a single message** (one tool call block) so they run concurrently.
Both use `model: haiku`. Give each agent the full `elephant.md` contents and the relevant
directive from Step 4.

**Agent 1 — Citation checker**
Reads only the files that `elephant.md` explicitly references (`path` or `path:line`
citations), verifies each specific claim against what those files actually contain, and
returns `CHECK_CITATIONS: PASS` or `CHECK_CITATIONS: FAIL <bulleted list>`.

**Agent 2 — Structure scanner**
Enumerates the actual repo structure using fast shell commands — does not read file contents.
Compares what exists against what the doc names, and returns `CHECK_STRUCTURE: PASS` or
`CHECK_STRUCTURE: FAIL <bulleted list>`. The scanner discovers the repo's significant units
(top-level source/module dirs, entry points, command/script/config files — whatever the doc's
`## Technical Plan` and `## Detailed Implementation` claim to enumerate) and flags any that the
doc never names. Use `git ls-files` for a fast, ignore-aware listing, e.g.:

```bash
git -C <root> ls-files                       # everything tracked
git -C <root> ls-files | grep -E '<pattern>' # narrow to the units the doc enumerates
```

Tailor `<pattern>` to whatever the doc claims to cover (e.g. command files, plugins, packages,
services). The goal is to catch real additions the doc missed, not to flag every file.

Collect both responses. Combine into a single result:
- Both PASS → `ELEPHANT_CHECK: PASS`
- Either FAIL → `ELEPHANT_CHECK: FAIL` with all bullets merged into one report

The main session reads the combined result and proceeds to Step 7. **Do not write `<doc>`.**

> Total cost: 2 haiku agents (run in parallel). Skip Steps 2 and 3 entirely for CHECK.

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
- CREATE / REGENERATE: use Write to overwrite `<doc>`, then enter the Goldfish Gate
- UPDATE / FEEDBACK: use Write to apply the author's output to `<doc>`; UPDATE then enters the Goldfish Gate
- VALIDATE: **no author step** — skip Steps 2–3 entirely and go directly to the Goldfish Gate
- CHECK: **does not reach this step** — handled entirely by the CHECK shortcut above Step 2

---

## Step 4 — Per-mode directives for the author

Include the relevant directive in the author's prompt verbatim.

### CREATE
No prior doc exists. Write a fresh `elephant.md` from the discovery bundle using the section
template. All four canonical sections must be populated. No `## Drift` section (nothing yet to
diverge from). Mark any inferred-but-unconfirmed rationale as `_(inferred from code — confirm)_`.
After writing, control returns to the **Goldfish Gate** to validate the new doc (unless
`GOLDFISH_AFTER_CREATE == false`).

### UPDATE
**Treat the existing doc as authoritative.** "Design is the new code" — the doc says what the
project *should* be. Update prose to match current reality where the code confirms the design.
Add or refresh a `## Drift` callout listing every place code has diverged from the documented
design as: `doc says X / code does Y (path:line)`. If no drift: `No drift detected as of <date>`.
**Never silently discard human-written rationale or the `## Alternatives` section.** Preserve
them; only append or correct. Mark superseded rationale as superseded with a reason — do not delete.
After the main session writes the updated doc, control passes to the **Goldfish Gate** to validate it.

### CHECK (Citation checker — Agent 1)
**Read-only. Do not modify any file.** You are a haiku-tier citation checker.

Read only the files that `elephant.md` explicitly references (by `path` or `path:line`
citations). For each cited claim, verify it against what that file actually contains at that
location. Return exactly one of:

```
CHECK_CITATIONS: PASS
```
```
CHECK_CITATIONS: FAIL
- doc says X / code does Y (path:line)
- doc says X / code does Y (path:line)
```

No other prose. Bullets must be specific enough that Scout E can locate and fix each one.

### CHECK (Structure scanner — Agent 2)
**Read-only. Do not modify any file.** You are a haiku-tier structure scanner.

Use fast shell enumeration (no file content reads) to list what actually exists in the repo:
all plugin directories, command files (`commands/*.md`), and scripts (`scripts/*.sh`). Compare
against what `elephant.md` names. Flag anything that exists in the code but is not mentioned
in the doc. Return exactly one of:

```
CHECK_STRUCTURE: PASS
```
```
CHECK_STRUCTURE: FAIL
- code has X / doc never mentions it (path)
- code has X / doc never mentions it (path)
```

No other prose.

### CHECK (Main session — combining results)
After both agents return, the main session combines their outputs:

- Both PASS → `ELEPHANT_CHECK: PASS` → print `✓ elephant.md is current`, proceed to Step 7.
- Either FAIL → `ELEPHANT_CHECK: FAIL` → merge all bullets into one report, print between
  delimiters, signal failure, proceed to Step 7. **Do not write `<doc>`.**

```
--- ELEPHANT CHECK REPORT (paste to /elephant-goldfish:elephant to fix) ---
<all FAIL bullets merged, citations first then structure gaps>
--- END REPORT ---
```

### REGENERATE
Rebuild `## The Problem`, `## The Technical Plan`, and `## Detailed Implementation` from code
analysis (overwrite). **But fold back still-valid human rationale and `## Alternatives` from the
old doc.** This is a rebuild, not a wipe. Pass the old doc contents to the author so it can diff
intent, not just text.
After the main session writes the regenerated doc, control passes to the **Goldfish Gate** to validate it.

### FEEDBACK
`$ARGUMENTS` (or, inside the Goldfish Gate, the judge's failure report) is a goldfish's failure
report. Patch the doc to close **exactly those gaps** — do not rewrite unrelated sections. Improve
the prose of each affected section in place so a cold reader naturally has what they need; the doc
should read as though the gap never existed, not as a log of patches applied to it.
Ground every patch in the targeted scout's findings (Scout E), not guesswork.

---

## Step 5 — Section template

The author emits `elephant.md` with this structure. The four canonical sections are **always**
present. `## Drift` is mode-conditional (UPDATE only). `## Open questions` is the honesty
pressure-valve — anything unverified goes there, not into the authoritative body.

```markdown
# <Project name> — Elephant (design of record)
<!-- Authoritative design doc. "Design is the new code." A zero-context session re-bootstraps
     from this file alone. Last updated: <date> against <git sha or "pre-git">. -->

## The Problem
<!-- Business/product context: what this exists to solve, for whom, and why.
     The "why", not the "how". -->

## The Technical Plan
<!-- Component architecture + relationships. Each component cites the path that owns it.
     Include a component list with one-line roles and data flow notes. -->

## Alternatives
<!-- Approaches CONSIDERED AND REJECTED, each with the reasoning. Human rationale is
     preserved verbatim across updates; mark superseded items as superseded, never delete. -->

## Detailed Implementation
<!-- Step-by-step with explicit file listings. Key files/dirs, one-line roles, build/test/run
     commands, entry points, external integrations. The map a goldfish follows to find the code. -->

## Drift
<!-- UPDATE only. "doc says X / code does Y (path:line)" for each divergence.
     "No drift detected as of <date>." if clean. Omit section in CREATE/REGENERATE/FEEDBACK/CHECK. -->

## Open questions / unverified
<!-- Anything the scouts could not confirm. Kept OUT of the authoritative body above.
     "None." if scouts resolved everything. -->

---
*Refresh: `/elephant-goldfish:elephant` (interactive menu, or create if no doc) ·
`/elephant-goldfish:elephant update` (drift pass → goldfish gate) · `/elephant-goldfish:elephant check` (read-only drift report) ·
`/elephant-goldfish:elephant validate` (goldfish gate only) · `/elephant-goldfish:elephant regenerate` (rebuild from code → goldfish gate) ·
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
🐘 elephant.md <created|updated|regenerated|patched|validated|checked> · <doc>
Mode: <MODE>   Scouts: <N> haiku (n/a for VALIDATE)   Author: opus (n/a for VALIDATE/CHECK)
Judge: agy/<AGY_MODEL> (n/a for CHECK)
Goldfish: <PASS after N pass(es) | ABORTED: stalled | ABORTED: max iters | ABORTED: judge error | n/a>
Drift items flagged: <N>     (UPDATE / CHECK only)
Check result: <PASS | FAIL — N drift items> (CHECK only — no writes; exit non-zero on FAIL)
Gaps closed this run: <N>    (VALIDATE/UPDATE/REGENERATE/CREATE only)
Sections: Problem · Technical Plan · Alternatives · Detailed Implementation [· Drift]
Run dir: <.goldfish-runs/... | n/a>
```

Then one reminder line: if `elephant.md` is in a tracked repo, commit it — it is a first-class
deliverable alongside the code, not a generated artefact to gitignore. If the gate aborted, the
doc still has open gaps; the run dir holds the last judge report. On a CHECK FAIL, no file was
written — paste the printed report to `/elephant-goldfish:elephant <report>` to trigger a fix.

If a `.goldfish-runs/` run dir was created and the repo's `.gitignore` doesn't already exclude it,
print one more reminder line: add `.goldfish-runs/` to `.gitignore` — these are per-run judge
artefacts, not deliverables to commit.
