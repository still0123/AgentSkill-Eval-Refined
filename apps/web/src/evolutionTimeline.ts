import type { ImportedReport } from './domain'

export type EvolutionStatus =
  'NOT_STARTED' | 'RUNNING' | 'PASSED' | 'FAILED' | 'REJECTED' | 'UNAVAILABLE'

export interface TimelineStage {
  id:
    'failure' | 'proposal' | 'search' | 'regression' | 'confirm' | 'locked' | 'review' | 'published'
  label: string
  status: EvolutionStatus
  summary: string
  evidence: string | null
}

export interface ProposalCandidate {
  id: string
  failureLabel: string
  rationale: string
  instruction: string
  risks: string[]
  failureLineage: string[]
  winner: boolean
}

export interface MetricSet {
  passRate: number | null
  tokens: number | null
  latencyMs: number | null
  costMicrousd: number | null
}

export interface EvolutionTimelineModel {
  evolutionId: string | null
  v1Version: string | null
  v2Version: string | null
  v1Hash: string | null
  v2Hash: string | null
  parentHash: string | null
  provider: string | null
  model: string | null
  proposalCount: number | null
  proposalUsage: {
    inputTokens: number | null
    outputTokens: number | null
    latencyMs: number | null
    costMicrousd: number | null
  }
  proposals: ProposalCandidate[]
  winnerId: string | null
  stages: TimelineStage[]
  wtl: { win: number | null; tie: number | null; loss: number | null }
  baseline: MetricSet
  winner: MetricSet
  regressionCases: string[]
  humanReview: { decision: string | null; reviewer: string | null; reason: string | null }
  publicationStatus: EvolutionStatus
  claimLimit: string | null
  simulated: boolean
  evidenceClass: string | null
  proposalEvidenceClass: string | null
  artifacts: { role: string; path: string | null; sha256: string | null }[]
  manifestHash: string | null
  inputFingerprint: string | null
  datasetVersion: string | null
  runner: string | null
  capabilitiesUnavailable: string[]
  contamination: 'high-contamination' | null
}

type Json = Record<string, any>

const emptyMetrics = (): MetricSet => ({
  passRate: null,
  tokens: null,
  latencyMs: null,
  costMicrousd: null,
})

const text = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() ? value : null
const num = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null
const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []

function decisionStatus(value: unknown, missing: EvolutionStatus = 'NOT_STARTED'): EvolutionStatus {
  const decision = String(value ?? '').toUpperCase()
  if (!decision) return missing
  if (['PASSED', 'CONFIRMED', 'APPROVED', 'PUBLISHED', 'COMPLETED', 'SUCCEEDED'].includes(decision))
    return 'PASSED'
  if (['REJECTED', 'DECLINED'].includes(decision)) return 'REJECTED'
  if (['FAILED', 'ERROR', 'TIMEOUT', 'INVALID'].includes(decision)) return 'FAILED'
  if (['RUNNING', 'QUEUED', 'PREPARING', 'PENDING'].includes(decision)) return 'RUNNING'
  if (['UNAVAILABLE', 'CAPABILITY_UNAVAILABLE'].includes(decision)) return 'UNAVAILABLE'
  return missing
}

function stage(
  id: TimelineStage['id'],
  label: string,
  status: EvolutionStatus,
  summary: string,
  evidence: string | null = null,
): TimelineStage {
  return { id, label, status, summary, evidence }
}

function metrics(value: unknown): MetricSet {
  if (!value || typeof value !== 'object') return emptyMetrics()
  const row = value as Json
  return {
    passRate: num(row.pass_rate ?? row.winner_pass_rate ?? row.base_pass_rate),
    tokens: num(row.tokens),
    latencyMs: num(row.latency_ms),
    costMicrousd: num(row.cost_microusd),
  }
}

function combineMetrics(scoreSource: unknown, totalSource: unknown): MetricSet {
  const score = metrics(scoreSource)
  const totals = metrics(totalSource)
  const totalRow =
    totalSource && typeof totalSource === 'object' ? (totalSource as Json) : undefined
  const totalValue = (field: 'tokens' | 'latency_ms' | 'cost_microusd', fallback: number | null) =>
    totalRow && Object.hasOwn(totalRow, field) ? num(totalRow[field]) : fallback
  return {
    passRate: score.passRate ?? totals.passRate,
    tokens: totalValue('tokens', score.tokens),
    latencyMs: totalValue('latency_ms', score.latencyMs),
    costMicrousd: totalValue('cost_microusd', score.costMicrousd),
  }
}

function find(reports: ImportedReport[], schema: string): Json | undefined {
  return reports.find((item) => item.schemaVersion === schema)?.data
}

