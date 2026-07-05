# ape

Imitation is the sincerest form of engineering. Apes techniques from open-source GitHub repos into your codebase: parallel gibbon discovery → metadata gate → shallow clones → parallel orangutan deep analysis → silverback synthesis.

## Components

| Component | File | Purpose |
|-----------|------|---------|
| Command | `commands/forage.md` | `/ape:forage [focus]` — full orchestration |
| Command | `commands/clean.md` | `/ape:clean [--all]` — sanctioned deletion of clones (keeps reports) |
| Agent | `agents/gibbon-scout.md` | haiku, `Bash` only — gh search + metadata triage, one axis each, hard 5-search budget. Brachiates fast across many candidates, never stops to read code. |
| Agent | `agents/orangutan-analyst.md` | sonnet, `Read/Grep/Glob/Bash/Write` — one repo each, budgeted read order, ≤400-word report to disk, 3-line return. Sits alone with one repo until it really understands it. |
| Agent | `agents/silverback-synthesist.md` | opus, `Read/Glob/Write` — reads every report plus the fingerprint itself, writes the ranked `RECOMMENDATIONS.md`, and returns only the top picks. The troop leader everyone reports back to. |

## Install

Drop this directory into your plugin marketplace repo and add an entry:

```json
{ "name": "ape", "source": "./ape", "description": "Forage OSS repos for transferable techniques" }
```

## Usage

```
/ape:forage testing        # focus the run
/ape:forage                # broad: architecture, testing, DX
/ape:clean               # delete clones, keep fingerprint + reports
/ape:clean --all         # full wipe
```

All artifacts land in `~/tmp/repo-research/<project-dir-name>/`:
`fingerprint.md` (cached ≤30 days), `candidates.md`, `repos/`, `reports/*.md`, `RECOMMENDATIONS.md`. Reports persisting on disk means you can re-run synthesis, or argue with a ranking, without re-foraging.

## Design rationale

- **Model inversion**: discovery is mechanical (queries + metadata) → haiku; analysis is where value is generated (extracting non-obvious transferable patterns from unfamiliar code) → sonnet. This costs more than haiku-analysis in absolute dollars because analysis is where the tokens flow — deliberately.
- **Fingerprint once, inject everywhere**: subagents don't inherit parent context; without this, N agents each re-characterise the project inconsistently. The already-in-use list stops agents recommending what you already have. The fingerprint is shown to you before dispatch because a wrong fingerprint produces convergent garbage at scale.
- **Axis-split discovery**: identical fan-out prompts converge on the same top-starred repos. Three gibbons with orthogonal axes buys coverage, not duplication.
- **Context hygiene**: orangutans write full reports to disk and return three lines. Eight analysts returning prose would blow the orchestrator's synthesis budget. Synthesis carries this all the way through: the silverback reads every report itself and hands the orchestrator only a finished top-2–3 pitch — the orchestrator's context never absorbs the ~3,000+ words of raw report bodies that reading eight reports directly would cost.
- **Parallelism spent where it pays**: one analyst per repo, all dispatched in one message. Claude Code runs parallel tasks up to a cap (~10 in current builds; extras queue), so waves are sized to fit.
- **Opus for synthesis, not analysis**: the silverback is the one place a wrong call is expensive — it's the last filter before a recommendation reaches the user, weighing convergent/conflicting analyst findings against the fingerprint in one shot with no chance to course-correct downstream. That judgment call gets the strongest model in the pipeline.

## Known wrinkles

- The backgrounded multi-clone is a compound bash command; depending on your permission settings the `Bash(git clone:*)` matcher may still prompt once. Approve it — or pre-allow in project settings.
- `disable-model-invocation` is a recent command frontmatter key (keeps Claude from auto-firing a 10-agent burn mid-session via the SlashCommand tool). Unknown keys are ignored, so it's harmless on older builds.
- GitHub's search API budget (~30 req/min) is shared across all three scouts; each is capped at 5 searches and told to back off on 403 rather than hammer.
- Cowork's plugin tooling treats `commands/` as legacy in favour of `skills/*/SKILL.md`. For Claude Code orchestration with `$ARGUMENTS`, a command is still the right shape; if you ever port this to Cowork, move the `forage.md` body into a SKILL.md.
- Discovery decays: rerunning next quarter tends to resurface the same repos. The durable asset is the analyst + fingerprint pattern; refresh axis C's curated sources rather than adding scouts.
