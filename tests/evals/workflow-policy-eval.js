#!/usr/bin/env node
'use strict'
// Real orchestration functions, deterministic environment responses. This is NOT
// an LLM benchmark. Null model/cost fields make that boundary machine-readable.
const fs = require('node:fs')
const path = require('node:path')
const cp = require('node:child_process')
const crypto = require('node:crypto')
const { performance } = require('node:perf_hooks')
const root = path.resolve(__dirname, '../..')
const file = 'plugins/imps/scripts/imps-run.workflow.js'
const casesBytes = fs.readFileSync(path.join(__dirname, 'workflow-cases.json'))
const dataset = JSON.parse(casesBytes)
const current = fs.readFileSync(path.join(root, file), 'utf8')
const baseline = cp.execFileSync('git', ['show', `${dataset.baseline}:${file}`], { cwd: root, encoding: 'utf8', maxBuffer: 2e6 })
const hash = value => crypto.createHash('sha256').update(value).digest('hex')
const sha = 'a'.repeat(40)
const proof = { path: '/fixture/evidence.log', sha256: 'd'.repeat(64) }

async function trial(source, scenario) {
  const calls = []
  let repaired = !scenario.repair
  let snapshots = 0
  const fault = scenario.fault
  const snapshot = { schema: 1, repo: '/fixture', head: sha, base: 'b'.repeat(40), merge_base: 'b'.repeat(40), spec_hash: 'c'.repeat(64), policy_hash: '9'.repeat(64), clean: true, requirements: [{ id: 'REQ-1', text: 'outcome' }] }
  if (fault === 'runtime') snapshot.requirements[0].method = 'runtime'
  const criterion = { id: 'REQ-1', text: 'outcome', status: 'satisfied', evidence: 'fixture artifact', verification: { kind: 'inspection', artifacts: [proof] } }
  if (['unsatisfied', 'unverifiable'].includes(fault)) criterion.status = fault
  if (fault === 'artifact') criterion.verification.artifacts = []
  if (fault === 'unknown-id') criterion.id = 'invented'
  const review = { status: 'ok', verdict: 'APPROVE', findings: [], model: 'fixture', provider: 'fixture' }
  if (fault === 'review-unavailable') review.status = 'blocked'
  if (['review-adverse', 'review-low-adverse'].includes(fault)) review.verdict = 'CHANGES_REQUESTED'
  if (fault === 'review-contradictory') review.findings = [{ severity: 'major' }]
  if (fault === 'review-low-adverse') review.findings = [{ severity: 'nit' }]
  async function agent(prompt, options) {
    const label = options.label
    calls.push(label)
    if (label === 'pr-status') return { checks: !repaired ? 'failing' : scenario.no_ci ? 'none' : 'passing', mergeable: fault === 'mergeability' ? 'unknown' : 'clean', unresolved_comments: [], head: fault === 'remote-head' ? 'f'.repeat(40) : sha, merged: false, merge_commit: null }
    if (label.startsWith('pr-fix')) { repaired = true; return {} }
    if (label === 'revision-snapshot') {
      snapshots++
      return { ...snapshot, clean: fault !== 'dirty', head: fault === 'moving-head' && snapshots > 2 ? 'f'.repeat(40) : sha }
    }
    if (label === 'discover-gates') return { gates: [], discovery_error: fault === 'discovery' ? 'cannot read CI' : null, no_checks_reason: fault === 'empty-manifest' ? null : 'explicit fixture policy' }
    if (label === 'ocr-review') return fault === 'review-malformed' ? {} : review
    if (label === 'dod-coverage') return { criteria: fault === 'missing' ? [] : fault === 'duplicate' ? [criterion, criterion] : [criterion] }
    if (label === 'verify-artifact') return fault === 'artifact-changed' ? { ...proof, sha256: 'e'.repeat(64) } : proof
    if (label.startsWith('merge-pr')) return { done: fault !== 'denied', refused: fault === 'denied' }
    return {}
  }
  const body = source.slice(0, source.indexOf("\nphase('Preflight')")).replace('export const meta', 'const meta')
  const drive = new Function('agent', 'parallel', 'phase', 'args', 'log', body + '\nreturn drivePrAndClose;')(agent, async fs => Promise.all(fs.map(fn => fn())), () => {}, {}, () => {})
  const started = performance.now()
  const outcome = await drive({ number: 1 }, 'fixture/repo', 'main', 'merge', false, false)
  return { observed_merge: outcome.merged === true, calls, duration_ms: performance.now() - started }
}

async function main() {
  const results = []
  for (const [variant, source] of [['baseline', baseline], ['candidate', current]]) {
    for (let repeat = 1; repeat <= 3; repeat++) for (const scenario of dataset.cases) {
      const result = await trial(source, scenario)
      results.push({ case_id: scenario.id, variant, trial: repeat, expected_merge: scenario.expected_merge, ...result,
        pass: result.observed_merge === scenario.expected_merge })
    }
  }
  const metrics = Object.fromEntries(['baseline', 'candidate'].map(variant => {
    const rows = results.filter(row => row.variant === variant)
    return [variant, { trials: rows.length, passing: rows.filter(row => row.pass).length,
      false_completion: rows.filter(row => row.observed_merge && !row.expected_merge).length,
      false_block: rows.filter(row => !row.observed_merge && row.expected_merge).length,
      mean_stubbed_agent_calls: rows.reduce((sum, row) => sum + row.calls.length, 0) / rows.length }]
  }))
  process.stdout.write(JSON.stringify({ schema: 1, kind: 'orchestration', model: null, cost_usd: null,
    baseline_commit: dataset.baseline, candidate_source_sha256: hash(current), dataset_sha256: hash(casesBytes), metrics, results }, null, 2) + '\n')
  if (metrics.candidate.false_completion || metrics.candidate.false_block) process.exitCode = 1
}
main().catch(error => { console.error(error); process.exitCode = 1 })
