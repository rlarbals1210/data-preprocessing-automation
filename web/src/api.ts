export type Grade = 'exact' | 'alias' | 'reuse' | 'similar' | 'shape' | 'none';
export type ColumnProposal = {
  original: string;
  logical: string | null;
  grade: Grade;
  candidates: { logical: string; grade: Grade; score: number | null }[];
  reasons: string[];
  needs_confirmation: boolean;
  reuse_version?: number;
};
export type Proposal = {
  table: { table: string | null; auto: boolean; basis: 'name' | 'headers' | 'user' | 'reuse' | null; scores: Record<string, number> };
  columns: ColumnProposal[];
  missing_required: string[];
  extra: string[];
  needs_confirmation: boolean;
};
export type Mapping = { table: string | null; columns: Record<string, string | null> };
export type FileEntry = {
  item_id: number;
  name: string;
  sheet?: string | null;
  read_errors?: { sheet?: string; cell?: string | null; record?: number | null; reason: string }[];
  sha256: string;
  bytes: number;
  rows: number;
  headers: string[];
  read_error: string | null;
  proposal: Proposal | null;
  mapping: Mapping;
  confirmed: boolean;
};
export type Catalog = Record<string, { columns: string[]; required: string[] }>;
export type RunStatus =
  | 'uploaded'
  | 'needs_confirmation'
  | 'diagnosed'
  | 'awaiting_approval'
  | 'completed'
  | 'completed_with_issues'
  | 'failed';
