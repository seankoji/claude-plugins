'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const SCRIPT_PATH = path.join(__dirname, '..', '..', 'plugins', 'imps', 'scripts', 'imps-run.workflow.js')

// imps-run.workflow.js is not a requirable module — it's evaluated by the Workflow
// tool's own runtime, which injects agent()/parallel()/phase()/args as ambient
// bindings and permits top-level await/return in the script body (see the file's
// own header comment). To unit-test its plain-JS logic (stageTasks/runDispatch),
// load everything up to the "Main" section — schemas + function declarations only
// — into a Function constructed with those same ambient names as parameters,
// stubbed per test. The Main section (which actually drives a run end to end) is
// never evaluated here.
function loadWorkflowFunctions({ agent, parallel, phase, args, log }) {
  const source = fs.readFileSync(SCRIPT_PATH, 'utf8')
  const mainMarker = source.indexOf("\nphase('Preflight')")
  assert.ok(mainMarker !== -1, 'expected to find the Main section marker — has imps-run.workflow.js been restructured?')
  const body = source.slice(0, mainMarker).replace('export const meta', 'const meta')
  const factory = new Function(
    'agent',
    'parallel',
    'phase',
    'args',
    'log',
    `${body}\nreturn { runDispatch, stageTasks, dispatchImp, parseTaskDecision, parseGateDecision, validateStateRead, nowIso, fixLoopRound, adjudicateFindings, writeParkedFindings, constraintsPointer, constraintsPointerForReviewer, resolvePolicy, sameRevision, acceptanceFailures, validManifest, reviewPassed, verifyForPublish, drivePrAndClose, ocrReview, ocrPreflight, fixOcrReview, personaReview, fixGate, finalizeRun, runGate, runGatesWithRetry }`
  )
  return factory(agent, parallel, phase || (() => {}), args || {}, log || (() => {}))
}

// Mirrors the real Workflow tool's parallel(): each thunk runs independently; one
// that throws resolves to null in the results array instead of rejecting the batch.
async function parallel(thunks) {
  const settled = await Promise.allSettled(thunks.map((fn) => fn()))
  return settled.map((s) => (s.status === 'fulfilled' ? s.value : null))
}

function task(id, overrides = {}) {
  return { id, label: `task #${id}`, model: 'sonnet', type: 'code', deps: [], ...overrides }
}

function baseState(tasks) {
  return { tasks, tasks_done: [], failed_tasks: [], worktrees: {}, artifacts: [] }
}

test('runDispatch records a parallel()-dropped dispatch as failed instead of losing it', async () => {
  async function agent(prompt, opts) {
    if (opts.label === 'imp-1') return { status: 'done', branch: 'br-1', artifacts: [] }
    if (opts.label === 'imp-2') throw new Error('simulated worktree-creation contention')
    if (opts.label === 'imp-3') return { status: 'done', branch: 'br-3', artifacts: [] }
    return {} // patchState's heartbeat call
  }
  const { runDispatch } = loadWorkflowFunctions({ agent, parallel })

  const outcome = await runDispatch(baseState([task(1), task(2), task(3)]))

  assert.equal(outcome.blocked, false)
  assert.deepEqual([...outcome.doneIds].sort(), [1, 3])
  const failedIds = outcome.failed.map((f) => f.id).sort()
  assert.deepEqual(failedIds, [2], 'the errored task must show up in failed_tasks, not vanish')
  const task2 = outcome.failed.find((f) => f.id === 2)
  assert.equal(task2.notes, 'agent call errored (dropped by parallel())')
  assert.deepEqual(outcome.worktrees, { 1: 'br-1', 3: 'br-3' })
})

test('a dependent task is never dispatched once its dependency is dropped by parallel()', async () => {
  const calls = []
  async function agent(prompt, opts) {
    calls.push(opts.label)
    if (opts.label === 'imp-1') return { status: 'done', branch: 'br-1', artifacts: [] }
    if (opts.label === 'imp-2') throw new Error('simulated worktree-creation contention')
    if (opts.label === 'imp-4') return { status: 'done', branch: 'br-4', artifacts: [] }
    return {}
  }
  const { runDispatch } = loadWorkflowFunctions({ agent, parallel })

  const outcome = await runDispatch(baseState([task(1), task(2), task(4, { deps: [2] })]))

  assert.ok(!calls.includes('imp-4'), 'task 4 depends on task 2, which errored — it must never be dispatched')
  const task4 = outcome.failed.find((f) => f.id === 4)
  assert.ok(task4, 'task 4 must be recorded as failed via dependency cascade')
  assert.equal(task4.notes, 'dependency failed')
})

test('an explicit status:"failed" result is still recorded the same way as before the fix', async () => {
  async function agent(prompt, opts) {
    if (opts.label === 'imp-1') return { status: 'failed', notes: 'lint errors', branch: null, artifacts: [] }
    return {}
  }
  const { runDispatch } = loadWorkflowFunctions({ agent, parallel })

  const outcome = await runDispatch(baseState([task(1)]))

  assert.deepEqual(outcome.failed.map((f) => f.id), [1])
  assert.equal(outcome.failed[0].notes, 'lint errors')
})

test('validateStateRead passes when readState() agrees with the raw file (#87)', async () => {
  const { validateStateRead } = loadWorkflowFunctions({ agent: async () => ({}), parallel })
  const state = { tasks: [task(1), task(2)], phase: 'dispatch_pending' }
  const rawCheck = { raw_task_count: 2, raw_phase: 'dispatch_pending', raw_error: null }

  assert.deepEqual(validateStateRead(state, rawCheck), { ok: true, error: null })
})

