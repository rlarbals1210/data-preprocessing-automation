export type Header = { original: string; logical: string | null; basis: 'legacy_map' | 'exact' | null };
export type FileEntry = {
  name: string;
  table: string;
  known_table?: boolean;
  rows?: number;
  columns?: number;
  bytes: number;
  sha256: string;
  read_error: string | null;
  headers: Header[];
};
export type RunStatus = 'uploaded' | 'diagnosed' | 'executed' | 'failed';
export type ResultSummary = {
  status: 'completed' | 'completed_with_issues' | 'failed';
  changed_cells?: number;
  rows?: number;
  before?: Record<string, number>;
  after?: Record<string, number>;
  error?: string;
};
export type Run = {
  id: string;
  created_at: string;
  set: string;
  status: RunStatus;
  snapshot: string | null;
  files: FileEntry[];
  rows: number;
  plan: { skipped_rules: SkippedRule[] } | null;
  result: ResultSummary | null;
};
export type RunListItem = Pick<Run, 'id' | 'created_at' | 'set' | 'status' | 'rows' | 'result'> & { file_count: number };
export type SkippedRule = { table: string; rule: string; columns: string[]; target?: string; reason: string };
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
  errors: { table: string; error: string }[];
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
};
export type PreviewGroup = {
  table: string;
  column: string;
  code: string;
  cells: number;
  examples: { before: string; after: string }[];
};
export type Change = { table: string; record: number; column: string; before: string; after: string; codes: string[] };
export type Page<T> = { total: number; items: T[] };

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
  createRun: (files: File[], set: string) => {
    const form = new FormData();
    files.forEach((f) => form.append('files', f, f.name));
    form.append('set', set);
    return req<Run>('/runs', { method: 'POST', body: form });
  },
  run: (id: string) => req<Run>(`/runs/${id}`),
  diagnose: (id: string) => req<Diagnosis>(`/runs/${id}/diagnose`, { method: 'POST' }),
  diagnosis: (id: string) => req<Diagnosis>(`/runs/${id}/diagnosis`),
  issues: (id: string, p: { category?: string; table?: string | null; offset: number; limit: number }) =>
    req<Page<Issue>>(`/runs/${id}/issues${q(p)}`),
  preview: (id: string) => req<{ groups: PreviewGroup[]; total_cells: number }>(`/runs/${id}/preview`),
  execute: (id: string) => req<ResultSummary>(`/runs/${id}/execute`, { method: 'POST' }),
  result: (id: string) => req<ResultSummary & { files: { name: string; bytes: number }[] }>(`/runs/${id}/result`),
  changes: (id: string, offset: number, limit: number) => req<Page<Change>>(`/runs/${id}/changes${q({ offset, limit })}`),
  fileUrl: (id: string, name: string) => `/api/runs/${id}/files/${encodeURIComponent(name)}`,
};