export const FINISHED: RunStatus[] = ['completed', 'completed_with_issues', 'failed'];
export type ReuseChoice = { groups: string[]; mappings: number[] };
export type ResultSummary = {
  status: 'completed' | 'completed_with_issues' | 'failed';
  reuse_rules?: number;
  changed_cells?: number;
  rows?: number;
  before?: Record<string, number>;
  after?: Record<string, number>;
  not_approved?: number;
  attempt?: number;
  result_version?: number;
  excluded_tables?: Record<string, string>;
  stage?: string;
  integrity_after?: number;
  audit?: { passed: boolean; checked_cells: number; changed_cells: number; allowed_cells: number };
  error?: string;
};
export type Run = {
  id: string;
  created_at: string;
  set: string;
  source: string;
  status: RunStatus;
  status_reason?: string | null;
  approvals?: Approval[];
  snapshot: string | null;
  files: FileEntry[];
  rows: number;
  catalog?: Catalog | null;
  retry_of?: string | null;
  retried_by?: string | null;
  drift?: { required: boolean; confirmed: boolean } | null;
  plan: { skipped: SkippedRule[]; tables: Record<string, TablePlan>; completeness: 'full' | 'partial'; hash: string } | null;
  result: ResultSummary | null;
};
export type DriftState = 'no_change' | 'changed' | 'choose' | 'baseline_create' | 'no_baseline';
export type DriftChange = {
  kind: string;
  column?: string;
  baseline_column?: string;
  logical?: string | null;
  norm?: string;
  before?: unknown;
  after?: unknown;
  added?: string[];
  removed?: string[];
  order_only?: boolean;
  reason?: string;
  filled?: { before: number; after: number };
  dominant?: string;
  overlap?: string;
  new_signatures?: Record<string, number>;
  new_ratio?: string;
  type?: string;
  below?: boolean;
  above?: boolean;
  unparsed?: number;
  tz_naive?: number;
  values?: string[];
  count?: number;
};
export type DriftItem = {
  item_id: number;
  name: string;
  sheet?: string | null;
  headers: string[];
  state: DriftState;
  reason: string;
  chosen_by: 'auto' | 'user';
  template_id: number | null;
  std_table: string | null;
  candidates: { template_id: number; std_table: string; overlap: number; size: number }[];
  baseline: { result_version_id: number; run_id: number; run: string | null } | null;
  changes: DriftChange[];
};
export type Drift = { required: boolean; confirmed: boolean; items: DriftItem[] };
export type Approval = {
  id: number;
  kind: 'structure' | 'execution';
  created_at: string;
  invalidated_at: string | null;
  invalidated_reason: string | null;
};
export type SourceSummary = {
  id: number;
  name: string;
  versions: number;
  current_version: number | null;
  current_kind: 'cleaned' | 'restore' | null;
};
export type ResultVersion = {
  id: number;
  kind: 'cleaned' | 'restore';
  created_at: string;
  current: boolean;
  restored_from: number | null;
  origin: number;
  run: string | null;
  run_status: RunStatus;
  source_id: number;
  changed_cells: number | null;
  not_approved: number | null;
  completeness: 'full' | 'partial' | null;
  integrity_after: number | null;
  remaining: { business_rule_violation: number; human_review: number };
  files: { name: string; rows: number }[];
  data_hash: string;
};
export type VersionDetail = ResultVersion & {
  refs: {
    origin: number;
    config_snapshot: string | null;
    mapping_version: { version: number; hash: string } | null;
    approval_id: number | null;
    approval_hashes: Record<string, string>;
  };
  events: { kind: string; payload: Record<string, unknown>; created_at: string }[];
};
export type RunListItem = Pick<Run, 'id' | 'created_at' | 'set' | 'source' | 'status' | 'rows' | 'result' | 'retry_of' | 'retried_by'> & { file_count: number };
export type SkippedRule = { table: string; kind: string; columns: string[]; target?: string; reason: string };
export type TablePlan = { diagnosable: boolean; publishable: boolean; missing_required: string[]; reason: string | null };
export type Category = 'normal_observation' | 'standardization_candidate' | 'business_rule_violation' | 'human_review';
export type Diagnosis = {
  engine_status: 'completed' | 'incomplete';
  mode: 'configured' | 'profile';
  rows_read: number;
  tables_read: number;
  category_counts: Record<Category, number>;
  tables: { table: string; rows: number; columns: number; counts: Partial<Record<Category, number>> }[];
  duplicate_groups: number;
  rules: { executed: number; not_executed: number };
  skipped_rules: SkippedRule[];
  plan_tables: Record<string, TablePlan>;
  completeness: 'full' | 'partial';
  errors: { table: string; error: string }[];
  integrity: Record<'raw' | 'virtual', { violations: number; not_computable: number; not_run: number; disabled: number }> | null;
};
export type IntegrityBasis = 'raw' | 'virtual' | 'approved' | 'result';
export type IntegrityRule = {
  id: string;
  description: string;
  anchor: string;
  type: string;
  status: 'ran' | 'disabled' | 'not_run';
  reason: string | null;
  counts: { ok: number; violation: number; not_applicable: number; not_computable: Record<string, number> };
};
export type IntegrityViolation = {
  rule: string;
  table: string;
  record: number;
  basis: string;
  left: number | string | null;
  right: number | string | null;
  detail: unknown;
  rows: [string, number][];
  sources: (Source & { table: string })[];
};
export type IntegrityResult = { basis: string; rules: IntegrityRule[] } & Page<IntegrityViolation>;
export type Source = {
  file: string | null;
  item_id: number;
  record: number;
  line_start: number;
  line_end: number;
  columns: Record<string, string | null>;
  absent: string[];
  sheet?: string;
  sheet_row?: number;
  cells?: Record<string, string | null>;
};
export type Issue = {
  table: string;
  record: number;
  columns: string[];
  values: Record<string, string>;
  code: string;
  severity: string;
  category: Category;
  suggested_value: string | null;
  category_reason: string;
  source?: Source | null;
  detail?: string | null;
};
export type PreviewGroup = {
  id: string;
  approved: boolean;
  held: number;
  table: string;
  column: string;
  code: string;
  cells: number;
  excluded: number;
  reuse: number;
  examples: { before: string; after: string }[];
};
export type Cell = [string, number, string];
export type PreviewData = {
  groups: PreviewGroup[];
  total_cells: number;
  candidates: number;
  held: number;
  not_approved: number;
  excluded_cells: Cell[];
  not_publishable: Record<string, string>;
  plan_hash: string;
  view_hash: string;
  approval: Record<string, string | null>;
};
export type Change = {
  table: string;
  record: number;
  column: string;
  before: string;
  after: string;
  codes: string[];
  source: Source;
};
export type Page<T> = { total: number; items: T[] };
export type SourceRow = {
  file: string;
  sheet: string | null;
  item_id: number;
  record: number;
  line_start: number | null;
  line_end: number | null;
  sheet_row: number | null;
  cells: { original: string; logical: string | null; value: string; type: string | null; cell: string | null }[];
};
export type DuplicateGroup = {
  id: string;
  table: string;
  code: string;
  kind: 'identical' | 'conflicting';
  columns: string[];
  differing_columns: string[];
  records: number[];
  participant_count: number;
  sources: Source[];
};
export type GroupCell = {
  cell: Cell;
  before: string;
  after: string;
  null: boolean;
  codes: string[];
  source: Source | null;
  held: boolean;
  excluded: boolean;
  reuse: boolean;
  reasons: { category: Category; code: string }[];
};

