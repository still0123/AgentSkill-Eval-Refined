import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { parseReportText } from '../parser'

describe('synthetic demo fixtures', () => {
  it('loads every fixture and keeps demo evidence clearly marked', () => {
    const names = [
      'paired-experiment.json',
      'trace-diagnosis.json',
      'benchmark-generation.json',
      'skill-search.json',
    ]
    const reports = names.map((name) =>
      parseReportText(
        readFileSync(resolve(process.cwd(), 'public', 'fixtures', name), 'utf8'),
        name,
      ),
    )
    expect(reports.map((item) => item.kind)).toEqual([
      'experiment',
      'diagnosis',
      'benchmark',
      'skill-search',
    ])
    expect(reports.every((item) => item.synthetic || item.simulated)).toBe(true)
  })
})
