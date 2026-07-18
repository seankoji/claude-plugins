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
    `${body}\nreturn { runDispatch, stageTasks, dispatchImp, parseTaskDecision, parseGateDecision, validateStateRead }`
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
