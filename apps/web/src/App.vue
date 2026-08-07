<script setup lang="ts">
import { computed, ref } from 'vue'
import Badge from './components/Badge.vue'
import EvolutionTimeline from './components/EvolutionTimeline.vue'
import MetricCard from './components/MetricCard.vue'
import ResearchChart from './components/ResearchChart.vue'
import { buildEvolutionTimeline } from './evolutionTimeline'
import { parsePatchFile, parseReportFile, parseReportText, parseSha256File } from './parser'
import type { CaseRow, DashboardState, ImportedReport } from './domain'
import { safeExternalUrl } from './security'

type Tab = 'overview' | 'cases' | 'trace' | 'benchmark' | 'search' | 'promotion' | 'evolution'
const tabs: { id: Tab; label: string; eyebrow: string }[] = [
  { id: 'overview', label: 'Overview', eyebrow: '01' },
  { id: 'cases', label: 'Paired Cases', eyebrow: '02' },
  { id: 'trace', label: 'Trace & Diagnosis', eyebrow: '03' },
  { id: 'benchmark', label: 'Benchmark Generation', eyebrow: '04' },
  { id: 'search', label: 'Skill Search', eyebrow: '05' },
  { id: 'promotion', label: 'Promotion', eyebrow: '06' },
  { id: 'evolution', label: 'Skill Evolution', eyebrow: '07' },
]
const state = ref<DashboardState>({ reports: [], loading: false, error: null })
const skillDiff = ref<string | null>(null)
const releaseManifestHash = ref<string | null>(null)
const active = ref<Tab>('overview')
const filter = ref({ outcome: 'all', category: 'all', group: 'all', query: '' })

const experiment = computed(() => state.value.reports.find((r) => r.kind === 'experiment'))
const benchmark = computed(() => state.value.reports.find((r) => r.kind === 'benchmark'))
const search = computed(() => state.value.reports.find((r) => r.kind === 'skill-search'))
const promotionReports = computed(() => state.value.reports.filter((r) => r.kind === 'promotion'))
const promotion = computed(
  () =>
    promotionReports.value.find((r) => r.schemaVersion === 'ase/promotion-workflow/v1alpha1') ??
    promotionReports.value.find((r) => r.schemaVersion === 'ase/skill-version-promotion/v1alpha1'),
)
const promotionRelease = computed(() =>
  promotionReports.value.find((r) => r.schemaVersion === 'ase/promotion-release/v1alpha1'),
)
const skillVersion = computed(() =>
  promotionReports.value.find((r) => r.schemaVersion === 'ase/skill-version/v1alpha1'),
)
const standaloneTraces = computed(() =>
  state.value.reports.filter((r) => ['trace', 'diagnosis', 'pair-diff'].includes(r.kind)),
)
const exp = computed<any>(() => experiment.value?.data.experiment ?? {})
const stats = computed<any>(() => experiment.value?.data.statistics ?? {})
const variants = computed<any[]>(() => (experiment.value?.data.variants as any[]) ?? [])
const traceIntel = computed<any>(
  () => experiment.value?.data.trace_intelligence ?? { traces: [], diagnoses: [], pair_diffs: [] },
)
const isDemo = computed(() => state.value.reports.some((r) => r.synthetic || r.simulated))
const evidenceClass = computed(
  () =>
    exp.value.protocol_snapshot?.evidence_class ??
    exp.value.protocol_snapshot?.evidence_mode ??
    null,
)
const evaluationSplit = computed(() => exp.value.protocol_snapshot?.evaluation_split ?? null)
const primaryEstimand = computed(
  () =>
    stats.value.primary_estimand ??
    (stats.value.primary_assignment_based ? 'assignment_based_conservative' : null),
)
const invalidCases = computed(
  () => stats.value.invalid_case_count ?? stats.value.wtl?.invalid ?? null,
)
const invalidRuns = computed(() => {
  const rows = stats.value.variants
  return Array.isArray(rows)
    ? rows.reduce((total: number, item: any) => total + (item.invalid_runs ?? 0), 0)
    : null
})
const evolutionTimeline = computed(() =>
  buildEvolutionTimeline(state.value.reports, releaseManifestHash.value),
)

