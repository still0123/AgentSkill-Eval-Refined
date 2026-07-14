export type ReportKind =
  | 'experiment'
  | 'trace'
  | 'diagnosis'
  | 'pair-diff'
  | 'benchmark'
  | 'skill-search'
  | 'promotion'
  | 'evolution'

export interface ImportedReport {
  id: string
  name: string
  kind: ReportKind
  schemaVersion: string
  importedAt: string
  data: Record<string, unknown>
  synthetic: boolean
  simulated: boolean
}

export interface CaseRow {
  caseId: string
  category: string
  group: string
  classification: string
  controlStatus: string
  treatmentStatus: string
  controlScore: number | null
  treatmentScore: number | null
  tokenDelta: number | null
  latencyDelta: number | null
  invalidReason: string | null
}

export interface DashboardState {
  reports: ImportedReport[]
  loading: boolean
  error: string | null
}
