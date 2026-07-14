import type { ImportedReport, ReportKind } from './domain'
import { IMPORT_LIMITS, ImportError, sanitizeJson } from './security'

const versions: Record<ReportKind, Set<string>> = {
  experiment: new Set(['ase/report/v1alpha1']),
  trace: new Set(['ase/v1alpha1']),
  diagnosis: new Set(['ase/v1alpha1']),
  'pair-diff': new Set(['ase/v1alpha1']),
  benchmark: new Set([
    'ase/benchmark-job/v1alpha1',
    'ase/benchmark-candidate/v1alpha1',
    'ase/benchmark-dataset-version/v1alpha1',
    'ase/benchmark-report/v1alpha1',
  ]),
  'skill-search': new Set([
    'ase/optimization-report/v1alpha1',
    'ase/optimization-job/v1alpha1',
    'ase/skill-candidate/v1alpha1',
  ]),
  promotion: new Set([
    'ase/skill-version-promotion/v1alpha1',
    'ase/skill-version/v1alpha1',
    'ase/promotion-workflow/v1alpha1',
    'ase/promotion-release/v1alpha1',
  ]),
}

function object(value: unknown, field = 'root'): Record<string, any> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new ImportError(`${field} 必须是 JSON 对象`, 'schema')
  return value as Record<string, any>
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value.trim())
    throw new ImportError(`缺少必需字段：${field}`, 'schema')
  return value
}

function requiredArray(value: unknown, field: string): unknown[] {
  if (!Array.isArray(value)) throw new ImportError(`缺少必需数组：${field}`, 'schema')
  return value
}

function detect(root: Record<string, any>): { kind: ReportKind; version: string } {
  if (root.report_schema_version !== undefined) {
    object(root.experiment, 'experiment')
    object(root.statistics, 'statistics')
    requiredArray(root.variants, 'variants')
    return {
      kind: 'experiment',
      version: requiredString(root.report_schema_version, 'report_schema_version'),
    }
  }
  const version = requiredString(root.schema_version, 'schema_version')
  if (
    version === 'ase/skill-version-promotion/v1alpha1' ||
    version === 'ase/skill-version/v1alpha1' ||
    version === 'ase/promotion-workflow/v1alpha1' ||
    version === 'ase/promotion-release/v1alpha1'
  )
    return { kind: 'promotion', version }
  if (version.startsWith('ase/benchmark-')) return { kind: 'benchmark', version }
  if (version.startsWith('ase/optimization-') || version.startsWith('ase/skill-candidate'))
    return { kind: 'skill-search', version }
  if (root.capabilities !== undefined) {
    requiredString(root.run_id, 'run_id')
    requiredArray(root.events, 'events')
    requiredArray(root.capabilities, 'capabilities')
    return { kind: 'trace', version }
  }
  if (root.findings !== undefined) {
    requiredString(root.run_id, 'run_id')
    requiredString(root.status, 'status')
    requiredArray(root.findings, 'findings')
    return { kind: 'diagnosis', version }
  }
  if (root.event_count_deltas !== undefined) {
    requiredString(root.pair_block_id, 'pair_block_id')
    requiredArray(root.event_count_deltas, 'event_count_deltas')
    return { kind: 'pair-diff', version }
  }
  throw new ImportError(`不兼容的 schema_version：${version}`, 'incompatible')
}

function validateByKind(kind: ReportKind, root: Record<string, any>): void {
  if (kind === 'benchmark') {
    if (root.schema_version === 'ase/benchmark-report/v1alpha1') {
      object(root.job, 'job')
      requiredArray(root.candidates, 'candidates')
    } else if (root.schema_version.includes('candidate')) {
      requiredString(root.id, 'id')
      requiredString(root.status, 'status')
      requiredArray(root.transitions, 'transitions')
    } else requiredString(root.id, 'id')
  }
  if (kind === 'skill-search') {
    if (root.schema_version === 'ase/optimization-report/v1alpha1') {
      object(root.job, 'job')
      requiredArray(root.candidates, 'candidates')
      requiredString(root.winner_id, 'winner_id')
      if (root.locked_test_accessed !== false)
        throw new ImportError('Skill Search 报告必须声明 locked_test_accessed=false', 'schema')
    } else requiredString(root.id, 'id')
  }
  if (kind === 'promotion') {
    if (root.schema_version === 'ase/skill-version-promotion/v1alpha1') {
      requiredString(root.id, 'id')
      requiredString(root.skill_name, 'skill_name')
      requiredString(root.status, 'status')
      requiredString(root.target_version, 'target_version')
      requiredArray(root.transitions, 'transitions')
      requiredArray(root.evidence, 'evidence')
    } else if (root.schema_version === 'ase/skill-version/v1alpha1') {
      requiredString(root.id, 'id')
      requiredString(root.skill_name, 'skill_name')
      requiredString(root.version, 'version')
      requiredString(root.promotion_id, 'promotion_id')
      object(root.validation_confirm, 'validation_confirm')
      object(root.locked_test, 'locked_test')
      requiredString(root.claim_limit, 'claim_limit')
    } else if (root.schema_version === 'ase/promotion-workflow/v1alpha1') {
      requiredString(root.id, 'id')
      requiredString(root.promotion_id, 'promotion_id')
      requiredString(root.skill_name, 'skill_name')
      requiredString(root.target_version, 'target_version')
      requiredString(root.status, 'status')
      requiredArray(root.lineage, 'lineage')
      requiredString(root.claim_limit, 'claim_limit')
    } else {
      requiredString(root.workflow_id, 'workflow_id')
      requiredString(root.promotion_id, 'promotion_id')
      requiredString(root.decision, 'decision')
      requiredArray(root.lineage, 'lineage')
      object(root.confirmation, 'confirmation')
      requiredString(root.claim_limit, 'claim_limit')
    }
  }
}

export function parseReportText(text: string, name = 'report.json'): ImportedReport {
  if (new Blob([text]).size > IMPORT_LIMITS.maxFileBytes)
    throw new ImportError(`文件超过 ${IMPORT_LIMITS.maxFileBytes / 1024 / 1024} MB 限制`, 'size')
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    throw new ImportError('JSON 语法无效', 'syntax')
  }
  const clean = object(sanitizeJson(parsed))
  const { kind, version } = detect(clean)
  if (!versions[kind].has(version))
    throw new ImportError(`不兼容的 schema_version：${version}`, 'incompatible')
  validateByKind(kind, clean)
  return {
    id: crypto.randomUUID(),
    name,
    kind,
    schemaVersion: version,
    importedAt: new Date().toISOString(),
    data: clean,
    synthetic: clean.synthetic === true || clean.experiment?.protocol_snapshot?.demo_only === true,
    simulated:
      clean.simulated === true ||
      clean.simulated_evidence === true ||
      (Array.isArray(clean.evidence) &&
        clean.evidence.some((item: any) => item?.simulated === true)) ||
      clean.experiment?.protocol_snapshot?.evidence_mode === 'simulated_fixture',
  }
}

export async function parseReportFile(file: File): Promise<ImportedReport> {
  if (file.size > IMPORT_LIMITS.maxFileBytes)
    throw new ImportError(`文件超过 ${IMPORT_LIMITS.maxFileBytes / 1024 / 1024} MB 限制`, 'size')
  if (!file.name.toLowerCase().endsWith('.json'))
    throw new ImportError('仅支持 .json 文件', 'schema')
  return parseReportText(await file.text(), file.name)
}
