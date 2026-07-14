import { describe, expect, it } from 'vitest'
import { parseReportFile, parseReportText, parseSha256File } from '../parser'
import { IMPORT_LIMITS } from '../security'

const minimal = {
  report_schema_version: 'ase/report/v1alpha1',
  experiment: { id: 'experiment', name: 'test', protocol_snapshot: {} },
  variants: [],
  statistics: {},
}

describe('report parser', () => {
  it('recognizes and validates a paired experiment report', () => {
    const report = parseReportText(JSON.stringify(minimal), 'report.json')
    expect(report.kind).toBe('experiment')
    expect(report.schemaVersion).toBe('ase/report/v1alpha1')
  })

  it('keeps malicious HTML inert as plain text and strips prototype keys', () => {
    const payload = JSON.parse(
      JSON.stringify({
        ...minimal,
        experiment: {
          ...minimal.experiment,
          name: '<img src=x onerror=alert(1)><script>steal()</script>',
          api_key: 'should-never-render',
        },
      }).replace('"statistics":{}', '"statistics":{},"__proto__":{"polluted":true}'),
    )
    const report = parseReportText(JSON.stringify(payload))
    const experiment = report.data.experiment as Record<string, unknown>
    expect(experiment.name).toContain('<script>')
    expect(experiment.api_key).toBe('[REDACTED]')
    expect(({} as Record<string, unknown>).polluted).toBeUndefined()
  })

  it('rejects an unknown schema version with an incompatibility message', () => {
    expect(() =>
      parseReportText(JSON.stringify({ ...minimal, report_schema_version: 'ase/report/v99' })),
    ).toThrowError(/不兼容的 schema_version/)
  })

  it('rejects oversized files before reading them', async () => {
    const file = new File(['{}'], 'huge.json', { type: 'application/json' })
    Object.defineProperty(file, 'size', { value: IMPORT_LIMITS.maxFileBytes + 1 })
    await expect(parseReportFile(file)).rejects.toMatchObject({
      code: 'size',
    })
  })

  it('accepts only a strict SHA-256 release sidecar', async () => {
    const valid = new File(['a'.repeat(64) + '\n'], 'release-manifest.sha256')
    Object.defineProperty(valid, 'text', { value: async () => 'a'.repeat(64) + '\n' })
    await expect(parseSha256File(valid)).resolves.toBe('a'.repeat(64))
    const invalid = new File(['not-a-hash'], 'release-manifest.sha256')
    Object.defineProperty(invalid, 'text', { value: async () => 'not-a-hash' })
    await expect(parseSha256File(invalid)).rejects.toThrow(/格式无效/)
  })

  it('strictly requires the locked test boundary', () => {
    expect(() =>
      parseReportText(
        JSON.stringify({
          schema_version: 'ase/optimization-report/v1alpha1',
          job: {},
          candidates: [],
          winner_id: 'winner',
          locked_test_accessed: true,
        }),
      ),
    ).toThrowError(/locked_test_accessed=false/)
  })

  it('recognizes promotion state and marks fake evidence as simulated', () => {
    const report = parseReportText(
      JSON.stringify({
        schema_version: 'ase/skill-version-promotion/v1alpha1',
        id: 'promotion-id',
        skill_name: 'python-review',
        target_version: '2.0.0',
        status: 'REJECTED',
        transitions: [{ to_status: 'REJECTED' }],
        evidence: [{ simulated: true }],
      }),
    )
    expect(report.kind).toBe('promotion')
    expect(report.simulated).toBe(true)
  })

  it('requires immutable SkillVersion evidence fields', () => {
    expect(() =>
      parseReportText(
        JSON.stringify({
          schema_version: 'ase/skill-version/v1alpha1',
          id: 'version-id',
          skill_name: 'python-review',
          version: '2.0.0',
          promotion_id: 'promotion-id',
          validation_confirm: {},
        }),
      ),
    ).toThrowError(/locked_test/)
  })

  it('recognizes Stage 4b workflow and release schemas', () => {
    const workflow = parseReportText(
      JSON.stringify({
        schema_version: 'ase/promotion-workflow/v1alpha1',
        id: 'workflow-id',
        promotion_id: 'promotion-id',
        skill_name: 'python-review',
        target_version: '2.0.0',
        status: 'APPROVED',
        lineage: [],
        claim_limit: 'fixture only',
        simulated: true,
      }),
    )
    const release = parseReportText(
      JSON.stringify({
        schema_version: 'ase/promotion-release/v1alpha1',
        workflow_id: 'workflow-id',
        promotion_id: 'promotion-id',
        decision: 'APPROVED',
        lineage: [],
        confirmation: {},
        claim_limit: 'fixture only',
        simulated: true,
      }),
    )
    expect([workflow.kind, release.kind]).toEqual(['promotion', 'promotion'])
    expect(workflow.simulated && release.simulated).toBe(true)
  })

  it('rejects incomplete Stage 4b release manifests', () => {
    expect(() =>
      parseReportText(
        JSON.stringify({
          schema_version: 'ase/promotion-release/v1alpha1',
          workflow_id: 'workflow-id',
          promotion_id: 'promotion-id',
          decision: 'REJECTED',
          lineage: [],
          claim_limit: 'fixture only',
        }),
      ),
    ).toThrowError(/confirmation/)
  })

  it('recognizes Evolution Release, report, index and real proposal evidence', () => {
    const report = parseReportText(
      JSON.stringify({
        schema_version: 'ase/evolution-evidence-report/v1alpha1',
        skill_versions: {},
        stages: {},
        claim_limit: 'descriptive only',
      }),
    )
    const manifest = parseReportText(
      JSON.stringify({
        schema_version: 'ase/evolution-evidence-release/v1alpha1',
        evolution_id: 'evolution',
        parent_content_sha256: 'parent',
        content_sha256: 'child',
        files: [],
      }),
    )
    const index = parseReportText(
      JSON.stringify({
        schema_version: 'ase/evolution-evidence-index/v1alpha1',
        artifacts: [],
      }),
    )
    const proposal = parseReportText(
      JSON.stringify({
        schema_version: 'ase/real-llm-proposal-smoke-result/v1alpha1',
        provider: 'deepseek',
        model: 'deepseek-chat',
        candidates: [],
      }),
    )
    expect([report.kind, manifest.kind, index.kind, proposal.kind]).toEqual([
      'evolution',
      'evolution',
      'evolution',
      'evolution',
    ])
  })
})