export function buildEvolutionTimeline(
  reports: ImportedReport[],
  releaseManifestHash: string | null = null,
): EvolutionTimelineModel | null {
  const report = find(reports, 'ase/evolution-evidence-report/v1alpha1')
  const manifest = find(reports, 'ase/evolution-evidence-release/v1alpha1')
  const index = find(reports, 'ase/evolution-evidence-index/v1alpha1')
  const proposal =
    find(reports, 'ase/real-llm-proposal-report/v1alpha1') ??
    find(reports, 'ase/real-llm-proposal-manifest/v1alpha1') ??
    find(reports, 'ase/real-llm-proposal-smoke-result/v1alpha1')
  const search = find(reports, 'ase/optimization-report/v1alpha1')
  const promotion = find(reports, 'ase/promotion-release/v1alpha1')
  const skillVersion = find(reports, 'ase/skill-version/v1alpha1')
  if (!report && !manifest && !proposal && !search && !promotion && !skillVersion) return null

  const versions = report?.skill_versions ?? {}
  const v1 = versions.v1 ?? {}
  const v2 = versions.v2 ?? {}
  const searchWinner =
    text(search?.winner_id) ?? text(report?.search_winner) ?? text(promotion?.winner_candidate_id)
  const proposalRows: Json[] = Array.isArray(report?.proposal_lineage)
    ? report.proposal_lineage
    : Array.isArray(proposal?.candidates)
      ? proposal.candidates
      : Array.isArray(proposal?.proposals)
        ? proposal.proposals
        : []
  const failureRows: Json[] = Array.isArray(report?.failure_lineage) ? report.failure_lineage : []
  const proposals = proposalRows.map((item) => {
    const failure = text(item.failure_label) ?? 'Unavailable'
    const lineage = failureRows
      .filter((row) => text(row.failure_label) === failure)
      .flatMap((row) => strings(row.evidence_refs))
    return {
      id: text(item.id) ?? 'unknown-candidate',
      failureLabel: failure,
      rationale: text(item.hypothesis ?? item.reason ?? item.rationale) ?? 'Unavailable',
      instruction: text(item.instruction) ?? 'Unavailable',
      risks: strings(item.risks),
      failureLineage: strings(item.evidence_refs).concat(lineage),
      winner: text(item.id) === searchWinner,
    }
  })

  const stages = report?.stages ?? {}
  const validation = stages.validation_search ?? {}
  const regression = stages.regression_dev ?? {}
  const confirm = stages.validation_confirm ?? promotion?.confirmation ?? {}
  const locked = stages.locked_test ?? promotion?.locked_test ?? skillVersion?.locked_test ?? {}
  const review = report?.human_review ?? promotion?.human_review ?? {}
  const failureStatus: EvolutionStatus =
    failureRows.length || proposalRows.length ? 'PASSED' : 'NOT_STARTED'
  const hasProposalEvidence =
    proposalRows.length > 0 ||
    (num(proposal?.proposal_count) ?? 0) > 0 ||
    text(proposal?.proposals_sha256) !== null
  const proposalStatus = hasProposalEvidence
    ? decisionStatus(proposal?.status, 'PASSED')
    : decisionStatus(proposal?.status)
  const searchStatus = searchWinner
    ? 'PASSED'
    : decisionStatus(search?.status ?? search?.job?.status)
  const regressionStatus =
    typeof regression.passed === 'boolean'
      ? regression.passed
        ? 'PASSED'
        : 'REJECTED'
      : decisionStatus(regression.decision)
  const confirmStatus = decisionStatus(confirm.decision)
  const lockedStatus = decisionStatus(locked.decision)
  const reviewStatus = decisionStatus(review.decision)
  const hasRelease = Boolean(manifest || promotion?.decision === 'APPROVED' || skillVersion)
  const requiredStagesPassed =
    regressionStatus === 'PASSED' &&
    confirmStatus === 'PASSED' &&
    lockedStatus === 'PASSED' &&
    reviewStatus === 'PASSED'
  const publicationStatus: EvolutionStatus =
    hasRelease && requiredStagesPassed
      ? 'PASSED'
      : reviewStatus === 'REJECTED' ||
          regressionStatus === 'REJECTED' ||
          confirmStatus === 'REJECTED'
        ? 'REJECTED'
        : hasRelease
          ? 'FAILED'
          : 'NOT_STARTED'

  const aggregate = report?.v1_v2_aggregate ?? {}
  const artifactRows: Json[] = Array.isArray(index?.artifacts)
    ? index.artifacts
    : Array.isArray(manifest?.files)
      ? manifest.files
      : []
  const unavailable = strings(report?.capability_unavailable ?? report?.capabilities_unavailable)
  const proposalInputClass =
    text(proposal?.input_evidence_class) ?? text(proposal?.input?.evidence_class)
  const simulated = Boolean(
    report?.simulated === true ||
    manifest?.simulated === true ||
    promotion?.simulated === true ||
    skillVersion?.simulated_evidence === true,
  )
  const evidenceClass =
    text(report?.evidence_class) ?? text(manifest?.evidence_class) ?? text(proposal?.evidence_class)

  return {
    evolutionId: text(manifest?.evolution_id ?? promotion?.evolution_id ?? report?.evolution_id),
    v1Version: text(v1.version ?? manifest?.parent_version),
    v2Version: text(v2.version ?? manifest?.version ?? skillVersion?.version),
    v1Hash: text(v1.content_sha256 ?? manifest?.parent_content_sha256),
    v2Hash: text(v2.content_sha256 ?? manifest?.content_sha256 ?? skillVersion?.content_sha256),
    parentHash: text(v2.parent_content_sha256 ?? manifest?.parent_content_sha256),
    provider: text(proposal?.provider ?? report?.provider),
    model: text(proposal?.model ?? report?.model),
    proposalCount:
      num(proposal?.proposal_count) ?? (proposalRows.length ? proposalRows.length : null),
    proposalUsage: {
      inputTokens: num(proposal?.input_tokens),
      outputTokens: num(proposal?.output_tokens),
      latencyMs: num(proposal?.duration_ms),
      costMicrousd: num(proposal?.cost_microusd),
    },
    proposals,
    winnerId: searchWinner,
    stages: [
      stage(
        'failure',
        'Failure',
        failureStatus,
        failureRows.length
          ? `${failureRows.length} failure lineage record(s)`
          : 'No failure evidence loaded',
        'evolution_report',
      ),
      stage(
        'proposal',
        'Proposal',
        proposalStatus,
        proposalRows.length
          ? `${proposalRows.length} candidate(s)`
          : 'Proposal evidence not loaded',
        'proposal_report',
      ),
      stage(
        'search',
        'Search',
        searchStatus,
        searchWinner ? `Winner ${searchWinner}` : 'No search winner',
        'search_report',
      ),
      stage(
        'regression',
        'Regression',
        regressionStatus,
        strings(regression.loss_cases).length
          ? `${strings(regression.loss_cases).length} regression case(s)`
          : regressionStatus === 'PASSED'
            ? 'Regression gate passed'
            : 'Regression evidence not loaded',
        'regression_gate',
      ),
      stage(
        'confirm',
        'Confirm',
        confirmStatus,
        text(confirm.decision) ?? 'Validation confirmation not run',
        'confirmation_report',
      ),
      stage(
        'locked',
        'Locked',
        lockedStatus,
        text(locked.decision) ?? 'Locked test not run',
        'locked_test_report',
      ),
      stage(
        'review',
        'Review',
        reviewStatus,
        text(review.decision) ?? 'Human review not recorded',
        'human_review',
      ),
      stage(
        'published',
        'Published',
        publicationStatus,
        publicationStatus === 'PASSED'
          ? 'Immutable SkillVersion published'
          : publicationStatus === 'FAILED'
            ? 'Release evidence is incomplete or inconsistent'
            : 'Skill v2 not published',
        'release_manifest',
      ),
    ],
    wtl: { win: num(aggregate.win), tie: num(aggregate.tie), loss: num(aggregate.loss) },
    baseline: combineMetrics(validation.v1, aggregate.base),
    winner: combineMetrics(validation.v2, aggregate.winner),
    regressionCases: strings(regression.loss_cases),
    humanReview: {
      decision: text(review.decision),
      reviewer: text(review.reviewer),
      reason: text(review.reason),
    },
    publicationStatus,
    claimLimit: text(
      report?.claim_limit ??
        manifest?.claim_limit ??
        promotion?.claim_limit ??
        proposal?.claim_limit,
    ),
    simulated,
    evidenceClass,
    proposalEvidenceClass: proposalInputClass,
    artifacts: artifactRows.map((item) => ({
      role: text(item.role ?? item.path) ?? 'unknown',
      path: text(item.path ?? item.archive_path),
      sha256: text(item.sha256 ?? item.source_sha256 ?? item.bundle_sha256),
    })),
    manifestHash: releaseManifestHash ?? text(manifest?.manifest_sha256),
    inputFingerprint: text(manifest?.input_fingerprint ?? index?.input_fingerprint),
    datasetVersion: text(report?.dataset_version ?? report?.dataset_version_sha256),
    runner: text(report?.runner ?? report?.runner_version),
    capabilitiesUnavailable: unavailable,
    contamination:
      report?.locked_test_visibility === 'public' || report?.locked_test_contamination === 'high'
        ? 'high-contamination'
        : null,
  }
}
