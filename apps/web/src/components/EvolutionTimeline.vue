<script setup lang="ts">
import { computed, ref } from 'vue'
import type { EvolutionStatus, EvolutionTimelineModel } from '../evolutionTimeline'
import Badge from './Badge.vue'

const props = defineProps<{ model: EvolutionTimelineModel | null; skillDiff: string | null }>()
const diffOpen = ref(false)

const statusTone = (status: EvolutionStatus) =>
  status === 'PASSED'
    ? 'good'
    : status === 'FAILED' || status === 'REJECTED'
      ? 'bad'
      : status === 'RUNNING'
        ? 'info'
        : 'warn'
const display = (value: unknown) =>
  value === null || value === undefined || value === '' ? 'Unavailable' : String(value)
const short = (value: string | null) =>
  value && value.length > 20 ? `${value.slice(0, 10)}…${value.slice(-8)}` : display(value)
const metric = (value: number | null, unit = '') =>
  value === null ? 'Unavailable' : `${value.toLocaleString()}${unit}`
const rate = (value: number | null) =>
  value === null ? 'Unavailable' : `${(value * 100).toFixed(1)}%`
const comparisonRows = computed(() => {
  const baseline = props.model?.baseline
  const winner = props.model?.winner
  if (!baseline || !winner) return []
  return [
    ['Pass rate', rate(baseline.passRate), rate(winner.passRate)],
    ['Tokens', metric(baseline.tokens), metric(winner.tokens)],
    ['Latency', metric(baseline.latencyMs, ' ms'), metric(winner.latencyMs, ' ms')],
    ['Cost', metric(baseline.costMicrousd, ' µUSD'), metric(winner.costMicrousd, ' µUSD')],
  ]
})
</script>