const cases = computed<CaseRow[]>(() =>
  ((stats.value.cases ?? []) as any[]).map((item) => ({
    caseId: String(item.case_id ?? 'unknown'),
    category: String(item.category ?? 'uncategorized'),
    group: String(item.independence_group ?? 'unknown'),
    classification: String(item.classification ?? 'unknown'),
    controlStatus: String(item.control_status ?? (item.control_pass_rate >= 0.5 ? 'pass' : 'fail')),
    treatmentStatus: String(
      item.treatment_status ?? (item.treatment_pass_rate >= 0.5 ? 'pass' : 'fail'),
    ),
    controlScore: numberOrNull(item.control_score ?? item.control_pass_rate),
    treatmentScore: numberOrNull(item.treatment_score ?? item.treatment_pass_rate),
    tokenDelta: numberOrNull(item.token_delta),
    latencyDelta: numberOrNull(item.latency_delta),
    invalidReason: item.invalid_reason ? String(item.invalid_reason) : null,
  })),
)
const filteredCases = computed(() =>
  cases.value.filter((item) => {
    const outcome = item.invalidReason
      ? 'invalid'
      : item.classification.startsWith('tie')
        ? 'tie'
        : item.classification
    return (
      (filter.value.outcome === 'all' || outcome === filter.value.outcome) &&
      (filter.value.category === 'all' || item.category === filter.value.category) &&
      (filter.value.group === 'all' || item.group === filter.value.group) &&
      (!filter.value.query || item.caseId.toLowerCase().includes(filter.value.query.toLowerCase()))
    )
  }),
)
const categories = computed(() => [...new Set(cases.value.map((item) => item.category))])
const groups = computed(() => [...new Set(cases.value.map((item) => item.group))])
const benchmarkData = computed<any>(() => benchmark.value?.data ?? {})
const benchmarkCandidates = computed<any[]>(
  () => benchmarkData.value.candidates ?? (benchmarkData.value.id ? [benchmarkData.value] : []),
)
const searchData = computed<any>(() => search.value?.data ?? {})
const promotionData = computed<any>(() => promotion.value?.data ?? {})
const promotionReleaseData = computed<any>(() => promotionRelease.value?.data ?? {})
const skillVersionData = computed<any>(() => skillVersion.value?.data ?? {})
const isWorkflow = computed(
  () => promotion.value?.schemaVersion === 'ase/promotion-workflow/v1alpha1',
)
const promotionEvidence = computed<any[]>(() =>
  isWorkflow.value
    ? [promotionData.value.confirmation, promotionData.value.locked_test].filter(Boolean)
    : (promotionData.value.evidence ?? []),
)
const humanReview = computed<any>(() =>
  isWorkflow.value
    ? promotionData.value.human_review
    : [...(promotionData.value.transitions ?? [])]
        .reverse()
        .find((item: any) => ['APPROVED', 'REJECTED'].includes(item.to_status)),
)
const promotionLineage = computed<any[]>(
  () => promotionData.value.lineage ?? promotionReleaseData.value.lineage ?? [],
)
const promotionSteps = computed<any[]>(() => {
  if (!isWorkflow.value) return promotionData.value.transitions ?? []
  const steps: any[] = [{ sequence: 1, to_status: 'HANDOFF_ACCEPTED' }]
  if (promotionData.value.confirmation)
    steps.push({ sequence: 2, to_status: 'VALIDATION_CONFIRMED' })
  if (promotionData.value.locked_test)
    steps.push({ sequence: 3, to_status: 'LOCKED_TEST_COMPLETED' })
  if (promotionData.value.human_review)
    steps.push({ sequence: 4, to_status: promotionData.value.human_review.decision })
  if (promotionRelease.value)
    steps.push({ sequence: 5, to_status: `RELEASE_${promotionReleaseData.value.decision}` })
  return steps
})
const searchCandidates = computed<any[]>(
  () => searchData.value.candidates ?? (searchData.value.id ? [searchData.value] : []),
)
const traceItems = computed<any[]>(() => {
  const embedded = [
    ...(traceIntel.value.traces ?? []),
    ...(traceIntel.value.diagnoses ?? []),
    ...(traceIntel.value.pair_diffs ?? []),
  ]
  return [...embedded, ...standaloneTraces.value.map((item) => item.data)]
})

const passOption = computed(() => ({
  backgroundColor: 'transparent',
  textStyle: { color: '#a9b2c3' },
  tooltip: { trigger: 'axis' },
  grid: { left: 35, right: 20, top: 25, bottom: 32 },
  xAxis: {
    type: 'category',
    data: ['without-Skill', 'with-Skill'],
    axisLine: { lineStyle: { color: '#3c4658' } },
  },
  yAxis: {
    type: 'value',
    max: 1,
    axisLabel: { formatter: (v: number) => `${v * 100}%` },
    splitLine: { lineStyle: { color: '#242b38' } },
  },
  series: [
    {
      type: 'bar',
      barWidth: 44,
      data: [
        stats.value.primary_assignment_based?.control_pass_rate ?? 0,
        stats.value.primary_assignment_based?.treatment_pass_rate ?? 0,
      ],
      itemStyle: {
        color: (p: any) => (p.dataIndex ? '#5ee2a0' : '#69758a'),
        borderRadius: [5, 5, 0, 0],
      },
    },
  ],
}))
const paretoOption = computed(() => ({
  backgroundColor: 'transparent',
  textStyle: { color: '#a9b2c3' },
  tooltip: { trigger: 'item' },
  grid: { left: 46, right: 18, top: 24, bottom: 38 },
  xAxis: { name: 'Tokens', type: 'value', splitLine: { lineStyle: { color: '#242b38' } } },
  yAxis: {
    name: 'Score',
    type: 'value',
    min: 0,
    max: 1,
    splitLine: { lineStyle: { color: '#242b38' } },
  },
  series: [
    {
      type: 'scatter',
      symbolSize: 13,
      data: searchCandidates.value
        .filter((c) => c.full_mean_score != null)
        .map((c) => ({
          name: c.name,
          value: [c.full_tokens, c.full_mean_score],
          itemStyle: {
            color: c.candidate_id === searchData.value.winner_id ? '#5ee2a0' : '#7a8cff',
          },
        })),
    },
  ],
}))

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}
function pct(value: unknown, signed = false): string {
  const n = numberOrNull(value)
  return n === null ? 'N/A' : `${signed && n > 0 ? '+' : ''}${(n * 100).toFixed(1)}%`
}
function compact(value: unknown): string {
  const n = numberOrNull(value)
  return n === null
    ? 'N/A'
    : Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(n)
}
function delta(value: number | null, suffix = ''): string {
  return value === null ? 'N/A' : `${value > 0 ? '+' : ''}${value.toLocaleString()}${suffix}`
}
function outcomeTone(value: string) {
  return value === 'win' ||
    value === 'pass' ||
    value === 'FROZEN' ||
    value === 'PUBLISHED' ||
    value === 'APPROVED' ||
    value === 'RELEASE_APPROVED'
    ? 'good'
    : value === 'loss' || value === 'fail' || value === 'REJECTED'
      ? 'bad'
      : ('neutral' as const)
}
function shortId(value: unknown): string {
  const text = String(value ?? '—')
  return text.length > 18 ? `${text.slice(0, 8)}…${text.slice(-6)}` : text
}
function variantLabel(role: string) {
  const item = variants.value.find((v) => v.role === role)
  return item
    ? `${item.name} · ${item.runner_snapshot?.name ?? 'runner'} / ${item.agent_snapshot?.model ?? 'agent'}`
    : 'N/A'
}
function available(value: unknown): string {
  return value === undefined || value === null || value === ''
    ? 'Unavailable / not provided'
    : String(value)
}

