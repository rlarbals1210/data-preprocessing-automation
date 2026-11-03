export type Grade = 'exact' | 'alias' | 'similar' | 'shape' | 'none';
export type ColumnProposal = {
  original: string;
  logical: string | null;
  grade: Grade;
  candidates: { logical: string; grade: Grade; score: number | null }[];
  reasons: string[];
  needs_confirmation: boolean;
};
export type Proposal = {
  table: { table: string | null; auto: boolean; basis: 'name' | 'headers' | 'user' | null; scores: Record<string, number> };
  columns: ColumnProposal[];
  missing_required: string[];
  extra: string[];
  needs_confirmation: boolean;
};
export type Mapping = { table: string | null; columns: Record<string, string | null> };
export type FileEntry = {
  item_id: number;
  name: string;
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
export type ResultSummary = {
  status: 'completed' | 'completed_with_issues' | 'failed';
  changed_cells?: number;
  rows?: number;
  before?: Record<string, number>;
  after?: Record<string, number>;
  not_approved?: number;
  attempt?: number;
  result_version?: number;
  excluded_tables?: Record<string, string>;
  stage?: string;
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
  plan: { skipped: SkippedRule[]; tables: Record<string, TablePlan>; completeness: 'full' | 'partial'; hash: string } | null;
  result: ResultSummary | null;
};
export type Approval = {
  id: number;
  kind: 'structure' | 'execution';
  created_at: string;
  invalidated_at: string | null;
  invalidated_reason: string | null;
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
};
export type Source = {
  file: string | null;
  item_id: number;
  record: number;
  line_start: number;
  line_end: number;
  columns: Record<string, string | null>;
  absent: string[];
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
  item_id: number;
  record: number;
  line_start: number;
  line_end: number;
  cells: { original: string; logical: string | null; value: string }[];
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
  reasons: { category: Category; code: string }[];
};

export class ApiError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(body?.code ?? 'error', body?.message ?? res.statusText);
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
  sources: () => req<{ id: number; name: string }[]>('/sources'),
  createRun: (files: File[], set: string, source: string) => {
    const form = new FormData();
    files.forEach((f) => form.append('files', f, f.name));
    form.append('set', set);
    form.append('source', source);
    return req<Run>('/runs', { method: 'POST', body: form });
  },
  run: (id: string) => req<Run>(`/runs/${id}`),
  retry: (id: string) => req<Run>(`/runs/${id}/retry`, { method: 'POST' }),
  propose: (id: string, item_id: number, table: string) =>
    req<Proposal>(`/runs/${id}/propose`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_id, table }),
    }),
  confirmMapping: (id: string, files: ({ item_id: number } & Mapping)[]) =>
    req<Run>(`/runs/${id}/mapping`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ files }),
    }),
  diagnose: (id: string) => req<Diagnosis>(`/runs/${id}/diagnose`, { method: 'POST' }),
  diagnosis: (id: string) => req<Diagnosis>(`/runs/${id}/diagnosis`),
  issues: (id: string, p: { category?: string; table?: string | null; offset: number; limit: number }) =>
    req<Page<Issue>>(`/runs/${id}/issues${q(p)}`),
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
  execute: (id: string, approval: Record<string, string | null>) =>
    req<ResultSummary>(`/runs/${id}/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approval }),
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