<template>
  <section v-if="!model" class="inline-empty" data-testid="evolution-empty">
    尚未加载 Evolution Evidence Release 或 Proposal evidence。
  </section>
  <div v-else class="content-stack evolution-page" data-testid="evolution-timeline-page">
    <div class="boundary-stack">
      <div class="section-note">
        <strong>Evidence semantics</strong>
        <span
          >Proposal 成功不等于 Skill 改进；Search winner 不等于最终 Skill v2。只有
          locked、人工审核和不可变发布证据完整时才显示 Published。</span
        >
      </div>
      <div v-if="model.simulated" class="simulation-banner">
        <Badge tone="warn">SIMULATED EVIDENCE</Badge>
        <span>该证据不能作为真实 Agent 或 Skill 改进结论。</span>
      </div>
      <div
        v-if="model.proposalEvidenceClass === 'simulated_fixture'"
        class="section-note warn-note"
      >
        <strong>Real Provider call / simulated fixture input</strong>
        <span
          >只证明真实 Proposal 调用链路；没有 Search、locked test 或 Skill improvement claim。</span
        >
      </div>
      <div v-if="model.contamination" class="section-note warn-note">
        <strong>{{ model.contamination }}</strong
        ><span>Locked set 是公开数据，存在高污染风险。</span>
      </div>
    </div>

    <article class="panel evolution-overview">
      <header>
        <div>
          <p class="kicker">SKILL EVOLUTION</p>
          <h2>{{ display(model.v1Version) }} → {{ display(model.v2Version) }}</h2>
        </div>
        <Badge :tone="statusTone(model.publicationStatus)">{{ model.publicationStatus }}</Badge>
      </header>
      <div class="evolution-overview-grid">
        <div>
          <span>Skill hashes</span
          ><code>{{ short(model.v1Hash) }} → {{ short(model.v2Hash) }}</code>
        </div>
        <div>
          <span>Provider / model</span
          ><strong>{{ display(model.provider) }} / {{ display(model.model) }}</strong>
        </div>
        <div>
          <span>Parent hash</span><code>{{ short(model.parentHash) }}</code>
        </div>
        <div>
          <span>Total cost</span><strong>{{ metric(model.winner.costMicrousd, ' µUSD') }}</strong>
        </div>
        <div>
          <span>Evidence</span
          ><strong>{{ display(model.evidenceClass) }} · simulated={{ model.simulated }}</strong>
        </div>
        <div>
          <span>Evolution ID</span><code>{{ display(model.evolutionId) }}</code>
        </div>
      </div>
      <div class="claim-limit">
        <strong>Claim limit</strong><span>{{ display(model.claimLimit) }}</span>
      </div>
    </article>

    <article class="panel">
      <header>
        <div>
          <p class="kicker">CONTROLLED EVIDENCE CHAIN</p>
          <h2>Evolution Timeline</h2>
        </div>
      </header>
      <ol class="evolution-track" aria-label="Skill evolution stages">
        <li
          v-for="item in model.stages"
          :key="item.id"
          :data-testid="`stage-${item.id}`"
          :class="item.status.toLowerCase()"
        >
          <span class="evolution-node" />
          <div>
            <Badge :tone="statusTone(item.status)">{{ item.status }}</Badge
            ><strong>{{ item.label }}</strong
            ><small>{{ item.summary }}</small
            ><code v-if="item.evidence">{{ item.evidence }}</code>
          </div>
        </li>
      </ol>
    </article>

    <article class="panel">
      <header>
        <div>
          <p class="kicker">REAL LLM PROPOSAL</p>
          <h2>Proposal Candidates</h2>
        </div>
        <span class="muted">{{ model.proposalCount ?? 'Unavailable' }} candidates</span>
      </header>
      <div class="summary-strip compact-strip proposal-usage">
        <div>
          <span>INPUT TOKENS</span><strong>{{ metric(model.proposalUsage.inputTokens) }}</strong>
        </div>
        <div>
          <span>OUTPUT TOKENS</span><strong>{{ metric(model.proposalUsage.outputTokens) }}</strong>
        </div>
        <div>
          <span>LATENCY</span><strong>{{ metric(model.proposalUsage.latencyMs, ' ms') }}</strong>
        </div>
        <div>
          <span>COST</span><strong>{{ metric(model.proposalUsage.costMicrousd, ' µUSD') }}</strong>
        </div>
      </div>
      <div v-if="!model.proposals.length" class="inline-empty">
        Proposal candidate fields unavailable.
      </div>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Candidate</th>
              <th>Failure label</th>
              <th>Modification rationale</th>
              <th>Instruction</th>
              <th>Risks / lineage</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="candidate in model.proposals"
              :key="candidate.id"
              :class="{ winner: candidate.winner }"
            >
              <td>
                <Badge :tone="candidate.winner ? 'good' : 'neutral'">{{
                  candidate.winner ? 'WINNER' : 'CANDIDATE'
                }}</Badge
                ><code>{{ candidate.id }}</code>
              </td>
              <td>{{ candidate.failureLabel }}</td>
              <td>{{ candidate.rationale }}</td>
              <td class="instruction-cell">{{ candidate.instruction }}</td>
              <td>
                <span>{{
                  candidate.risks.length ? candidate.risks.join(' · ') : 'Unavailable'
                }}</span
                ><small>{{
                  candidate.failureLineage.length
                    ? candidate.failureLineage.join(' · ')
                    : 'Lineage unavailable'
                }}</small>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </article>

    <div class="two-column">
      <article class="panel">
        <header>
          <div>
            <p class="kicker">PAIRED OUTCOMES</p>
            <h2>v1 / v2 comparison</h2>
          </div>
        </header>
        <div class="summary-strip compact-strip">
          <div>
            <span>WIN</span><strong>{{ model.wtl.win ?? 'Unavailable' }}</strong>
          </div>
          <div>
            <span>TIE</span><strong>{{ model.wtl.tie ?? 'Unavailable' }}</strong>
          </div>
          <div>
            <span>LOSS</span><strong>{{ model.wtl.loss ?? 'Unavailable' }}</strong>
          </div>
        </div>
        <table class="comparison-table">
          <thead>
            <tr>
              <th>Metric</th>
              <th>v1</th>
              <th>v2</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in comparisonRows" :key="row[0]">
              <td>{{ row[0] }}</td>
              <td>{{ row[1] }}</td>
              <td>{{ row[2] }}</td>
            </tr>
          </tbody>
        </table>
        <div class="claim-limit">
          <strong>Regression cases</strong
          ><span>{{
            model.regressionCases.length
              ? model.regressionCases.join(', ')
              : 'None reported / unavailable by stage status'
          }}</span>
        </div>
      </article>
      <article class="panel">
        <header>
          <div>
            <p class="kicker">SAFE PATCH VIEW</p>
            <h2>Skill Diff</h2>
          </div>
          <button class="button ghost" @click="diffOpen = !diffOpen">
            {{ diffOpen ? 'Collapse' : 'Expand' }}
          </button>
        </header>
        <div v-if="!skillDiff" class="inline-empty">skill-diff.patch not loaded.</div>
        <pre v-else-if="diffOpen" class="skill-diff" data-testid="skill-diff">{{ skillDiff }}</pre>
        <small v-else class="muted"
          >Patch loaded locally. Expand to inspect escaped plain text.</small
        >
      </article>
    </div>

    <article class="panel">
      <header>
        <div>
          <p class="kicker">AUDIT SURFACE</p>
          <h2>Evidence</h2>
        </div>
      </header>
      <div class="evidence-metadata">
        <div>
          <span>Manifest hash</span><code>{{ display(model.manifestHash) }}</code>
        </div>
        <div>
          <span>Input fingerprint</span><code>{{ display(model.inputFingerprint) }}</code>
        </div>
        <div>
          <span>DatasetVersion</span><code>{{ display(model.datasetVersion) }}</code>
        </div>
        <div>
          <span>Runner</span><strong>{{ display(model.runner) }}</strong>
        </div>
        <div>
          <span>Human review</span
          ><strong
            >{{ display(model.humanReview.decision) }} ·
            {{ display(model.humanReview.reviewer) }}</strong
          ><small>{{ display(model.humanReview.reason) }}</small>
        </div>
      </div>
      <div v-if="model.capabilitiesUnavailable.length" class="claim-limit">
        <strong>Capability unavailable</strong
        ><span>{{ model.capabilitiesUnavailable.join(', ') }}</span>
      </div>
      <div v-if="model.artifacts.length" class="lineage-grid">
        <div
          v-for="artifact in model.artifacts"
          :key="`${artifact.role}-${artifact.path}`"
          data-testid="artifact-link"
        >
          <Badge tone="info">{{ artifact.role }}</Badge
          ><code>{{ display(artifact.path) }}</code
          ><small>SHA-256 {{ short(artifact.sha256) }}</small>
        </div>
      </div>
      <div v-else class="inline-empty">Artifact index unavailable.</div>
    </article>
  </div>
</template>
