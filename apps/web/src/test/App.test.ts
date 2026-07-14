import { fireEvent, render, screen } from '@testing-library/vue'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import App from '../App.vue'
import MetricCard from '../components/MetricCard.vue'

describe('dashboard page', () => {
  it('renders navigation, privacy boundary, empty state and import controls', async () => {
    render(App)
    expect(screen.getByText('AgentSkill Eval')).toBeTruthy()
    expect(screen.getByText('把已有报告带到一个安全的本地视图')).toBeTruthy()
    expect(screen.getAllByText('导入 JSON').length).toBeGreaterThan(0)
    await fireEvent.click(screen.getByRole('button', { name: /Trace & Diagnosis/ }))
    expect(screen.getByRole('heading', { name: 'Trace & Diagnosis' })).toBeTruthy()
  })

  it('contains all six required research sections', () => {
    render(App)
    for (const label of [
      'Overview',
      'Paired Cases',
      'Trace & Diagnosis',
      'Benchmark Generation',
      'Skill Search',
      'Promotion',
    ]) {
      expect(screen.getByRole('button', { name: new RegExp(label) })).toBeTruthy()
    }
  })

  it('keeps the promotion view available without inventing missing evidence', async () => {
    const { container } = render(App)
    const content = JSON.stringify({
      schema_version: 'ase/skill-version-promotion/v1alpha1',
      id: 'promotion-id',
      skill_name: 'python-review',
      target_version: '2.0.0',
      status: 'REJECTED',
      transitions: [{ sequence: 1, to_status: 'REJECTED', actor: 'reviewer' }],
      evidence: [],
      rejection_reason: '<script>unsafe()</script>',
    })
    const rejected = new File([content], 'promotion.json', { type: 'application/json' })
    Object.defineProperty(rejected, 'text', { value: async () => content })
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    Object.defineProperty(input, 'files', { value: [rejected], configurable: true })
    await fireEvent.update(input)
    await fireEvent.click(screen.getByRole('button', { name: /Promotion/ }))
    expect(screen.getAllByText('REJECTED').length).toBeGreaterThan(0)
    expect(screen.getByText(/Immutable SkillVersion Manifest unavailable/)).toBeTruthy()
    expect(container.querySelector('script')).toBeNull()
  })

  it('renders malicious HTML text without creating executable DOM', () => {
    const payload = '<img src=x onerror=alert(1)><script>window.pwned=true</script>'
    const { container } = render(MetricCard, {
      props: { label: 'UNTRUSTED', value: payload },
    })
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText(payload)).toBeTruthy()
  })

  it('renders Stage 4b lineage, review and immutable release evidence', async () => {
    const { container } = render(App)
    const names = ['promotion-workflow.json', 'promotion-release.json']
    const files = names.map((name) => {
      const content = readFileSync(resolve(process.cwd(), 'public', 'fixtures', name), 'utf8')
      const file = new File([content], name, { type: 'application/json' })
      Object.defineProperty(file, 'text', { value: async () => content })
      return file
    })
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    Object.defineProperty(input, 'files', { value: files, configurable: true })
    await fireEvent.update(input)
    await fireEvent.click(screen.getByRole('button', { name: /Promotion/ }))
    expect(await screen.findByText('Frozen winner provenance')).toBeTruthy()
    expect(screen.getByText('evolution_report')).toBeTruthy()
    expect(screen.getByText('fixture-human-reviewer')).toBeTruthy()
    expect(screen.getByText('IMMUTABLE PROMOTION RELEASE')).toBeTruthy()
    expect(screen.getByText('SkillVersion Manifest SHA-256')).toBeTruthy()
    expect(screen.getAllByText('APPROVED').length).toBeGreaterThan(0)
  })
})
