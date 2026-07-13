import { fireEvent, render, screen } from '@testing-library/vue'
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

  it('contains all five required research sections', () => {
    render(App)
    for (const label of [
      'Overview',
      'Paired Cases',
      'Trace & Diagnosis',
      'Benchmark Generation',
      'Skill Search',
    ]) {
      expect(screen.getByRole('button', { name: new RegExp(label) })).toBeTruthy()
    }
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
})
