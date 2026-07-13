import { describe, expect, it } from 'vitest'
import { parseReportFile, parseReportText } from '../parser'
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
})