async function importFiles(event: Event) {
  const input = event.target as HTMLInputElement
  if (!input.files?.length) return
  state.value.loading = true
  state.value.error = null
  try {
    for (const file of Array.from(input.files)) {
      if (/\.(?:patch|diff)$/i.test(file.name)) skillDiff.value = await parsePatchFile(file)
      else if (file.name === 'release-manifest.sha256')
        releaseManifestHash.value = await parseSha256File(file)
      else if (file.name.toLowerCase().endsWith('.json'))
        state.value.reports.push(await parseReportFile(file))
    }
  } catch (error) {
    state.value.error = error instanceof Error ? error.message : '导入失败'
  } finally {
    state.value.loading = false
    input.value = ''
  }
}
async function loadDemo() {
  state.value.loading = true
  state.value.error = null
  try {
    const files = [
      'paired-experiment.json',
      'trace-diagnosis.json',
      'benchmark-generation.json',
      'skill-search.json',
      'promotion.json',
      'skill-version.json',
      'promotion-workflow.json',
      'promotion-release.json',
      'evolution-evidence-report.json',
      'evolution-release-manifest.json',
      'evolution-evidence-index.json',
    ]
    const loaded: ImportedReport[] = []
    for (const file of files) {
      const response = await fetch(`/fixtures/${file}`)
      if (!response.ok) throw new Error(`Fixture 加载失败：${file}`)
      loaded.push(parseReportText(await response.text(), `DEMO · ${file}`))
    }
    const diffResponse = await fetch('/fixtures/skill-diff.patch')
    if (!diffResponse.ok) throw new Error('Fixture 加载失败：skill-diff.patch')
    skillDiff.value = await diffResponse.text()
    const hashResponse = await fetch('/fixtures/release-manifest.sha256')
    if (!hashResponse.ok) throw new Error('Fixture 加载失败：release-manifest.sha256')
    releaseManifestHash.value = (await hashResponse.text()).trim()
    state.value.reports = loaded
  } catch (error) {
    state.value.error = error instanceof Error ? error.message : 'Fixture 加载失败'
  } finally {
    state.value.loading = false
  }
}
async function loadProposalDemo() {
  state.value.loading = true
  state.value.error = null
  try {
    const response = await fetch('/fixtures/real-llm-proposal-smoke.json')
    if (!response.ok) throw new Error('Fixture 加载失败：real-llm-proposal-smoke.json')
    state.value.reports = [
      parseReportText(await response.text(), 'SANITIZED · Stage 1 DeepSeek Proposal smoke'),
    ]
    skillDiff.value = null
    releaseManifestHash.value = null
    active.value = 'evolution'
  } catch (error) {
    state.value.error = error instanceof Error ? error.message : 'Fixture 加载失败'
  } finally {
    state.value.loading = false
  }
}
function clearData() {
  state.value = { reports: [], loading: false, error: null }
  skillDiff.value = null
  releaseManifestHash.value = null
  active.value = 'overview'
}
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">A·E</div>
        <div><strong>AgentSkill Eval</strong><span>RESEARCH CONSOLE</span></div>
      </div>
      <nav aria-label="Dashboard sections">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          :class="{ active: active === tab.id }"
          @click="active = tab.id"
        >
          <span>{{ tab.eyebrow }}</span
          >{{ tab.label }}
        </button>
      </nav>
      <div class="trust-note">
        <span class="pulse" />LOCAL / READ-ONLY
        <p>Reports remain in this browser tab. No upload, execution, or Manifest writes.</p>
      </div>
    </aside>
    <main>
      <header class="topbar">
        <div>
          <p class="kicker">AGENTOPS / EVALUATION INTELLIGENCE</p>
          <h1>{{ tabs.find((t) => t.id === active)?.label }}</h1>
        </div>
        <div class="actions">
          <button class="button ghost" :disabled="!state.reports.length" @click="clearData">
            清除本地数据</button
          ><label class="button ghost"
            ><input type="file" multiple webkitdirectory directory @change="importFiles" />导入
            Release 目录</label
          ><label class="button primary"
            ><input
              type="file"
              accept="application/json,.json,.patch,.diff,.sha256"
              multiple
              @change="importFiles"
            />导入证据</label
          >
        </div>
      </header>
      <div v-if="isDemo" class="simulation-banner">
        <Badge tone="warn">SYNTHETIC / SIMULATED</Badge
        ><span>演示数据仅用于验证界面和控制链路，不构成 Agent、Skill 或泛化性能证据。</span>
      </div>
      <div v-if="state.error" class="error-state" role="alert">
        <strong>无法导入报告</strong><span>{{ state.error }}</span
        ><button @click="state.error = null">关闭</button>
      </div>
      <div v-if="state.loading" class="loading-state">
        <span />
        <p>正在本地校验报告…</p>
      </div>
      <section v-else-if="!state.reports.length" class="empty-state">
        <div class="empty-grid" />
        <p class="kicker">NO EVIDENCE LOADED</p>
        <h2>把已有报告带到一个安全的本地视图</h2>
        <p>
          支持 report.json、search-report.json、Benchmark
          job/report、TraceManifest、FailureDiagnosis 与 PairTraceDiff、Promotion 和 SkillVersion
          Manifest。不会上传或执行其中的任何内容。
        </p>
        <div>
          <label class="button primary"
            ><input
              type="file"
              accept="application/json,.json,.patch,.diff,.sha256"
              multiple
              @change="importFiles"
            />选择证据文件</label
          ><label class="button ghost"
            ><input type="file" multiple webkitdirectory directory @change="importFiles" />选择
            Release 目录</label
          ><button class="button ghost" @click="loadDemo">加载 Synthetic Demo</button>
          <button class="button ghost" @click="loadProposalDemo">加载 Stage 1 脱敏 Proposal</button>
        </div>
        <small>单文件上限 5 MB · 深度/数组/字符串限制 · Secret 字段自动遮蔽</small>
      </section>

      <template v-else>
        <section v-if="active === 'overview'" class="content-stack">
          <div v-if="experiment" class="identity-row">
            <div>
              <span>EXPERIMENT ID</span><code>{{ exp.id }}</code>
            </div>
            <div>
              <span>DATASET</span
              ><strong>{{ exp.dataset_name ?? exp.dataset_version_id ?? 'N/A' }}</strong>
            </div>
            <div>
              <span>EVIDENCE CLASS</span
              ><Badge :tone="experiment.simulated ? 'warn' : 'info'">{{
                available(evidenceClass)
              }}</Badge>
            </div>
          </div>
          <div v-else class="inline-empty">尚未导入 Experiment report.json。</div>
          <div class="metric-grid">
            <MetricCard
              label="WITHOUT-SKILL PASS RATE"
              :value="pct(stats.primary_assignment_based?.control_pass_rate)"
              :detail="variantLabel('control')"
            />
            <MetricCard
              label="WITH-SKILL PASS RATE"
              :value="pct(stats.primary_assignment_based?.treatment_pass_rate)"
              :detail="variantLabel('treatment')"
            />
            <MetricCard
              label="ABSOLUTE GAIN"
              :value="pct(stats.primary_assignment_based?.absolute_gain, true)"
              detail="assignment-based / group weighted"
              accent
            />
            <MetricCard
              label="VALID / INVALID BLOCKS"
              :value="`${pct(stats.valid_block_ratio)} / ${pct(stats.valid_block_ratio == null ? null : 1 - stats.valid_block_ratio)}`"
              :detail="`${available(invalidCases)} invalid cases / ${available(invalidRuns)} invalid runs`"
            />
          </div>
          <div class="two-column">
            <article class="panel">
              <header>
                <div>
                  <p class="kicker">EFFECTIVENESS</p>
                  <h2>Paired pass rate</h2>
                </div>
                <Badge :tone="stats.inference_ready ? 'good' : 'warn'">{{
                  stats.inference_ready ? 'INFERENCE READY' : 'DESCRIPTIVE ONLY'
                }}</Badge>
              </header>
              <ResearchChart :option="passOption" label="Control and treatment pass rates" />
            </article>
            <article class="panel">
              <header>
                <div>
                  <p class="kicker">PROTOCOL</p>
                  <h2>Evidence envelope</h2>
                </div>
              </header>
              <dl class="data-list">
                <div>
                  <dt>Primary estimand</dt>
                  <dd>{{ available(primaryEstimand) }}</dd>
                </div>
                <div>
                  <dt>Evaluation split</dt>
                  <dd>{{ available(evaluationSplit) }}</dd>
                </div>
                <div>
                  <dt>Invalid cases / runs</dt>
                  <dd>{{ available(invalidCases) }} / {{ available(invalidRuns) }}</dd>
                </div>
                <div>
                  <dt>Independence groups</dt>
                  <dd>{{ stats.independence_group_count ?? 'N/A' }}</dd>
                </div>
                <div>
                  <dt>Claim limit</dt>
                  <dd>
                    {{
                      exp.protocol_snapshot?.claim_limit ?? stats.inference_note ?? 'Not declared'
                    }}
                  </dd>
                </div>
                <div>
                  <dt>Token overhead</dt>
                  <dd>{{ pct(stats.tokens?.relative_overhead, true) }}</dd>
                </div>
                <div>
                  <dt>Latency overhead</dt>
                  <dd>{{ pct(stats.latency_ms?.relative_overhead, true) }}</dd>
                </div>
                <div>
                  <dt>Cost / success</dt>
                  <dd>{{ compact(stats.variants?.[1]?.cost_per_success_microusd) }} µUSD</dd>
                </div>
                <div>
                  <dt>Weighting</dt>
                  <dd>{{ stats.weighting ?? 'N/A' }}</dd>
                </div>
              </dl>
            </article>
          </div>
          <article class="panel">
            <header>
              <div>
                <p class="kicker">IMPORTED EVIDENCE</p>
                <h2>Report inventory</h2>
              </div>
              <span class="muted">{{ state.reports.length }} local files</span>
            </header>
            <div class="inventory">
              <div v-for="report in state.reports" :key="report.id">
                <Badge tone="info">{{ report.kind }}</Badge
                ><strong>{{ report.name }}</strong
                ><code>{{ report.schemaVersion }}</code
                ><span>{{ new Date(report.importedAt).toLocaleTimeString() }}</span>
              </div>
            </div>
          </article>
        </section>

        <section v-else-if="active === 'cases'" class="content-stack">
          <div class="summary-strip">
            <div>
              <span>WIN</span><strong class="positive">{{ stats.wtl?.win ?? 0 }}</strong>
            </div>
            <div>
              <span>TIE</span
              ><strong>{{
                (stats.wtl?.tie_positive ?? 0) + (stats.wtl?.tie_negative ?? 0)
              }}</strong>
            </div>
            <div>
              <span>LOSS</span><strong class="negative">{{ stats.wtl?.loss ?? 0 }}</strong>
            </div>
            <div>
              <span>INVALID</span><strong>{{ available(invalidCases) }}</strong>
            </div>
            <div>
              <span>VISIBLE</span><strong>{{ filteredCases.length }} / {{ cases.length }}</strong>
            </div>
          </div>
          <div class="filters">
            <input
              v-model="filter.query"
              placeholder="Search case ID"
              aria-label="Search case ID"
            /><select v-model="filter.outcome">
              <option value="all">All outcomes</option>
              <option value="win">Win</option>
              <option value="tie">Tie</option>
              <option value="loss">Loss</option>
              <option value="invalid">Invalid</option></select
            ><select v-model="filter.category">
              <option value="all">All categories</option>
              <option v-for="item in categories" :key="item">{{ item }}</option></select
            ><select v-model="filter.group">
              <option value="all">All independence groups</option>
              <option v-for="item in groups" :key="item">{{ item }}</option>
            </select>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Outcome</th>
                  <th>Case / category</th>
                  <th>Baseline → Treatment</th>
                  <th>Score Δ</th>
                  <th>Token Δ</th>
                  <th>Latency Δ</th>
                  <th>Independence group</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in filteredCases" :key="item.caseId">
                  <td>
                    <Badge
                      :tone="outcomeTone(item.invalidReason ? 'invalid' : item.classification)"
                      >{{ item.invalidReason ? 'invalid' : item.classification }}</Badge
                    >
                  </td>
                  <td>
                    <strong>{{ item.caseId }}</strong
                    ><small>{{ item.category }}</small
                    ><em v-if="item.invalidReason">{{ item.invalidReason }}</em>
                  </td>
                  <td>
                    <Badge :tone="outcomeTone(item.controlStatus)">{{ item.controlStatus }}</Badge
                    ><span class="arrow">→</span
                    ><Badge :tone="outcomeTone(item.treatmentStatus)">{{
                      item.treatmentStatus
                    }}</Badge>
                  </td>
                  <td>
                    {{
                      delta(
                        item.controlScore === null || item.treatmentScore === null
                          ? null
                          : item.treatmentScore - item.controlScore,
                      )
                    }}
                  </td>
                  <td>{{ delta(item.tokenDelta) }}</td>
                  <td>{{ delta(item.latencyDelta, ' ms') }}</td>
                  <td>
                    <code>{{ item.group }}</code>
                  </td>
                </tr>
                <tr v-if="!filteredCases.length">
                  <td colspan="7" class="table-empty">没有符合当前筛选条件的 Case。</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section v-else-if="active === 'trace'" class="content-stack">
          <div class="section-note">
            <strong>Observable evidence only</strong
            ><span>仅展示平台可观测事件和确定性诊断；不会展示或推断模型隐藏思维链。</span>
          </div>
          <div v-if="!traceItems.length" class="inline-empty">
            尚未导入 TraceManifest 或 FailureDiagnosis。
          </div>
          <article v-for="(item, index) in traceItems" :key="index" class="panel trace-panel">
            <header>
              <div>
                <p class="kicker">RUN {{ shortId(item.run_id ?? item.pair_block_id) }}</p>
                <h2>
                  {{
                    item.events
                      ? 'Run timeline'
                      : item.findings
                        ? `Diagnosis · ${item.status}`
                        : 'Pair trace diff'
                  }}
                </h2>
              </div>
              <Badge :tone="item.status === 'abstained' ? 'warn' : 'info'">{{
                item.status ?? item.schema_version
              }}</Badge>
            </header>
            <div v-if="item.capabilities" class="capabilities">
              <div v-for="cap in item.capabilities" :key="cap.name">
                <Badge :tone="cap.availability === 'observed' ? 'good' : 'warn'">{{
                  cap.availability
                }}</Badge
                ><strong>{{ cap.name }}</strong
                ><span>{{ cap.reason ?? cap.source }}</span>
              </div>
            </div>
            <ol v-if="item.events" class="timeline">
              <li v-for="event in item.events" :key="event.sequence_no">
                <div class="event-dot" :class="event.source" />
                <time>{{ new Date(event.occurred_at).toLocaleTimeString() }}</time>
                <div>
                  <strong>{{ event.kind }}</strong
                  ><span>{{ event.source }} · {{ event.status ?? 'observed' }}</span>
                  <pre>{{ JSON.stringify(event.summary, null, 2) }}</pre>
                </div>
              </li>
            </ol>
            <div v-if="item.findings" class="findings">
              <div v-for="finding in item.findings" :key="finding.rule_id">
                <Badge :tone="finding.label === 'UNKNOWN' ? 'warn' : 'bad'">{{
                  finding.label
                }}</Badge
                ><strong>{{ Math.round(finding.confidence * 100) }}% · {{ finding.role }}</strong>
                <p>{{ finding.rationale }}</p>
                <span>Evidence #{{ finding.evidence_sequence_nos.join(', #') || 'none' }}</span>
              </div>
            </div>
            <div v-if="item.event_count_deltas" class="delta-grid">
              <div v-for="entry in item.event_count_deltas" :key="entry.kind">
                <span>{{ entry.kind }}</span
                ><strong>{{ entry.control_count }} → {{ entry.treatment_count }}</strong
                ><Badge :tone="entry.delta === 0 ? 'neutral' : 'info'">{{
                  delta(entry.delta)
                }}</Badge>
              </div>
            </div>
          </article>
        </section>

        <section v-else-if="active === 'benchmark'" class="content-stack">
          <div v-if="benchmark" class="identity-row">
            <div>
              <span>BENCHMARK JOB</span><code>{{ benchmarkData.job?.id ?? benchmarkData.id }}</code>
            </div>
            <div>
              <span>STATUS</span
              ><Badge tone="good">{{ benchmarkData.job?.status ?? benchmarkData.status }}</Badge>
            </div>
            <div>
              <span>DATASET VERSION HASH</span
              ><code>{{
                shortId(benchmarkData.dataset_version?.content_sha256 ?? 'Not published')
              }}</code>
            </div>
          </div>
          <div v-else class="inline-empty">尚未导入 Benchmark generation job/report JSON。</div>
          <article
            v-for="candidate in benchmarkCandidates"
            :key="candidate.id"
            class="panel candidate-card"
          >
            <header>
              <div>
                <p class="kicker">{{ candidate.key ?? shortId(candidate.id) }}</p>
                <h2>{{ candidate.task ?? 'Benchmark candidate' }}</h2>
              </div>
              <Badge :tone="outcomeTone(candidate.status)">{{ candidate.status }}</Badge>
            </header>
            <div class="state-track">
              <span
                v-for="step in candidate.transitions ?? []"
                :key="step.sequence"
                :class="{ reached: true, rejected: step.to_status === 'REJECTED' }"
                >{{ step.to_status }}</span
              >
            </div>
            <div class="candidate-grid">
              <div>
                <span>Source repository</span
                ><a
                  v-if="safeExternalUrl(candidate.provenance?.repository_url)"
                  :href="safeExternalUrl(candidate.provenance.repository_url)!"
                  target="_blank"
                  rel="noopener noreferrer"
                  >{{ candidate.provenance.repository_url }}</a
                ><code v-else>{{ candidate.provenance?.repository_url ?? 'N/A' }}</code>
              </div>
              <div>
                <span>Before → after</span
                ><code
                  >{{ shortId(candidate.provenance?.before_commit) }} →
                  {{ shortId(candidate.provenance?.after_commit) }}</code
                >
              </div>
              <div>
                <span>License / provenance</span
                ><strong
                  >{{ candidate.provenance?.license_spdx ?? 'N/A' }} ·
                  {{ candidate.provenance?.source_type ?? 'N/A' }}</strong
                >
              </div>
              <div>
                <span>Dedup / human review</span
                ><strong
                  >{{
                    candidate.duplicate_of
                      ? `duplicate of ${shortId(candidate.duplicate_of)}`
                      : 'unique'
                  }}
                  · {{ candidate.review_decision ?? 'pending' }}</strong
                >
              </div>
            </div>
            <div class="gate-grid">
              <div v-for="gate in candidate.quality_gates ?? []" :key="gate.name">
                <span :class="gate.passed ? 'gate-pass' : 'gate-fail'">{{
                  gate.passed ? '✓' : '×'
                }}</span
                ><strong>{{ gate.name }}</strong
                ><small>{{ gate.detail }}</small>
              </div>
            </div>
            <div v-if="candidate.rejection_reasons?.length" class="rejected-reason">
              Rejected: {{ candidate.rejection_reasons.join(' · ') }}
            </div>
          </article>
        </section>

        <section v-else-if="active === 'search'" class="content-stack">
          <div v-if="search" class="simulation-banner">
            <Badge :tone="searchData.simulated ? 'warn' : 'info'">{{
              searchData.simulated ? 'SIMULATED SEARCH' : 'VALIDATION ONLY'
            }}</Badge
            ><span>{{ searchData.claim_limit }}</span
            ><strong>locked_test_accessed={{ searchData.locked_test_accessed }}</strong>
          </div>
          <div v-else class="inline-empty">尚未导入 search-report.json。</div>
          <div class="two-column">
            <article class="panel">
              <header>
                <div>
                  <p class="kicker">MULTI-OBJECTIVE</p>
                  <h2>Pareto frontier</h2>
                </div>
                <Badge tone="good">Winner highlighted</Badge>
              </header>
              <ResearchChart :option="paretoOption" label="Candidate score and token Pareto plot" />
            </article>
            <article class="panel">
              <header>
                <div>
                  <p class="kicker">SUCCESSIVE HALVING</p>
                  <h2>Search funnel</h2>
                </div>
              </header>
              <div class="funnel">
                <div>
                  <strong>{{ searchCandidates.length }}</strong
                  ><span>Created</span>
                </div>
                <div>
                  <strong>{{
                    searchCandidates.filter((c) => c.subset_pass_rate != null).length
                  }}</strong
                  ><span>Subset scored</span>
                </div>
                <div>
                  <strong>{{
                    searchCandidates.filter((c) => c.full_pass_rate != null).length
                  }}</strong
                  ><span>Full validation</span>
                </div>
                <div><strong>1</strong><span>Frozen winner</span></div>
              </div>
            </article>
          </div>
          <div class="candidate-table">
            <div class="candidate-row candidate-head">
              <span>Candidate</span><span>Origin / status</span><span>Subset</span><span>Full</span
              ><span>Tokens / Skill bytes</span><span>Decision</span>
            </div>
            <div
              v-for="candidate in searchCandidates"
              :key="candidate.candidate_id ?? candidate.id"
              class="candidate-row"
              :class="{ winner: (candidate.candidate_id ?? candidate.id) === searchData.winner_id }"
            >
              <span
                ><strong>{{ candidate.name }}</strong
                ><code>{{ shortId(candidate.candidate_id ?? candidate.id) }}</code></span
              ><span
                ><Badge tone="info">{{ candidate.origin }}</Badge
                ><Badge :tone="outcomeTone(candidate.status)">{{ candidate.status }}</Badge></span
              ><span
                >{{ pct(candidate.subset_pass_rate)
                }}<small>{{
                  candidate.subset_mean_score == null
                    ? 'not evaluated'
                    : `score ${candidate.subset_mean_score.toFixed(2)}`
                }}</small></span
              ><span
                >{{ pct(candidate.full_pass_rate)
                }}<small>{{
                  candidate.full_mean_score == null
                    ? 'not promoted'
                    : `score ${candidate.full_mean_score.toFixed(2)}`
                }}</small></span
              ><span
                >{{ compact(candidate.full_tokens)
                }}<small>{{ compact(candidate.content_bytes) }} bytes</small></span
              ><span
                ><Badge
                  v-if="(candidate.candidate_id ?? candidate.id) === searchData.winner_id"
                  tone="good"
                  >WINNER</Badge
                ><em v-else>{{
                  candidate.elimination_reason ??
                  (candidate.pareto_dominated_by?.length ? 'Pareto dominated' : 'Comparator')
                }}</em></span
              >
            </div>
          </div>
        </section>

        <EvolutionTimeline
          v-else-if="active === 'evolution'"
          :model="evolutionTimeline"
          :skill-diff="skillDiff"
        />

        <section v-else class="content-stack promotion-view">
          <div class="section-note">
            <strong>Read-only promotion evidence</strong
            ><span
              >仅展示导入的可观测字段；缺失字段明确标记 unavailable，不推断审核或执行结果。</span
            >
          </div>
          <div v-if="promotion" class="identity-row">
            <div>
              <span>PROMOTION</span><code>{{ promotionData.id }}</code>
            </div>
            <div>
              <span>SKILL / TARGET VERSION</span
              ><strong>{{ promotionData.skill_name }}@{{ promotionData.target_version }}</strong>
            </div>
            <div>
              <span>STATUS</span
              ><Badge :tone="outcomeTone(promotionData.status)">{{ promotionData.status }}</Badge>
            </div>
          </div>
          <div v-else class="inline-empty">
            Promotion state unavailable。请导入 ase/promotion-workflow/v1alpha1 或 Stage 4a
            Promotion Manifest。
          </div>

          <div v-if="promotion" class="two-column">
            <article class="panel">
              <header>
                <div>
                  <p class="kicker">PROPOSAL LINEAGE</p>
                  <h2>Frozen winner provenance</h2>
                </div>
                <Badge :tone="promotion.simulated ? 'warn' : 'info'">{{
                  promotion.simulated ? 'FIXTURE / FAKE' : 'OBSERVED'
                }}</Badge>
              </header>
              <dl class="data-list">
                <div>
                  <dt>Evolution / proposal</dt>
                  <dd>
                    <code>{{ available(promotionData.evolution_id) }}</code>
                  </dd>
                </div>
                <div>
                  <dt>Optimization job</dt>
                  <dd>
                    <code>{{ available(promotionData.optimization_job_id) }}</code>
                  </dd>
                </div>
                <div>
                  <dt>Winner candidate</dt>
                  <dd>
                    <code>{{ available(promotionData.winner_candidate_id) }}</code>
                  </dd>
                </div>
                <div>
                  <dt>Parent Skill SHA-256</dt>
                  <dd>
                    <code>{{ available(promotionData.base_skill_sha256) }}</code>
                  </dd>
                </div>
                <div>
                  <dt>Winner Skill SHA-256</dt>
                  <dd>
                    <code>{{ available(promotionData.winner_skill_sha256) }}</code>
                  </dd>
                </div>
                <div>
                  <dt>Published SkillVersion</dt>
                  <dd>
                    <code>{{
                      available(
                        promotionData.published_skill_version_id ??
                          promotionData.skill_version_manifest_sha256 ??
                          promotionReleaseData.skill_version_manifest_sha256,
                      )
                    }}</code>
                  </dd>
                </div>
              </dl>
            </article>
            <article class="panel">
              <header>
                <div>
                  <p class="kicker">HUMAN REVIEW</p>
                  <h2>Decision</h2>
                </div>
                <Badge
                  :tone="
                    outcomeTone(
                      humanReview?.decision ?? humanReview?.to_status ?? promotionData.status,
                    )
                  "
                  >{{ humanReview?.decision ?? humanReview?.to_status ?? 'UNAVAILABLE' }}</Badge
                >
              </header>
              <dl class="data-list">
                <div>
                  <dt>Actor</dt>
                  <dd>{{ available(humanReview?.reviewer ?? humanReview?.actor) }}</dd>
                </div>
                <div>
                  <dt>Decision time</dt>
                  <dd>{{ available(humanReview?.reviewed_at ?? humanReview?.occurred_at) }}</dd>
                </div>
                <div>
                  <dt>Reason</dt>
                  <dd>{{ available(humanReview?.reason ?? promotionData.rejection_reason) }}</dd>
                </div>
                <div>
                  <dt>Evidence class</dt>
                  <dd>{{ promotion.simulated ? 'Fake / fixture only' : 'Observed evidence' }}</dd>
                </div>
              </dl>
            </article>
          </div>

          <article v-if="promotion && promotionLineage.length" class="panel">
            <header>
              <div>
                <p class="kicker">CONTENT-ADDRESSED LINEAGE</p>
                <h2>Handoff → evolution → regression → hypotheses → search</h2>
              </div>
              <code>{{ shortId(promotionData.lineage_sha256) }}</code>
            </header>
            <div class="lineage-grid">
              <div v-for="artifact in promotionLineage" :key="artifact.role">
                <Badge tone="info">{{ artifact.role }}</Badge>
                <code>{{ shortId(artifact.sha256) }}</code>
                <small>{{ compact(artifact.size_bytes) }} bytes</small>
              </div>
            </div>
          </article>

          <article v-if="promotion" class="panel">
            <header>
              <div>
                <p class="kicker">CONTROL FLOW</p>
                <h2>Confirmation → locked test → review → publication</h2>
              </div>
            </header>
            <div class="state-track promotion-track">
              <span
                v-for="step in promotionSteps"
                :key="step.sequence"
                class="reached"
                :class="{ rejected: step.to_status === 'REJECTED' }"
                :title="available(step.reason)"
                >{{ step.to_status }}</span
              >
            </div>
            <div class="promotion-evidence-grid">
              <div v-for="stage in ['validation_confirm', 'locked_test']" :key="stage">
                <template v-if="promotionEvidence.find((item) => item.stage === stage)">
                  <Badge
                    :tone="
                      promotionEvidence.find((item) => item.stage === stage).decision ===
                      'CONFIRMED'
                        ? 'good'
                        : 'bad'
                    "
                  >
                    {{ promotionEvidence.find((item) => item.stage === stage).decision }}
                  </Badge>
                  <strong>{{ stage }}</strong>
                  <code>{{
                    available(
                      promotionEvidence.find((item) => item.stage === stage)
                        .final_evaluation_job_id,
                    )
                  }}</code>
                  <small
                    >report
                    {{
                      shortId(promotionEvidence.find((item) => item.stage === stage).report_sha256)
                    }}
                    · validator
                    {{
                      available(
                        promotionEvidence.find((item) => item.stage === stage).validator_version,
                      )
                    }}</small
                  >
                </template>
                <template v-else>
                  <Badge tone="warn">UNAVAILABLE</Badge><strong>{{ stage }}</strong
                  ><small>Evidence field unavailable</small>
                </template>
              </div>
            </div>
          </article>

          <article v-if="promotionRelease" class="panel">
            <header>
              <div>
                <p class="kicker">IMMUTABLE PROMOTION RELEASE</p>
                <h2>Decision · {{ promotionReleaseData.decision }}</h2>
              </div>
              <Badge :tone="outcomeTone(promotionReleaseData.decision)">{{
                promotionReleaseData.decision
              }}</Badge>
            </header>
            <div class="candidate-grid">
              <div>
                <span>Workflow ID</span
                ><code>{{ available(promotionReleaseData.workflow_id) }}</code>
              </div>
              <div>
                <span>Promotion ID</span
                ><code>{{ available(promotionReleaseData.promotion_id) }}</code>
              </div>
              <div>
                <span>Parent → winner hash</span
                ><code
                  >{{ shortId(promotionReleaseData.parent_skill_sha256) }} →
                  {{ shortId(promotionReleaseData.winner_skill_sha256) }}</code
                >
              </div>
              <div>
                <span>SkillVersion Manifest SHA-256</span
                ><code>{{ available(promotionReleaseData.skill_version_manifest_sha256) }}</code>
              </div>
              <div>
                <span>Diff SHA-256</span
                ><code>{{ available(promotionReleaseData.diff_sha256) }}</code>
              </div>
              <div>
                <span>Released at</span
                ><strong>{{ available(promotionReleaseData.released_at) }}</strong>
              </div>
            </div>
            <div class="claim-limit">
              <strong>Claim limit</strong
              ><span>{{ available(promotionReleaseData.claim_limit) }}</span>
            </div>
          </article>

          <article v-if="skillVersion" class="panel">
            <header>
              <div>
                <p class="kicker">IMMUTABLE SKILLVERSION</p>
                <h2>{{ skillVersionData.skill_name }}@{{ skillVersionData.version }}</h2>
              </div>
              <Badge :tone="skillVersionData.simulated_evidence ? 'warn' : 'good'">PUBLISHED</Badge>
            </header>
            <div class="candidate-grid">
              <div>
                <span>Manifest ID</span><code>{{ available(skillVersionData.id) }}</code>
              </div>
              <div>
                <span>Promotion ID</span><code>{{ available(skillVersionData.promotion_id) }}</code>
              </div>
              <div>
                <span>Parent → content hash</span
                ><code
                  >{{ shortId(skillVersionData.parent_content_sha256) }} →
                  {{ shortId(skillVersionData.content_sha256) }}</code
                >
              </div>
              <div>
                <span>Diff SHA-256</span><code>{{ available(skillVersionData.diff_sha256) }}</code>
              </div>
              <div>
                <span>Published at</span
                ><strong>{{ available(skillVersionData.published_at) }}</strong>
              </div>
              <div>
                <span>Content bytes</span
                ><strong>{{ compact(skillVersionData.content_bytes) }}</strong>
              </div>
            </div>
            <div class="claim-limit">
              <strong>Claim limit</strong><span>{{ available(skillVersionData.claim_limit) }}</span>
            </div>
          </article>
          <div v-if="!skillVersion && !promotionRelease" class="inline-empty">
            Immutable SkillVersion Manifest unavailable；REJECTED 或尚未发布的 Promotion 不应生成
            Manifest。
          </div>
        </section>
      </template>
    </main>
  </div>
</template>
