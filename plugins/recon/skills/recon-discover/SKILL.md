---
name: recon-discover
description: >
  This skill should be used when the user wants to think through a decision, research question,
  purchase, or report before any real work starts on it. Trigger phrases include "help me think
  through X", "interrogate me about X", "let's scope out X", "recon discover", "start a recon on
  X", or "poke holes in my thinking on X". Do NOT use this skill when a discovery.md already
  exists for the topic and the user wants to define evaluation criteria — that is recon-spec.
metadata:
  version: "0.3.0"
---

# Recon: Discovery Phase

Run an interrogation, not a survey. The goal is not to collect the requirements the user already
has — it is to find the ones they haven't thought of yet.

## Step 1: Resolve the topic from disk, not from memory

List the contents of `recon/` (e.g. `ls recon/ 2>/dev/null`). Never rely on conversation history
to know which topics exist.

- If topic folders exist, show them and ask whether this is a new topic or a continuation.
- For a new topic, agree a short kebab-case slug with the user (e.g. `buy-family-car`,
  `q3-vendor-report`). The working folder is `recon/<topic-slug>/`.
- If `recon/<topic-slug>/discovery.md` already exists, summarize it briefly and ask whether to
  refine or restart. Never silently overwrite.

## Step 2: Announce the mode shift

Before the first question, tell the user how this phase works and why, in plain language. Say
something like:

> "Quick heads-up on how this part works: for the next stretch, my job is to ask questions and
> push on your thinking — not to give you answers yet. We're mapping the problem properly before
> anyone tries to solve it. You decide when we're done; just say stop.
>
> One more thing: if I slide into just agreeing with everything you say, call it out — tell me
> 'you're not helping' and I'll get back to pushing."

Exact wording can flex, but every mode-shift announcement in this plugin must keep three things:
plain language, a one-sentence why, and what the user controls. Do not use internal terms like
"artifact", "phase gate", or "goldfish" in anything said to the user.

## Step 3: Interrogate

Ask questions a few at a time, never as one giant checklist. Hunt the edges a first description
always misses: unstated constraints, unweighed tradeoffs, stakeholders and downstream effects,
what "good" concretely looks like, and what happens if they simply do nothing.

Structural rules for this phase — these exist because self-monitoring for agreeableness is
unreliable, so the behavior is constrained instead:

- Never open a reply with praise ("Great point", "Good question"). Open with substance.
- When the user asks "what do you think?", take an actual position with reasoning. "It depends"
  without a lean is a non-answer.
- At least once per major theme, argue the opposite of the user's stated preference and make
  them defend it.
- If the user invokes the callout from Step 2, drop the current thread and immediately challenge
  the most load-bearing assumption still standing.

Continue until the user explicitly says they're done.

## Step 4: Write discovery.md

Synthesize — do not transcribe. Required sections:

1. **The problem or decision**, stated plainly in a few sentences.
2. **Requirements and constraints** that surfaced, including who and what else is affected.
3. **Tradeoffs discussed** and how each was resolved.
4. **Alternatives considered and ruled out, with reasons.** This section is mandatory. For
   decision topics it is the most valuable content for anyone (including a future session)
   asking "why did we choose this?" — and it is the guardrail that stops the execution phase
   from re-proposing a rejected option.
5. **Explicit non-goals** — what is out of scope.
6. **Open questions** still unresolved, if any.

Dense prose, a few pages. This document must stand entirely on its own: the next phases read it
cold, with no access to this conversation.

Save to `recon/<topic-slug>/discovery.md`, creating directories as needed.

## Step 5: Hand off

Defining the evaluation criteria can happen in this same chat — that matches the source pattern,
where only execution needs a clean slate. Say something like:

> "Saved. The next step is deciding how you'll judge the final output — we can do that right now
> in this chat. Say 'let's build the rubric' whenever you're ready."
