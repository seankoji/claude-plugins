---
name: recon-spec
description: >
  This skill should be used after discovery.md exists for a topic and the user wants to define
  how the eventual output will be judged. Trigger phrases include "let's build the rubric",
  "define the acceptance criteria for X", "how will we know the output on X is good", "recon
  spec". Do NOT use this skill if discovery.md is missing for the topic — direct the user to
  recon-discover first.
metadata:
  version: "0.3.0"
---

# Recon: Spec Phase

The output of this phase is a grading rubric a stranger could apply, not a wishlist.

## Step 1: Resolve topic and load context

List `recon/` and confirm the topic slug with the user — even if the topic was just discussed in
this chat, confirm against what is actually on disk.

Read `recon/<topic-slug>/discovery.md` in full. If it does not exist, stop and say something
like:

> "There's no discovery brief saved for this topic yet, and the rubric has to be built against
> one — otherwise we'd be defining pass/fail for work we haven't scoped. Say 'help me think
> through <topic>' and we'll build the brief first."

## Step 2: Announce the mode shift with the one-shot framing

This framing device is what forces criteria to become concrete. Use it, near-verbatim:

> "New role for this part. Imagine you're about to hand this brief to a researcher you've never
> worked with. They get exactly one shot, and you don't get to answer follow-up questions.
> I'm going to play the skeptic who has to judge their finished work cold — and press you on
> what would need to be true for you to actually trust it."

## Step 3: Interview for checkable criteria

Cover, going back and forth as long as needed:

- What evidence, sourcing, or citations must be present, and of what kind?
- What structure or sections are non-negotiable?
- What would make the user reject the output on sight?
- What separates a genuinely expert version from a plausible-sounding mediocre one?
- What must the output NOT do — especially anything that violates a constraint or re-proposes a
  ruled-out alternative from discovery.md?

Refuse vague answers. Every criterion must be checkable by a cold reader as pass/fail. When the
user gives a vague criterion, show them the upgrade. Model example to reuse:

- Vague (reject): "Cite reputable sources."
- Checkable (accept): "Every factual claim that influences the recommendation cites at least one
  primary source — manufacturer spec sheet, government dataset, or peer-reviewed study — with a
  working URL. Blog posts and forums may add color but cannot be the sole support for any
  claim."

## Step 4: Write spec.md

Structure:

1. **Pass/fail criteria** — itemized, each one checkable cold, each referencing the relevant
   part of discovery.md where that helps ("per the budget constraint in discovery.md, no
   recommended option above the stated ceiling").
2. **Auto-reject conditions** — a short list of things that fail the entire deliverable
   immediately, regardless of other merits (e.g. recommends a ruled-out alternative, missing a
   non-negotiable section, any load-bearing claim with no source).
3. **Quality bar** — what distinguishes expert from mediocre, phrased as concretely as the
   interview allows.

Save to `recon/<topic-slug>/spec.md`.

## Step 5: Stop and let the user pick a path

Both documents are saved. Do not default to any one path and do not keep talking past this
point — stop and let the user choose. If a structured choice tool is available in this
environment (e.g. AskUserQuestion), use it; otherwise present the three options as a numbered
list in chat and wait for a reply. Say something like:

> "Rubric saved for `<topic-slug>`. Three ways to take it from here — pick whichever fits:
>
> 1. **Fresh chat (recommended).** Best results — the next phase gets an honest cold read
>    instead of one shaped by our back-and-forth. I'll hand you a paste-ready block; open a
>    new chat and paste it as your first message.
> 2. **Continue right here.** Faster, no context switch — but it skips the independent check,
>    so save this for small or low-stakes topics. I'll say so plainly in the saved files, not
>    just to you.
> 3. **Hand off to `/imps:imps`.** For a larger implementation task in Claude Code — I'll give
>    you a ready-to-run task description pointing at these two files."

Do not proceed down any path until the user answers. Do not offer to blend paths (e.g. "I'll
just start now and you can verify later") — that reintroduces exactly the silent-gap failure
mode this plugin exists to prevent.

### Path 1: Fresh chat

Build one fenced block containing, in order: the line `verify <topic-slug>`, then a
`--- recon/<topic-slug>/discovery.md ---` divider followed by that file's full contents, then a
`--- recon/<topic-slug>/spec.md ---` divider followed by that file's full contents. Give the user
this block to copy and say to paste it as the very first message in a new chat. Pasting the
finished artifacts is not contamination — it is the same input recon-verify would read off disk;
what invalidates a cold read is the back-and-forth that produced them, which a new chat never
sees.

### Path 2: Continue right here

State the tradeoff before doing anything else, near-verbatim:

> "Skipping the fresh-chat isolation, so this run won't get an independent cold read or
> verification pass — I'll grade my own work against the rubric instead of a stranger doing it.
> That's a reasonable trade for something small; for anything you'd actually rely on, the fresh
> chat is stronger."

Then, in this same conversation:

1. Write `recon/<topic-slug>/verify.md` with first line `status: SKIPPED`, then
   `skipped_at: <ISO-8601 timestamp>` and one line of reason: same-session convenience mode,
   chosen by the user over independent verification. This is not a shortcut around the record —
   it keeps the folder honest about what did and didn't happen, and it means a later attempt to
   run recon-execute in a genuinely fresh chat correctly refuses (no PASS on file) instead of
   silently trusting a check that never happened.
2. Follow recon-execute's Step 3 (read the brief and surface it), Step 4 (do the actual work),
   Step 5 using its **self-assessed** branch only — never present this run's grade as
   independent — and Step 6 (present). Skip recon-execute's Steps 1 and 2: the contamination
   check is moot (this is a disclosed, deliberate choice, not silent contamination) and the
   artifact gate is moot (you already know discovery.md and spec.md exist — you just wrote
   spec.md).

### Path 3: Hand off to /imps:imps

Give the user this task description to run as `/imps:imps <text>` (Claude Code only — note that
if the user isn't in Claude Code or doesn't have the imps plugin installed, this path doesn't
apply and they should pick one of the other two):

> Read `recon/<topic-slug>/discovery.md` and `recon/<topic-slug>/spec.md` in full, then implement
> the deliverable they describe. Treat spec.md's pass/fail criteria and auto-reject conditions as
> hard requirements. Do not re-propose any alternative discovery.md rules out.

Mention, once, that an independent verify pass first (fresh chat, "verify `<topic-slug>`")
produces a stronger brief for imps to work from — but don't gate the handoff on it; the user
picked this path knowing the tradeoff.