export class ApiError extends Error {
  code: string;
  details: Record<string, unknown> | null;
  constructor(code: string, message: string, details: Record<string, unknown> | null = null) {
    super(message);
    this.code = code;
    this.details = details;
  }
}
export type ReuseImpact = {
  item_id: number;
  kind: 'mapping_changed' | 'header_changed';
  template_id?: number;
  version?: number;
  diff?: { column: string; rule: string | null; now: string | null }[];
  headers?: string[];
  prior?: { template_id: number; headers: string[]; rules: number }[];
};
export type ImpactChoice = 'this_run' | 'new_version';

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(body?.code ?? 'error', body?.message ?? res.statusText, body?.details ?? null);
  return body as T;
}

const q = (params: Record<string, string | number | undefined | null>) => {
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => v !== undefined && v !== null && v !== '' && s.set(k, String(v)));
  return s.toString() ? `?${s}` : '';
};

export const api = {
  sets: () => req<{ id: string; label: string }[]>('/sets'),
  runs: () => req<RunListItem[]>('/runs'),
  sources: () => req<SourceSummary[]>('/sources'),
  sourceVersions: (sid: number) => req<ResultVersion[]>(`/sources/${sid}/versions`),
  version: (vid: number) => req<VersionDetail>(`/versions/${vid}`),
  restore: (vid: number) => req<VersionDetail>(`/versions/${vid}/restore`, { method: 'POST' }),
  versionFileUrl: (vid: number, name: string) => `/api/versions/${vid}/files/${encodeURIComponent(name)}`,
  createRun: (files: File[], set: string, source: string) => {
    const form = new FormData();
    files.forEach((f) => form.append('files', f, f.name));
    form.append('set', set);
    form.append('source', source);
    return req<Run>('/runs', { method: 'POST', body: form });
  },
  run: (id: string) => req<Run>(`/runs/${id}`),
  retry: (id: string) => req<Run>(`/runs/${id}/retry`, { method: 'POST' }),
  drift: (id: string) => req<Drift>(`/runs/${id}/drift`),
  driftSelect: (id: string, item_id: number, template_id: number | null) =>
    req<Drift>(`/runs/${id}/drift/select`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_id, template_id }),
    }),
  driftConfirm: (id: string) => req<Drift>(`/runs/${id}/drift/confirm`, { method: 'POST' }),
  propose: (id: string, item_id: number, table: string) =>
    req<Proposal>(`/runs/${id}/propose`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_id, table }),
    }),
  confirmMapping: (id: string, files: ({ item_id: number } & Mapping)[], reuse_impact?: Record<number, ImpactChoice>) =>
    req<Run>(`/runs/${id}/mapping`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reuse_impact ? { files, reuse_impact } : { files }),
    }),
  diagnose: (id: string) => req<Diagnosis>(`/runs/${id}/diagnose`, { method: 'POST' }),
  diagnosis: (id: string) => req<Diagnosis>(`/runs/${id}/diagnosis`),
  issues: (id: string, p: { category?: string; table?: string | null; offset: number; limit: number }) =>
    req<Page<Issue>>(`/runs/${id}/issues${q(p)}`),
  integrity: (id: string, basis: IntegrityBasis, offset: number, limit: number) =>
    req<IntegrityResult>(`/runs/${id}/integrity${q({ basis, offset, limit })}`),
  duplicates: (id: string, offset: number, limit: number) =>
    req<Page<DuplicateGroup>>(`/runs/${id}/duplicates${q({ offset, limit })}`),
  sourceRow: (id: string, item_id: number, record: number) =>
    req<SourceRow>(`/runs/${id}/source${q({ item_id, record })}`),
  preview: (id: string) => req<PreviewData>(`/runs/${id}/preview`),
  previewCells: (id: string, gid: string, offset: number, limit: number) =>
    req<Page<GroupCell> & { held: number }>(`/runs/${id}/preview/groups/${encodeURIComponent(gid)}${q({ offset, limit })}`),
  select: (id: string, approved_groups: string[], excluded_cells: Cell[]) =>
    req<PreviewData>(`/runs/${id}/selection`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approved_groups, excluded_cells }),
    }),
  execute: (id: string, approval: Record<string, string | null>, reuse?: ReuseChoice) =>
    req<ResultSummary>(`/runs/${id}/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reuse ? { approval, reuse } : { approval }),
    }),
  result: (id: string) =>
    req<ResultSummary & { files: { name: string; rows: number }[]; evidence: string[]; published: boolean }>(
      `/runs/${id}/result`,
    ),
  remaining: (id: string, p: { category?: string | null; offset: number; limit: number }) =>
    req<Page<Issue>>(`/runs/${id}/remaining${q(p)}`),
  failureReportUrl: (id: string) => `/api/runs/${id}/failure-report`,
  changes: (id: string, offset: number, limit: number) => req<Page<Change>>(`/runs/${id}/changes${q({ offset, limit })}`),
  fileUrl: (id: string, name: string) => `/api/runs/${id}/files/${encodeURIComponent(name)}`,
};