test('validateStateRead blocks when readState() mismaps tasks to [] (#87 reproduction)', async () => {
  const { validateStateRead } = loadWorkflowFunctions({ agent: async () => ({}), parallel })
  // Mirrors the observed failure: haiku nested real content under last_result and
  // defaulted top-level tasks to [] / phase to "complete" while the raw file still has
  // 8 tasks and phase "dispatch_pending".
  const state = { tasks: [], phase: 'complete', task: 'Read JSON from state file' }
  const rawCheck = { raw_task_count: 8, raw_phase: 'dispatch_pending', raw_error: null }

  const result = validateStateRead(state, rawCheck)
  assert.equal(result.ok, false)
  assert.match(result.error, /returned 0 task\(s\) but the raw file has 8/)
  assert.match(result.error, /#87/)
})

test('an unreadable autonomy policy is never read as consent', async () => {
  // Phase 1 Step 7 settles endstate / plan_review / learnings_policy. A state file that
  // omits one, or carries a value this version does not know, must fall back to the
  // conservative choice — not to merging, not to skipping plan review, and not to writing
  // the shared learnings log unasked.
  const { resolvePolicy: resolve } = loadWorkflowFunctions({ agent: async () => ({}), parallel })

  for (const bad of [undefined, null, '', 'yes', 'MERGE', 'auto ', 7, {}]) {
    assert.equal(resolve(bad, ['pr', 'merge', 'release'], 'pr'), 'pr')
    assert.equal(resolve(bad, ['ask', 'on_objection'], 'ask'), 'ask')
    assert.equal(resolve(bad, ['auto', 'none', 'ask'], 'ask'), 'ask')
  }
  // And the recognized values still resolve to themselves.
  assert.equal(resolve('release', ['pr', 'merge', 'release'], 'pr'), 'release')
  assert.equal(resolve('auto', ['auto', 'none', 'ask'], 'ask'), 'auto')
})

test('validateStateRead blocks when a task spec comes back truncated', async () => {
  const { validateStateRead } = loadWorkflowFunctions({ agent: async () => ({}), parallel })
  // The observed failure: the task list and phase are intact, so the count check passes,
  // but the imp would be dispatched on the first line of a multi-KB spec.
  const state = { tasks: [task(1, { spec: 'Read the goal file.' })], phase: 'dispatch_pending' }
  const rawCheck = {
    raw_task_count: 1,
    raw_phase: 'dispatch_pending',
    raw_spec_lengths: [4200],
    raw_error: null,
  }

  const result = validateStateRead(state, rawCheck)
  assert.equal(result.ok, false)
  assert.match(result.error, /spec for task #1/)
  assert.match(result.error, /truncated/)
})

test('validateStateRead tolerates whitespace-scale spec length drift', async () => {
  const { validateStateRead } = loadWorkflowFunctions({ agent: async () => ({}), parallel })
  const spec = 'x'.repeat(1000)
  const state = { tasks: [task(1, { spec })], phase: 'dispatch_pending' }
  const rawCheck = {
    raw_task_count: 1,
    raw_phase: 'dispatch_pending',
    raw_spec_lengths: [1005],
    raw_error: null,
  }

  assert.deepEqual(validateStateRead(state, rawCheck), { ok: true, error: null })
})

test('validateStateRead skips the spec check when raw_spec_lengths is absent', async () => {
  const { validateStateRead } = loadWorkflowFunctions({ agent: async () => ({}), parallel })
  // Legacy invocation shape, or a jq run that failed: nothing to compare against, so the
  // guard must not block a run it cannot evaluate.
  const state = { tasks: [task(1, { spec: 'short' })], phase: 'dispatch_pending' }
  const rawCheck = { raw_task_count: 1, raw_phase: 'dispatch_pending', raw_error: null }

  assert.deepEqual(validateStateRead(state, rawCheck), { ok: true, error: null })
})

test('validateStateRead blocks on a phase mismatch even when task counts agree', async () => {
  const { validateStateRead } = loadWorkflowFunctions({ agent: async () => ({}), parallel })
  const state = { tasks: [task(1)], phase: 'complete' }
  const rawCheck = { raw_task_count: 1, raw_phase: 'dispatch_pending', raw_error: null }

  const result = validateStateRead(state, rawCheck)
  assert.equal(result.ok, false)
  assert.match(result.error, /phase/)
})

test('validateStateRead surfaces a fatal readState() error field instead of proceeding', async () => {
  const { validateStateRead } = loadWorkflowFunctions({ agent: async () => ({}), parallel })
  const state = { tasks: [], phase: null, error: 'file is not valid JSON' }
  const rawCheck = { raw_task_count: -1, raw_phase: '', raw_error: 'jq: parse error' }

  const result = validateStateRead(state, rawCheck)
  assert.equal(result.ok, false)
  assert.match(result.error, /fatal error/)
})

// parseTaskDecision/parseGateDecision are pure string parsers — no agent() calls inside
// them, so the stub agent below is never invoked; it only satisfies loadWorkflowFunctions'
// factory signature.
const noopAgent = async () => ({})

test('parseTaskDecision parses valid retry and skip decisions', () => {
  const { parseTaskDecision } = loadWorkflowFunctions({ agent: noopAgent, parallel })

  assert.deepEqual(parseTaskDecision('retry tasks #1,#2: fix the flaky test'), {
    kind: 'retry',
    ids: [1, 2],
    guidance: 'fix the flaky test',
  })
  assert.deepEqual(parseTaskDecision('skip tasks #4,#5'), { kind: 'skip', ids: [4, 5] })
})

test('parseTaskDecision is case-insensitive on the retry/skip keyword', () => {
  const { parseTaskDecision } = loadWorkflowFunctions({ agent: noopAgent, parallel })

  assert.deepEqual(parseTaskDecision('RETRY TASKS #1: bump the timeout'), {
    kind: 'retry',
    ids: [1],
    guidance: 'bump the timeout',
  })
  assert.deepEqual(parseTaskDecision('SKIP TASKS #3'), { kind: 'skip', ids: [3] })
})

test('parseTaskDecision tolerates whitespace around ids and guidance', () => {
  const { parseTaskDecision } = loadWorkflowFunctions({ agent: noopAgent, parallel })

  assert.deepEqual(parseTaskDecision('retry tasks #1, #2 :   extra spaces guidance  '), {
    kind: 'retry',
    ids: [1, 2],
    guidance: 'extra spaces guidance',
  })
})

test('parseTaskDecision returns null (not NaN, not a throw) for malformed input', () => {
  const { parseTaskDecision } = loadWorkflowFunctions({ agent: noopAgent, parallel })

  assert.equal(parseTaskDecision('retry tasks #abc: fix it'), null, 'non-numeric ids never match the id character class')
  assert.equal(parseTaskDecision('skip tasks #xyz'), null)
  assert.equal(parseTaskDecision('gibberish decision'), null)
  assert.equal(parseTaskDecision(''), null)
  assert.equal(parseTaskDecision(null), null)
  assert.equal(parseTaskDecision(undefined), null)
})

test('parseGateDecision parses valid retry and skip decisions', () => {
  const { parseGateDecision } = loadWorkflowFunctions({ agent: noopAgent, parallel })

  assert.deepEqual(parseGateDecision('retry lint: fix the eslint config'), {
    kind: 'retry',
    gate: 'lint',
    guidance: 'fix the eslint config',
  })
  assert.deepEqual(parseGateDecision('skip lint'), { kind: 'skip', gate: 'lint', rationale: 'Operator explicitly requested: skip lint' })
})

test('parseGateDecision is case-insensitive on the retry/skip keyword', () => {
  const { parseGateDecision } = loadWorkflowFunctions({ agent: noopAgent, parallel })

  assert.deepEqual(parseGateDecision('RETRY TEST: bump the timeout'), {
    kind: 'retry',
    gate: 'TEST',
    guidance: 'bump the timeout',
  })
  assert.deepEqual(parseGateDecision('SKIP BUILD'), { kind: 'skip', gate: 'BUILD', rationale: 'Operator explicitly requested: SKIP BUILD' })
})

test('parseGateDecision tolerates whitespace around the guidance text', () => {
  const { parseGateDecision } = loadWorkflowFunctions({ agent: noopAgent, parallel })

  assert.deepEqual(parseGateDecision('retry lint:    extra spaces guidance   '), {
    kind: 'retry',
    gate: 'lint',
    guidance: 'extra spaces guidance',
  })
})

// Gate names come from the discovered gate list, not a fixed vocabulary, so real projects
// routinely name them `type-check`, `test-e2e`, `lint:fix`-minus-the-colon and so on. #120
// widened the gate capture from `\w+` to `[^:]+` (retry) and `.+` (skip) and added .trim()
// to match; this test still asserted the old `\w+` behavior and had been failing on master
// since that merge. Non-word characters in a gate name are valid input, not malformed.
test('parseGateDecision accepts gate names that are not bare \\w+', () => {
  const { parseGateDecision } = loadWorkflowFunctions({ agent: noopAgent, parallel })

  assert.deepEqual(parseGateDecision('retry test-fail: guidance'), {
    kind: 'retry',
    gate: 'test-fail',
    guidance: 'guidance',
  })
  assert.deepEqual(parseGateDecision('skip type-check'), { kind: 'skip', gate: 'type-check', rationale: 'Operator explicitly requested: skip type-check' })
  // The gate name itself is trimmed, not just the guidance.
  assert.deepEqual(parseGateDecision('retry   spaced gate  : do the thing'), {
    kind: 'retry',
    gate: 'spaced gate',
    guidance: 'do the thing',
  })
})

test('parseGateDecision returns null (not NaN, not a throw) for malformed input', () => {
  const { parseGateDecision } = loadWorkflowFunctions({ agent: noopAgent, parallel })

  assert.equal(parseGateDecision('retry lint'), null, 'missing colon must not match')
  assert.equal(parseGateDecision('gibberish decision'), null)
  assert.equal(parseGateDecision(''), null)
  assert.equal(parseGateDecision(null), null)
  assert.equal(parseGateDecision(undefined), null)
})

// --- Global Constraints pointer ------------------------------------------------------
// Cross-cutting invariants live in GOAL.md, delivered to every code-writing/reviewing
// agent call BY POINTER — never as text embedded in the state file, which patchState()
// round-trips through haiku and truncates.

const GOAL_ARGS = {
  goalFilePath: '/tmp/imps-runs/some-run.md',
  pluginRoot: '/plugins/imps',
  stateFilePath: '/tmp/imps-runs/some-run.state.json',
  personaPostingProtocolPath: '/plugins/imps/references/persona-posting.md',
  personaBriefPaths: {},
}

// Captures the prompt + options of the single agent() call the function under test makes.
function captureAgent() {
  const calls = []
  const agent = async (prompt, opts) => {
    calls.push({ prompt, opts: opts || {} })
    return {}
  }
  return { agent, calls }
}

test('the constraints pointer names the GOAL.md path and the exact section heading', () => {
  const { constraintsPointer, constraintsPointerForReviewer } = loadWorkflowFunctions({
    agent: noopAgent,
    parallel,
    args: GOAL_ARGS,
  })

  const pointer = constraintsPointer()
  assert.match(pointer, /MANDATORY FIRST ACTION/)
  assert.ok(pointer.includes(GOAL_ARGS.goalFilePath), 'the pointer must carry the real GOAL.md path')
  assert.ok(pointer.includes('"Global Constraints"'), 'the section name is a pinned contract name')
  // The reviewer variant adds the one thing a writer does not need.
  assert.match(constraintsPointerForReviewer(), /MAJOR finding/)
})

test('finalizeRun persists a bounded checkbox-free decision trail in GOAL.md', async () => {
  const { agent, calls } = captureAgent()
  const { finalizeRun } = loadWorkflowFunctions({ agent, parallel, args: GOAL_ARGS })

  await finalizeRun({}, null, [], { tokens_spent: 0, model_counts: {} })

  assert.equal(calls.length, 1)
  const prompt = calls[0].prompt
  assert.match(prompt, /## Decision trail/)
  assert.match(prompt, /next line beginning with "## "/)
  assert.match(prompt, /never emit a second heading/)
  assert.match(prompt, /no checkboxes/)
  assert.match(prompt, /_None\._/)
  assert.match(prompt, /Record only pivots, not routine actions/)
})

test('every Integrate-phase fixer is told to commit its work', async () => {
  const { agent, calls } = captureAgent()
  const wf = loadWorkflowFunctions({ agent, parallel, args: GOAL_ARGS })

  await wf.fixGate({ name: 'lint', cmd: 'npm run lint' }, 'tail', undefined)
  await wf.fixOcrReview([{ severity: 'major', path: 'x.js', line: 1, message: 'breaks zero input' }])
  await wf.fixLoopRound(['a finding'])

  // run-ocr.sh reviews MERGE_BASE..HEAD and `git push` sends commits, so a fixer that
  // leaves work uncommitted is reviewed around and then silently dropped — with every
  // gate still green. fixLoopRound always had this instruction; the other two did not.
  for (const call of calls) {
    assert.match(call.prompt, /commit/i, `${call.opts.label} does not tell its agent to commit`)
  }
})

test('OCR review is a mechanical wrapper and code fixes carry constraints', async () => {
  const { agent, calls } = captureAgent()
  const wf = loadWorkflowFunctions({ agent, parallel, args: GOAL_ARGS })
  const task = { id: 1, label: 'do a thing', type: 'code', model: 'sonnet', deps: [], spec: 'the spec' }
  const brief = { path: '/briefs/sre.md', model: 'sonnet' }

  await wf.dispatchImp(task, { task: 'run goal' }, undefined, false)
  await wf.fixGate({ name: 'lint', cmd: 'npm run lint' }, 'tail', undefined)
  await wf.fixLoopRound(['a finding'])
  await wf.ocrReview('master')
  await wf.fixOcrReview([{ severity: 'major', path: 'x.js', line: 1, message: 'breaks zero input' }])
  await wf.personaReview('sre', brief, 7, 'seankoji/claude-plugins', 'master', 'live')

  assert.equal(calls.length, 6)
  for (const call of calls.filter((c) => c.opts.label !== 'ocr-review')) {
    assert.ok(
      call.prompt.includes('MANDATORY FIRST ACTION') && call.prompt.includes(GOAL_ARGS.goalFilePath),
      `${call.opts.label} lost the Global Constraints pointer`
    )
  }
  // Only the persona reviewer gets the review-specific major-finding escalation. The
  // OCR wrapper receives neither code nor a diff; the helper snapshots it itself.
  const withMajor = calls.filter((c) => /MAJOR finding/.test(c.prompt)).map((c) => c.opts.label)
  assert.deepEqual(withMajor.sort(), ['persona-sre'])

  const wrapper = calls.find((c) => c.opts.label === 'ocr-review')
  assert.ok(wrapper.prompt.includes('Do not read, summarize, review, edit'), 'wrapper must not receive review work')
  assert.ok(wrapper.prompt.includes('run-code-review.sh'), 'wrapper must invoke the dedicated harness')
  assert.ok(wrapper.prompt.includes(GOAL_ARGS.goalFilePath), 'wrapper must pass GOAL.md by path')
})

// --- Fix-round schema ------------------------------------------------------------------

test('fixLoopRound requires a rationale for every WONTFIX instead of discarding it silently', async () => {
  const { agent, calls } = captureAgent()
  const { fixLoopRound } = loadWorkflowFunctions({ agent, parallel, args: GOAL_ARGS })

  await fixLoopRound(['finding one', 'finding two'])

  const schema = calls[0].opts.schema
  assert.ok(schema, 'fixLoopRound was schema-less; its WONTFIX rationale reached nobody')
  assert.deepEqual(schema.required.sort(), ['fixed', 'summary', 'wontfix'])
  const wontfixItem = schema.properties.wontfix.items
  assert.deepEqual(
    wontfixItem.required.sort(),
    ['finding', 'rationale'],
    'a wontfix entry without a rationale is exactly the silent discard this schema exists to block'
  )
  // The findings still reach the prompt verbatim.
  assert.ok(calls[0].prompt.includes('finding two'))
})

// --- Adjudication ----------------------------------------------------------------------

test('the adjudicator can only rule load-bearing against an external anchor', async () => {
  const { agent, calls } = captureAgent()
  const { adjudicateFindings } = loadWorkflowFunctions({ agent, parallel, args: GOAL_ARGS })

  await adjudicateFindings(
    [
      { slug: 'grumpy-engineer', findings: ['the retry bound never increments'] },
      { slug: 'sre', findings: ['the retry bound never increments'] },
    ],
    [{ round: 1, summary: 'renamed a variable', fixed: [] }],
    'master'
  )

  const { prompt, opts } = calls[0]
  assert.equal(opts.model, 'opus', 'adjudication is the run-blocking judgment call — never routed below opus')
  // Anchor (a): a quoted DoD criterion. Anchor (b): a named breaking input. Both, or the
  // ruling may not block: with (a) alone an unanticipated correctness finding would be
  // unblockable by construction, since a DoD enumerates deliverables, not defects.
  assert.ok(prompt.includes('## Definition of Done'), 'anchor (a) must point at the DoD')
  assert.match(prompt, /QUOTE that criterion verbatim/)
  assert.match(prompt, /concrete breaking input/)
  assert.match(prompt, /MUST NOT be "load-bearing"/)
  assert.match(prompt, />=2 DISTINCT personas/)
  // Persona attribution survives into the prompt — the flattened list the fix loop uses
  // would make the >=2-personas rule inapplicable.
  assert.ok(prompt.includes('grumpy-engineer') && prompt.includes('"sre"'))
  // "Reviewed and parked" is not "never reviewed".
  assert.match(prompt, /SKIPPED/)
  // The adjudicator may not hand itself the operator's verb.
  assert.deepEqual(opts.schema.properties.rulings.items.properties.ruling.enum, [
    'parked-contestable',
    'parked-deferred',
    'load-bearing',
  ])
  assert.deepEqual(opts.schema.properties.rulings.items.required.sort(), ['finding', 'rationale', 'ruling'])
})

// --- GOAL.md parked-findings writer -----------------------------------------------------

test('writeParkedFindings replaces one bounded section and never emits a checkbox', async () => {
  const { agent, calls } = captureAgent()
  const { writeParkedFindings } = loadWorkflowFunctions({ agent, parallel, args: GOAL_ARGS })

  await writeParkedFindings([{ finding: 'f', ruling: 'operator-overridden', rationale: 'r' }])

  const { prompt } = calls[0]
  assert.ok(prompt.includes(GOAL_ARGS.goalFilePath))
  assert.ok(prompt.includes('## Parked findings'), 'the heading is a pinned contract name')
  // The boundary rule: one prompt serves a template that places the section LAST and one
  // that places it MID-FILE. A to-EOF implementation would corrupt the mid-file one.
  assert.match(prompt, /next line beginning with "## "/)
  assert.match(prompt, /end-of-file if no further/)
  assert.match(prompt, /REPLACE that body/)
  assert.match(prompt, /_None\._/, 'an empty section must render _None._, not vanish')
  assert.match(prompt, /NO markdown checkboxes/, 'a stray checkbox outside the DoD becomes a phantom task')
  assert.match(prompt, /Do NOT touch the "## Definition of Done"/, 'dodCoverage owns those boxes')
  assert.ok(prompt.includes('operator-overridden'), 'a non-parked ruling still needs a home in this section')
})

// --- Timestamps -------------------------------------------------------------------------

test('nowIso names a concrete date command rather than asking for "the current time"', async () => {
  const { agent, calls } = captureAgent()
  const { nowIso } = loadWorkflowFunctions({ agent, parallel, args: GOAL_ARGS })

  await nowIso()

  const { prompt, opts } = calls[0]
  assert.ok(prompt.includes('date -u +%Y-%m-%dT%H:%M:%SZ'), 'the command must be named, not described')
  assert.match(prompt, /Do not compute or guess/)
  assert.deepEqual(opts.schema.required, ['iso'])
})

test('a throwing nowIso never costs the heartbeat its dispatch bookkeeping', async () => {
  const patches = []
  async function agent(prompt, opts) {
    if (opts.label === 'now') throw new Error('clock agent died')
    if (opts.label === 'imp-1') return { status: 'done', branch: 'br-1', artifacts: [{ url: 'x' }] }
    if (opts.label === 'heartbeat') {
      patches.push(JSON.parse(prompt.match(/'--patch' '(.*?)'/s)[1]))
      return {}
    }
    return {}
  }
  const { runDispatch } = loadWorkflowFunctions({ agent, parallel, args: GOAL_ARGS })

  // The whole point: runDispatch is called with no try/catch of its own, so a throw from
  // the clock helper would kill the run and lose bookkeeping for imps that already ran.
  const outcome = await runDispatch(baseState([task(1)]))

  assert.equal(outcome.blocked, false)
  assert.deepEqual([...outcome.doneIds], [1])
  assert.equal(patches.length, 1, 'the heartbeat still ran')
  assert.deepEqual(patches[0].tasks_done, [1], 'the completed stage is still recorded')
  assert.deepEqual(patches[0].worktrees, { 1: 'br-1' })
  assert.ok(
    !Object.prototype.hasOwnProperty.call(patches[0], 'last_heartbeat'),
    'on a clock failure the key is omitted entirely, not overwritten with a sentinel'
  )
})

test('a working nowIso puts a real ISO value in the heartbeat', async () => {
  const patches = []
  async function agent(prompt, opts) {
    if (opts.label === 'now') return { iso: '2026-08-07T11:22:33Z' }
    if (opts.label === 'imp-1') return { status: 'done', branch: 'br-1', artifacts: [] }
    if (opts.label === 'heartbeat') {
      patches.push(JSON.parse(prompt.match(/'--patch' '(.*?)'/s)[1]))
      return {}
    }
    return {}
  }
  const { runDispatch } = loadWorkflowFunctions({ agent, parallel, args: GOAL_ARGS })

  await runDispatch(baseState([task(1)]))

  assert.equal(patches[0].last_heartbeat, '2026-08-07T11:22:33Z')
})

// ---- Phase 5: drive-to-green and close ----

const SHA = 'a'.repeat(40)
const PROOF = { path: '/tmp/test-evidence.log', sha256: 'd'.repeat(64) }
const SNAPSHOT = { schema: 1, repo: '/repo', head: SHA, base: 'b'.repeat(40), merge_base: 'b'.repeat(40), spec_hash: 'c'.repeat(64), policy_hash: '9'.repeat(64), clean: true, requirements: [{ id: 'REQ-1', text: 'it works' }] }
const COVERAGE = { criteria: [{ id: 'REQ-1', text: 'it works', status: 'satisfied', evidence: 'checked implementation', verification: { kind: 'inspection', artifacts: [PROOF] } }] }
const REVIEW = { status: 'ok', verdict: 'APPROVE', findings: [], model: 'fixture-model', provider: 'fixture' }
const MANIFEST = { gates: [], discovery_error: null, no_checks_reason: 'fixture explicitly has no CI' }
const GREEN = { checks: 'passing', mergeable: 'clean', unresolved_comments: [], detail: 'green', head: SHA, merged: false, merge_commit: null }
function prAgent(script) {
  const calls = []
  const defaults = { 'revision-snapshot': SNAPSHOT, 'discover-gates': MANIFEST, 'ocr-review': REVIEW, 'dod-coverage': COVERAGE, 'verify-artifact': PROOF, 'save-publish-verification': {}, 'refresh-remote-manifest': {}, 'save-remote-mapping': {} }
  async function agent(prompt, opts) {
    calls.push(opts.label)
    const key = Object.keys(script).find((k) => opts.label.startsWith(k))
    const v = key ? script[key] : defaults[opts.label]
    return typeof v === 'function' ? v(calls, prompt) : v
  }
  return { agent, calls }
}

const PR = { number: 7, url: 'https://example.test/pr/7' }

test('a PR repair is reviewed and accepted before merge', async () => {
  let repaired = false
  const { agent, calls } = prAgent({
    'pr-status': () => ({ ...GREEN, checks: repaired ? 'passing' : 'failing' }),
    'pr-fix': () => { repaired = true; return {} },
    'merge-pr': { done: true, refused: false },
  })
  const { drivePrAndClose } = loadWorkflowFunctions({ agent, parallel })
  const out = await drivePrAndClose(PR, 'o/r', 'main', 'merge', false)
  assert.equal(out.merged, true)
  assert.ok(calls.indexOf('ocr-review') > calls.indexOf('pr-fix-7'))
  assert.ok(calls.indexOf('dod-coverage') < calls.indexOf('merge-pr-7'))
})

for (const [name, response] of [
  ['unimplemented requirement', { ...COVERAGE, criteria: [{ ...COVERAGE.criteria[0], status: 'unsatisfied' }] }],
  ['runtime criterion with no proof', { ...COVERAGE, criteria: [{ ...COVERAGE.criteria[0], verification: { kind: 'runtime', artifacts: [] } }] }],
  ['missing requirement', { criteria: [] }],
  ['stale checked criterion', { ...COVERAGE, criteria: [{ ...COVERAGE.criteria[0], status: 'unverifiable' }] }],
  ['unknown requirement ID', { ...COVERAGE, criteria: [{ ...COVERAGE.criteria[0], id: 'invented' }] }],
  ['duplicate requirement ID', { criteria: [...COVERAGE.criteria, ...COVERAGE.criteria] }],
]) {
  test(`acceptance blocks ${name}`, async () => {
    const { agent, calls } = prAgent({ 'pr-status': GREEN, 'dod-coverage': response })
    const { drivePrAndClose } = loadWorkflowFunctions({ agent, parallel })
    const out = await drivePrAndClose(PR, 'o/r', 'main', 'merge', false)
    assert.equal(out.green, false)
    assert.equal(out.merged, false)
    assert.ok(!calls.includes('merge-pr-7'))
  })
}

test('late external PR head movement blocks merge', async () => {
  const { agent, calls } = prAgent({ 'pr-status': { ...GREEN, head: 'f'.repeat(40) } })
  const { drivePrAndClose } = loadWorkflowFunctions({ agent, parallel })
  const out = await drivePrAndClose(PR, 'o/r', 'main', 'merge', false)
  assert.match(out.detail, /head differs/)
  assert.ok(!calls.includes('merge-pr-7'))
})

test('evidence artifact drift blocks completion', async () => {
  const { agent } = prAgent({ 'verify-artifact': { ...PROOF, sha256: 'f'.repeat(64) } })
  const { verifyForPublish } = loadWorkflowFunctions({ agent, parallel })
  assert.equal((await verifyForPublish('main')).reason, 'evidence_artifact_changed')
})

test('model inspection cannot satisfy an explicit runtime method', () => {
  const { acceptanceFailures } = loadWorkflowFunctions({ agent: async () => {}, parallel })
  assert.deepEqual(acceptanceFailures({ ...SNAPSHOT, requirements: [{ ...SNAPSHOT.requirements[0], method: 'runtime' }] }, COVERAGE.criteria), ['REQ-1'])
})

test('changed requirement contract invalidates prior passing evidence', async () => {
  const { agent, calls } = prAgent({})
  const { verifyForPublish } = loadWorkflowFunctions({ agent, parallel })
  const old = { schema: 1, status: 'passed', snapshot: { ...SNAPSHOT, spec_hash: 'e'.repeat(64) } }
  await verifyForPublish('main', old)
  assert.ok(calls.includes('dod-coverage'))
  assert.ok(calls.includes('ocr-review'))
})

test('missing check discovery cannot masquerade as a no-checks project', async () => {
  const { agent } = prAgent({ 'discover-gates': { gates: [], discovery_error: 'unreadable CI', no_checks_reason: null } })
  const { verifyForPublish } = loadWorkflowFunctions({ agent, parallel })
  assert.equal((await verifyForPublish('main')).reason, 'verification_manifest_invalid')
})

test('contradictory approval with a major finding is rejected', async () => {
  const { agent } = prAgent({ 'ocr-review': { ...REVIEW, findings: [{ severity: 'major' }] } })
  const { verifyForPublish } = loadWorkflowFunctions({ agent, parallel })
  assert.equal((await verifyForPublish('main')).status, 'blocked')
})

test('different base and dirty checkout invalidate evidence', () => {
  const { sameRevision } = loadWorkflowFunctions({ agent: async () => {}, parallel })
  assert.equal(sameRevision(SNAPSHOT, { ...SNAPSHOT, base: 'e'.repeat(40) }), false)
  assert.equal(sameRevision(SNAPSHOT, { ...SNAPSHOT, clean: false }), false)
})

test('a green PR with endstate "pr" is never merged', async () => {
  const { agent, calls } = prAgent({ 'pr-status': GREEN })
  const { drivePrAndClose } = loadWorkflowFunctions({ agent, parallel })

  const out = await drivePrAndClose(PR, 'o/r', 'main', 'pr', false)

  assert.equal(out.green, true)
  assert.equal(out.merged, false)
  assert.equal(out.rounds, 0, 'a green PR needs no fix rounds')
  assert.ok(!calls.some((c) => c.startsWith('merge-pr')), 'endstate "pr" must not merge')
})

test('a refused merge is final — no retry, and it is reported as refused', async () => {
  const { agent, calls } = prAgent({
    'pr-status': GREEN,
    'merge-pr': { done: false, refused: true, detail: 'denied by a standing deny rule' },
  })
  const { drivePrAndClose } = loadWorkflowFunctions({ agent, parallel })

  const out = await drivePrAndClose(PR, 'o/r', 'main', 'merge', false)

  assert.equal(out.merged, false)
  assert.equal(out.refused, true)
  assert.match(out.detail, /deny rule/)
  assert.equal(calls.filter((c) => c.startsWith('merge-pr')).length, 1, 'a refusal must never be retried')
})

test('a merge that is refused never proceeds to a release', async () => {
  const { agent, calls } = prAgent({
    'pr-status': GREEN,
    'merge-pr': { done: false, refused: true, detail: 'blocked' },
  })
  const { drivePrAndClose } = loadWorkflowFunctions({ agent, parallel })

  await drivePrAndClose(PR, 'o/r', 'main', 'release', false)

  assert.ok(!calls.some((c) => c.startsWith('cut-release')), 'nothing may be released off an unmerged PR')
})

test('the green loop is bounded and hands off rather than looping forever', async () => {
  const RED = { ...GREEN, checks: 'failing', mergeable: 'clean', unresolved_comments: [], detail: 'tests red' }
  const { agent, calls } = prAgent({ 'pr-status': RED, 'pr-fix': {} })
  const { drivePrAndClose } = loadWorkflowFunctions({ agent, parallel })

  const out = await drivePrAndClose(PR, 'o/r', 'main', 'merge', false)

  assert.equal(out.rounds, 3, 'the cap is three rounds')
  assert.equal(out.green, false)
  assert.equal(out.merged, false, 'a PR that never went green must not be merged')
  assert.match(out.detail, /not green after 3 round/)
  assert.ok(!calls.some((c) => c.startsWith('merge-pr')))
})

test('an already-merged PR is not merged again on a resume', async () => {
  const { agent, calls } = prAgent({ 'pr-status': { ...GREEN, merged: true, merge_commit: 'e'.repeat(40) } })
  const { drivePrAndClose } = loadWorkflowFunctions({ agent, parallel })

  const out = await drivePrAndClose(PR, 'o/r', 'main', 'merge', true)

  assert.equal(out.merged, true)
  assert.ok(!calls.some((c) => c.startsWith('merge-pr')), 'the merged_at marker must guard the re-merge')
})

test('unresolved review comments block green just as a failing check does', async () => {
  let round = 0
  const { agent } = prAgent({
    'pr-status': () => {
      round += 1
      return round === 1
        ? { ...GREEN, checks: 'passing', mergeable: 'clean', unresolved_comments: ['rename the helper'], detail: '1 comment' }
        : GREEN
    },
    'pr-fix': {},
  })
  const { drivePrAndClose } = loadWorkflowFunctions({ agent, parallel })

  const out = await drivePrAndClose(PR, 'o/r', 'main', 'pr', false)

  assert.equal(out.rounds, 1, 'the comment must drive exactly one fix round')
  assert.equal(out.green, true)
})

test('an unknown mergeability is not green — the check fails closed', async () => {
  // GitHub computes mergeability asynchronously and reports unknown while it does.
  // Treating that as green would merge on the absence of the fact the check establishes.
  const UNKNOWN = { ...GREEN, checks: 'passing', mergeable: 'unknown', unresolved_comments: [], detail: 'mergeability not computed' }
  const { agent, calls } = prAgent({ 'pr-status': UNKNOWN, 'pr-fix': {} })
  const { drivePrAndClose } = loadWorkflowFunctions({ agent, parallel })

  const out = await drivePrAndClose(PR, 'o/r', 'main', 'merge', false)

  assert.equal(out.green, false)
  assert.equal(out.merged, false)
  assert.ok(!calls.some((c) => c.startsWith('merge-pr')), 'must never merge on unknown mergeability')
})

test('a resume after a merge can still cut the release it never got to', async () => {
  // The failure this guards: merge lands, the run dies, and the resume returns early
  // because the PR is already merged — so the authorized release never happens.
  const { agent, calls } = prAgent({
    'pr-status': { ...GREEN, merged: true, merge_commit: 'e'.repeat(40) },
    'cut-release': { done: true, refused: false, url: 'https://example.test/releases/v1', detail: 'cut' },
  })
  const { drivePrAndClose } = loadWorkflowFunctions({ agent, parallel })

  const out = await drivePrAndClose(PR, 'o/r', 'main', 'release', true, false, { schema: 1, status: 'passed', snapshot: { head: GREEN.head }, manifest: { gates: [] } })

  assert.ok(!calls.some((c) => c.startsWith('merge-pr')), 'an already-merged PR must not be re-merged')
  assert.equal(out.released, true, 'the release must still be reachable on resume')
  assert.equal(out.release_url, 'https://example.test/releases/v1')
})

test('an already-released run re-cuts nothing', async () => {
  const { agent, calls } = prAgent({ 'pr-status': { ...GREEN, merged: true, merge_commit: 'e'.repeat(40) } })
  const { drivePrAndClose } = loadWorkflowFunctions({ agent, parallel })

  const out = await drivePrAndClose(PR, 'o/r', 'main', 'release', true, true)

  assert.equal(out.released, true)
  assert.ok(!calls.some((c) => c.startsWith('cut-release')), 'the release_url marker must guard the re-cut')
})

test('a run with no PR reports that rather than treating it as an error', async () => {
  const { agent, calls } = prAgent({})
  const { drivePrAndClose } = loadWorkflowFunctions({ agent, parallel })

  const out = await drivePrAndClose(null, 'o/r', 'main', 'merge', false)

  assert.equal(out.merged, false)
  assert.match(out.detail, /no PR/)
  assert.equal(calls.length, 0)
})


test('merged resume without evidence cannot release', async () => {
  const { agent, calls } = prAgent({ 'pr-status': { ...GREEN, merged: true, merge_commit: 'e'.repeat(40) } })
  const { drivePrAndClose } = loadWorkflowFunctions({ agent, parallel })
  const out = await drivePrAndClose(PR, 'o/r', 'main', 'release', true, false)
  assert.equal(out.released, false)
  assert.ok(!calls.some(c => c.startsWith('cut-release')))
})


async function executeWorkflow(state, overrides = {}) {
  const calls = []
  const phases = []
  const source = fs.readFileSync(SCRIPT_PATH, 'utf8').replace('export const meta', 'const meta')
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
  const invoke = new AsyncFunction('agent', 'parallel', 'phase', 'args', 'log', source)
  const result = await invoke(async (prompt, options) => {
    calls.push({ label: options.label, prompt })
    if (Object.hasOwn(overrides, options.label)) return typeof overrides[options.label] === 'function' ? overrides[options.label](prompt, calls) : overrides[options.label]
    if (options.label === 'claim-run') return { token: 'a'.repeat(32) }
    if (options.label === 'read-state') return state
    if (options.label === 'count-state-tasks') return { raw_task_count: state.tasks.length, raw_phase: state.phase, raw_spec_lengths: state.tasks.map(t => (t.spec || '').length) }
    if (options.label === 'release-run') return { released: true }
    return {}
  }, parallel, name => phases.push(name), { stateFilePath: '/fixture/run.json', pluginRoot: '/fixture/plugin' }, () => {})
  return { result, calls, phases }
}

test('full entrypoint abort preserves resume metadata and releases ownership', async () => {
  const { result, calls } = await executeWorkflow({ tasks: [], branch: 'test', phase: 'ready', operator_decision: 'abort', last_result: { default_branch: 'master', dispatch: { artifacts: ['kept'] } } })
  assert.equal(result.status, 'aborted')
  const saved = calls.find(c => c.label === 'save-result').prompt
  assert.match(saved, /"default_branch":"master"/)
  assert.match(saved, /"artifacts":\["kept"\]/)
  assert.equal(calls.at(-1).label, 'release-run')
})

test('full entrypoint rejects a mismapped read while releasing ownership', async () => {
  const { result, calls } = await executeWorkflow({ tasks: [], branch: 'test', phase: 'ready' }, { 'count-state-tasks': { raw_task_count: 1, raw_phase: 'ready', raw_spec_lengths: [10] } })
  assert.equal(result.reason, 'state_read_mismatch')
  assert.equal(calls.at(-1).label, 'release-run')
})

test('full entrypoint never reads or mutates state without ownership', async () => {
  const { result, calls } = await executeWorkflow({}, { 'claim-run': { error: 'already owned' } })
  assert.equal(result.reason, 'run_owned_or_claim_failed')
  assert.deepEqual(calls.map(c => c.label), ['claim-run'])
})


test('verification pins the fetched base for inner snapshots', async () => {
  const snapshots = []
  const { agent } = prAgent({ 'revision-snapshot': (_calls, prompt) => { snapshots.push(prompt); return SNAPSHOT } })
  const { verifyForPublish } = loadWorkflowFunctions({ agent, parallel })
  assert.equal((await verifyForPublish('main')).status, 'passed')
  assert.match(snapshots[0], /--refresh/)
  for (const prompt of snapshots.slice(1)) {
    assert.doesNotMatch(prompt, /--refresh/)
    assert.ok(prompt.includes(SNAPSHOT.base))
  }
})

test('legacy process-only contracts receive an actionable reason', async () => {
  const { agent } = prAgent({ 'revision-snapshot': { error: 'no_functional_criteria: add observable outcomes' } })
  const { verifyForPublish } = loadWorkflowFunctions({ agent, parallel })
  assert.equal((await verifyForPublish('main')).reason, 'no_functional_criteria')
})

test('inspection evidence can complete a zero-diff research outcome', async () => {
  const { agent } = prAgent({ 'revision-snapshot': { ...SNAPSHOT, base: SNAPSHOT.head, merge_base: SNAPSHOT.head } })
  const { verifyForPublish } = loadWorkflowFunctions({ agent, parallel })
  assert.equal((await verifyForPublish('main')).status, 'passed')
})


const COMPLETE_RUN_RESPONSES = {
  'check-contract': { requirements: SNAPSHOT.requirements },
  'check-budget': { ok: true },
  'get-default-branch': { default_branch: 'master' },
  'sync-default': { conflict: null },
  'revision-snapshot': SNAPSHOT,
  'discover-gates': MANIFEST,
  'ocr-review': REVIEW,
  'dod-coverage': COVERAGE,
  'verify-artifact': PROOF,
  'diff-stat': { diff_stat: 'artifact-only outcome; no code diff' },
}
const INTEGRATE_STATE = { tasks: [], branch: 'test', phase: 'integrate', dispatched_at: '2026-09-09T00:00:00Z' }

test('complete main integration reaches awaiting authorization and saves evidence', async () => {
  const { result, calls } = await executeWorkflow(INTEGRATE_STATE, COMPLETE_RUN_RESPONSES)
  assert.equal(result.status, 'awaiting_authorization')
  assert.equal(result.verification.status, 'passed')
  assert.match(result.diff_stat, /artifact-only/)
  assert.ok(calls.find(c => c.label === 'save-result').prompt.includes('awaiting_authorization'))
  assert.equal(calls.at(-1).label, 'release-run')
})

test('main integration preserves an explicit gate skip for the failed revision', async () => {
  const gate = { name: 'lint', cmd: 'npm run lint', argv: ['npm','run','lint'], cwd: '.', timeout_seconds: 60, source: 'package.json', remote_only: false, required: true }
  const { result, calls } = await executeWorkflow({ ...INTEGRATE_STATE, operator_decision: 'skip lint: accepted known debt', verification: { snapshot: SNAPSHOT } }, { ...COMPLETE_RUN_RESPONSES, 'discover-gates': { gates: [gate], discovery_error: null } })
  assert.equal(result.status, 'awaiting_authorization')
  assert.equal(result.verification.gate_waiver.rationale, 'accepted known debt')
  assert.equal(result.verification.gates[0].skipped, true)
  assert.equal(result.verification.gates[0].pass, false)
  assert.ok(!calls.some(c => c.label === 'gate-lint'))
})

test('cosmetic reviewer text changes are replaced with the canonical requirement', async () => {
  const { agent } = prAgent({ 'dod-coverage': { criteria: [{ ...COVERAGE.criteria[0], text: 'cosmetic model rewrite' }] } })
  const { verifyForPublish } = loadWorkflowFunctions({ agent, parallel })
  const result = await verifyForPublish('main')
  assert.equal(result.status, 'passed')
  assert.equal(result.criteria[0].text, SNAPSHOT.requirements[0].text)
})


test('complete publish path returns a verified final PR result', async () => {
  const integrated = await executeWorkflow(INTEGRATE_STATE, COMPLETE_RUN_RESPONSES)
  const state = { ...INTEGRATE_STATE, pr: PR, endstate: 'pr', operator_decision: 'PR: yes', last_result: integrated.result, verification: integrated.result.verification }
  const { result, calls } = await executeWorkflow(state, { ...COMPLETE_RUN_RESPONSES, 'pr-status': GREEN, 'finalize': { pr_ready: true, run_stats: {}, learnings_candidates: [], discussion_comment_url: null, prs_monitor: null } })
  assert.equal(result.status, 'final')
  assert.equal(result.pr_outcome.green, true)
  assert.equal(result.pr_outcome.merged, false)
  assert.equal(result.verification.status, 'passed')
  assert.ok(calls.some(c => c.label === 'publish-evidence'))
  assert.equal(calls.at(-1).label, 'release-run')
})


const GATE_PLAN = { gate: 'lint', cmd: 'npm run lint', argv: ['npm','run','lint'], source_sha256: '8'.repeat(64), plan_id: '7'.repeat(32), plan_path: '/tmp/plan.json', log_path: '/tmp/plan.json.log', cwd: '/repo', timeout_seconds: 60 }
const GATE_RECEIPT = { ...GATE_PLAN, artifact: { path: GATE_PLAN.log_path, sha256: '6'.repeat(64) } }
const LOCAL_LINT = { name: 'lint', cmd: 'npm run lint', argv: ['npm','run','lint'], cwd: '.', timeout_seconds: 60, source: 'package.json', remote_only: false, required: true }

test('a failed gate records the post-repair head so an operator skip can bind', async () => {
  let head = SNAPSHOT.head
  let fixes = 0
  const responses = { ...COMPLETE_RUN_RESPONSES, 'revision-snapshot': () => ({ ...SNAPSHOT, head }), 'discover-gates': { gates: [LOCAL_LINT], discovery_error: null }, 'gate-plan-lint': GATE_PLAN, 'gate-lint': { ...GATE_RECEIPT, pass: false, status: 'failed', exit_code: 1, tail: 'known debt' }, 'fix-lint': () => { fixes++; head = (fixes === 1 ? 'e' : 'f').repeat(40); return {} } }
  const failed = await executeWorkflow(INTEGRATE_STATE, responses)
  assert.equal(failed.result.reason, 'gate_red')
  assert.equal(failed.result.detail.snapshot.head, 'f'.repeat(40))
  assert.equal(failed.result.handover.head, head)
  const resumed = await executeWorkflow({ ...INTEGRATE_STATE, operator_decision: 'skip lint: accepted debt', verification: failed.result.detail }, responses)
  assert.equal(resumed.result.status, 'awaiting_authorization')
  assert.ok(!resumed.calls.some(call => call.label === 'gate-lint'))
})

for (const reason of ['run_budget_exhausted', 'invalid_concurrency', 'gate_red', 'gate_evidence_invalid', 'verification_manifest_invalid', 'acceptance_incomplete', 'publish_incomplete']) {
  test(`publish resume for ${reason} retains authorization and segment`, async () => {
    const integrated = await executeWorkflow(INTEGRATE_STATE, COMPLETE_RUN_RESPONSES)
    const state = { ...INTEGRATE_STATE, segment: 'publish_finalize', pr: PR, endstate: 'pr', operator_decision: 'resolved, continue', last_result: { ...integrated.result, status: 'blocked', reason }, verification: integrated.result.verification }
    const { result, phases } = await executeWorkflow(state, { ...COMPLETE_RUN_RESPONSES, 'pr-status': GREEN, 'finalize': { pr_ready: true, run_stats: {}, learnings_candidates: [] } })
    assert.equal(result.status, 'final')
    assert.equal(result.pr_outcome.green, true)
    assert.ok(!phases.includes('Dispatch'))
  })
}

test('remote checks use explicit rendered names instead of friendly gate labels', async () => {
  const gate = { ...LOCAL_LINT, remote_only: true, name: 'friendly-label', check_name: 'caller / test (22)' }
  const { agent } = prAgent({ 'discover-gates': { gates: [gate], discovery_error: null }, 'pr-status': { ...GREEN, check_details: [{ name: 'caller / test (22)', status: 'passing' }] } })
  const { drivePrAndClose } = loadWorkflowFunctions({ agent, parallel })
  assert.equal((await drivePrAndClose(PR, 'o/r', 'main', 'pr', false)).green, true)
})

test('missing remote mapping exposes observed names while preserving passed local evidence', async () => {
  const gate = { ...LOCAL_LINT, remote_only: true, check_name: 'wrong' }
  const { agent } = prAgent({ 'discover-gates': { gates: [gate], discovery_error: null }, 'pr-status': { ...GREEN, check_details: [{ name: 'actual-check', status: 'passing' }] } })
  const { drivePrAndClose } = loadWorkflowFunctions({ agent, parallel })
  const result = await drivePrAndClose(PR, 'o/r', 'main', 'pr', false)
  assert.equal(result.green, false)
  assert.equal(result.verification.status, 'passed')
  assert.equal(result.remote_check_observation.reason, 'remote_checks_unverified')
  assert.match(result.detail, /actual-check/)
})


test('gate receipt must match the separately observed plan and quoted helper path', async () => {
  const prompts = []
  const { runGate } = loadWorkflowFunctions({ args: { pluginRoot: "/plugin with spaces" }, agent: async (prompt, opts) => {
    prompts.push(prompt)
    return opts.label === 'gate-plan-lint' ? GATE_PLAN : { ...GATE_RECEIPT, pass: true, exit_code: 0, status: 'passed', plan_id: '0'.repeat(32) }
  }, parallel })
  const result = await runGate(LOCAL_LINT)
  assert.equal(result.status, 'unavailable')
  assert.match(result.tail, /does not match/)
  assert.ok(prompts[1].includes("'python3' '/plugin with spaces/scripts/workflow-evidence.py' 'gate-record'"))
})

test('one transient timeout retries without a speculative code repair', async () => {
  let executions = 0
  const calls = []
  const { runGatesWithRetry } = loadWorkflowFunctions({ agent: async (prompt, opts) => {
    calls.push(opts.label)
    if (opts.label === 'gate-plan-lint') return GATE_PLAN
    executions++
    return { ...GATE_RECEIPT, pass: false, status: 'unavailable', exit_code: 124, tail: 'timeout' }
  }, parallel })
  const result = await runGatesWithRetry([LOCAL_LINT])
  assert.equal(executions, 2)
  assert.equal(result.results[0].attempts, 2)
  assert.ok(!calls.includes('fix-lint'))
})

test('dirty repair preserves gate diagnostics without issuing a stale waiver snapshot', async () => {
  let dirty = false
  const responses = { ...COMPLETE_RUN_RESPONSES, 'revision-snapshot': () => ({ ...SNAPSHOT, clean: !dirty }), 'discover-gates': { gates: [LOCAL_LINT], discovery_error: null }, 'gate-plan-lint': GATE_PLAN, 'gate-lint': { ...GATE_RECEIPT, pass: false, status: 'failed', exit_code: 1, tail: 'specific gate failure' }, 'fix-lint': () => { dirty = true; return {} } }
  const { result } = await executeWorkflow(INTEGRATE_STATE, responses)
  assert.equal(result.reason, 'gate_red')
  assert.equal(result.detail.gates[0].tail, 'specific gate failure')
  assert.equal(result.detail.snapshot, null)
  assert.match(result.detail.snapshot_error, /dirty/)
  assert.equal(result.detail.last_verified_snapshot.head, SNAPSHOT.head)
})

test('unknown operator gate name lists the applicable names', async () => {
  const { result } = await executeWorkflow({ ...INTEGRATE_STATE, operator_decision: 'skip lint-typo: accepted debt', verification: { snapshot: SNAPSHOT } }, { ...COMPLETE_RUN_RESPONSES, 'discover-gates': { gates: [LOCAL_LINT], discovery_error: null } })
  assert.equal(result.reason, 'gate_decision_unmatched')
  assert.deepEqual(result.detail.local_gates, ['lint'])
})

test('local manifest cannot silently exceed the native foreground timeout', () => {
  const { validManifest } = loadWorkflowFunctions({ agent: async () => ({}), parallel })
  assert.equal(validManifest({ gates: [{ ...LOCAL_LINT, timeout_seconds: 1800 }], discovery_error: null }), false)
})


test('remote name reconciliation retains local evidence without rerunning verification', async () => {
  const gate = { ...LOCAL_LINT, remote_only: true, check_name: 'unexpanded-name' }
  const { agent, calls } = prAgent({ 'discover-gates': { gates: [gate], discovery_error: null }, 'pr-status': { ...GREEN, check_details: [{ name: 'caller / lint (22)', status: 'passing' }] }, 'reconcile-remote-checks': { mappings: [{ gate: 'lint', source: gate.source, check_name: 'caller / lint (22)', evidence: 'fixture CI job lint expands node matrix 22 under caller' }] } })
  const { drivePrAndClose } = loadWorkflowFunctions({ agent, parallel })
  const result = await drivePrAndClose(PR, 'o/r', 'main', 'pr', false)
  assert.equal(result.green, true)
  assert.equal(result.verification.status, 'passed')
  assert.equal(calls.filter(label => label === 'ocr-review').length, 1)
  assert.equal(calls.filter(label => label === 'dod-coverage').length, 1)
})

test('a matching native receipt passes the gate orchestration', async () => {
  const { runGatesWithRetry } = loadWorkflowFunctions({ agent: async (prompt, opts) => opts.label === 'gate-plan-lint' ? GATE_PLAN : { ...GATE_RECEIPT, pass: true, status: 'passed', exit_code: 0 }, parallel })
  const result = await runGatesWithRetry([LOCAL_LINT])
  assert.equal(result.blockedOn, null)
  assert.equal(result.results[0].pass, true)
})
