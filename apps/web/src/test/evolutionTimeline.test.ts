import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { buildEvolutionTimeline } from '../evolutionTimeline'
import { parseReportText } from '../parser'

const fixture = (name: string) =>
  JSON.parse(readFileSync(resolve(process.cwd(), 'public', 'fixtures', name), 'utf8'))

function reports(overrides: Record<string, unknown> = {}) {
  const evidence = { ...fixture('evolution-evidence-report.json'), ...overrides }
  return [
    parseReportText(JSON.stringify(evidence), 'evolution-report.json'),
    parseReportText(
      JSON.stringify(fixture('evolution-release-manifest.json')),
      'release-manifest.json',
    ),
    parseReportText(
      JSON.stringify(fixture('evolution-evidence-index.json')),
      'evidence-index.json',
    ),
  ]
}

const stage = (model: ReturnType<typeof buildEvolutionTimeline>, id: string) =>
  model?.stages.find((item) => item.id === id)?.status

describe('Evolution Timeline read model', () => {
  it('materializes a complete published evidence chain with winner and artifacts', () => {
    const model = buildEvolutionTimeline(reports(), 'b'.repeat(64))
    expect(model?.publicationStatus).toBe('PASSED')
    expect(model?.stages.map((item) => item.status)).toEqual(Array(8).fill('PASSED'))
    expect(model?.proposals.find((item) => item.id === 'candidate-boundary')?.winner).toBe(true)
    expect(model?.artifacts.find((item) => item.role === 'locked_test_report')?.path).toBe(
      'evidence/locked-test-report.json',
    )
    expect(model?.baseline.passRate).toBe(0.5)
    expect(model?.winner.tokens).toBe(1450)
    expect(model?.manifestHash).toBe('b'.repeat(64))
    expect(model?.inputFingerprint).toBe('a'.repeat(64))
  })

  it('rejects publication when regression fails', () => {
    const source = fixture('evolution-evidence-report.json')
    source.stages.regression_dev = { passed: false, loss_cases: ['case-regression'] }
    const model = buildEvolutionTimeline(reports({ stages: source.stages }))
    expect(stage(model, 'regression')).toBe('REJECTED')
    expect(stage(model, 'published')).toBe('REJECTED')
  })

  it('rejects publication when confirmation is rejected', () => {
    const source = fixture('evolution-evidence-report.json')
    source.stages.validation_confirm.decision = 'REJECTED'
    const model = buildEvolutionTimeline(reports({ stages: source.stages }))
    expect(stage(model, 'confirm')).toBe('REJECTED')
    expect(model?.publicationStatus).toBe('REJECTED')
  })

  it('does not publish when locked test has not started', () => {
    const source = fixture('evolution-evidence-report.json')
    delete source.stages.locked_test
    const model = buildEvolutionTimeline(reports({ stages: source.stages }))
    expect(stage(model, 'locked')).toBe('NOT_STARTED')
    expect(stage(model, 'published')).not.toBe('PASSED')
  })

  it('keeps human review rejection as a terminal rejection', () => {
    const model = buildEvolutionTimeline(
      reports({ human_review: { decision: 'REJECTED', reviewer: 'reviewer', reason: 'unsafe' } }),
    )
    expect(stage(model, 'review')).toBe('REJECTED')
    expect(model?.publicationStatus).toBe('REJECTED')
  })

  it('distinguishes real provider calls over simulated fixture input', () => {
    const model = buildEvolutionTimeline([
      parseReportText(
        JSON.stringify(fixture('real-llm-proposal-smoke.json')),
        'proposal-report.json',
      ),
    ])
    expect(model?.provider).toBe('deepseek')
    expect(model?.proposalEvidenceClass).toBe('simulated_fixture')
    expect(model?.simulated).toBe(false)
    expect(model?.publicationStatus).toBe('NOT_STARTED')
  })

  it('preserves unavailable numeric values as null rather than zero', () => {
    const source = fixture('evolution-evidence-report.json')
    source.stages.validation_search.v1.pass_rate = null
    source.v1_v2_aggregate.base.tokens = null
    const model = buildEvolutionTimeline(
      reports({ stages: source.stages, v1_v2_aggregate: source.v1_v2_aggregate }),
    )
    expect(model?.baseline.passRate).toBeNull()
    expect(model?.baseline.tokens).toBeNull()
  })

  it('uses UNAVAILABLE only for an explicit unavailable decision', () => {
    const source = fixture('evolution-evidence-report.json')
    source.stages.locked_test = { decision: 'UNAVAILABLE' }
    const model = buildEvolutionTimeline(reports({ stages: source.stages }))
    expect(stage(model, 'locked')).toBe('UNAVAILABLE')
    expect(stage(model, 'published')).toBe('FAILED')
  })

  it('treats a frozen Proposal Manifest as completed without inventing candidate fields', () => {
    const model = buildEvolutionTimeline([
      parseReportText(
        JSON.stringify({
          schema_version: 'ase/real-llm-proposal-manifest/v1alpha1',
          provider: 'deepseek',
          model: 'deepseek-v4-pro',
          proposal_count: 4,
          proposals_sha256: 'a'.repeat(64),
          input_evidence_class: 'simulated_fixture',
          claim_limit: 'proposal path only',
        }),
      ),
    ])
    expect(stage(model, 'proposal')).toBe('PASSED')
    expect(model?.proposals).toEqual([])
    expect(stage(model, 'search')).toBe('NOT_STARTED')
  })
})
