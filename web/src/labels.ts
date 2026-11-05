import type { Category, DriftState } from './api';

export const CATEGORY: Record<Category, { label: string; color: string }> = {
  business_rule_violation: { label: '업무 규칙 위반', color: 'red' },
  human_review: { label: '사람 검토 필요', color: 'orange' },
  standardization_candidate: { label: '표준화 후보', color: 'teal' },
  normal_observation: { label: '정상 관측', color: 'gray' },
};
export const CATEGORY_ORDER: Category[] = [
  'business_rule_violation',
  'human_review',
  'standardization_candidate',
  'normal_observation',
];

export const CODE: Record<string, string> = {
  blank: '빈 값',
  missing_candidate: '결측 표현',
  outer_whitespace: '앞뒤 공백',
  duplicate_row: '중복 행',
  required: '필수값 누락',
  comparison_normalization: '표기 통일',
  allowed: '허용값 밖',
  pattern: '형식 불일치',
  type: '타입 해석 실패',
  min: '최솟값 미만',
  max: '최댓값 초과',
  unique: '키 중복',
  reference: '참조 불일치',
};

export const CHANGE: Record<string, string> = {
  type: '형식 변환',
  pattern: '형식 통일',
  comparison_normalization: '표기 통일',
  missing_candidate: '결측 통일',
  blank: '결측 통일',
  outer_whitespace: '앞뒤 공백 제거',
};

export const RULE_KIND: Record<string, string> = {
  required: '필수',
  required_if: '조건부 필수',
  missing_values: '결측 표현',
  comparison_normalization: '표기 비교',
  allowed: '허용값',
  pattern: '형식',
  type: '타입',
  min: '최솟값',
  max: '최댓값',
  standardization: '표준화',
  unique: '유일 키',
  reference: '참조',
};

export const PUBLISH_REASON: Record<string, string> = { missing_required: '필수 컬럼 없음', no_rules: '검사할 규칙 없음' };

export const SKIP_REASON: Record<string, string> = {
  missing_column: '컬럼 없음',
  excluded_target: '대상 테이블 없음',
  dependency_skipped: '의존 규칙 제외',
};

export const RUN_STATUS: Record<string, { label: string; color: string }> = {
  uploaded: { label: '업로드됨', color: 'blue' },
  needs_confirmation: { label: '확인 필요', color: 'orange' },
  diagnosed: { label: '진단 완료', color: 'violet' },
  awaiting_approval: { label: '승인 대기', color: 'cyan' },
  completed: { label: '완료', color: 'teal' },
  completed_with_issues: { label: '완료 · 문제 남음', color: 'yellow' },
  failed: { label: '실패', color: 'red' },
};

export const RESULT_STATUS: Record<string, { label: string; color: string }> = {
  completed: { label: '완료', color: 'teal' },
  completed_with_issues: { label: '완료 · 문제 남음', color: 'orange' },
  failed: { label: '실패', color: 'red' },
};

export const SET_LABEL: Record<string, string> = { shop: '쇼핑몰 표준', none: '설정 없음' };

export const fmt = (n: number | undefined | null) => (n ?? 0).toLocaleString('ko-KR');
export const when = (iso: string) =>
  new Date(iso).toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' });

export const GRADE: Record<string, { label: string; color: string }> = {
  exact: { label: '정확 일치', color: 'blue' },
  alias: { label: '별칭', color: 'teal' },
  reuse: { label: '재사용 규칙', color: 'grape' },
  similar: { label: '유사 이름', color: 'orange' },
  shape: { label: '값 모양', color: 'orange' },
  none: { label: '후보 없음', color: 'gray' },
};

export const REASON: Record<string, string> = {
  one_to_one: '일대일 위반',
  normalization_collision: '정규화 충돌',
  similar_tie: '후보 동률',
  shape_tie: '후보 동률',
};

export const TABLE_BASIS: Record<string, string> = { name: '이름 일치', headers: '헤더 비율', user: '직접 선택', reuse: '재사용 규칙' };

export const where = (
  s?: { file: string | null; record: number; line_start: number | null; line_end: number | null; sheet?: string; sheet_row?: number } | null,
) =>
  !s
    ? ''
    : s.sheet
      ? `${s.file ?? ''} [${s.sheet}] ${s.record}행 · 엑셀 ${s.sheet_row}행`
      : `${s.file ?? ''} ${s.record}행 · ${s.line_start === s.line_end ? `${s.line_start}줄` : `${s.line_start}–${s.line_end}줄`}`;

export const APPROVAL_KIND: Record<string, string> = { structure: '구조 승인', execution: '실행 승인' };
const HASH_LABEL: Record<string, string> = {
  config_hash: '설정',
  input_hash: '실행 대상',
  mapping_hash: '매핑',
  plan_hash: '실행 계획',
  view_hash: '표준화 뷰',
  candidate_hash: '표준화 후보',
  apply_hash: '적용 선택',
};
const INVALIDATED: Record<string, string> = {
  targets_changed: '실행 대상 변경',
  mapping_changed: '매핑 변경',
  mapping_reconfirmed: '매핑 재확정',
  plan_regenerated: '실행 계획 재생성',
  view_regenerated: '다시 진단',
  selection_changed: '적용 선택 변경',
  superseded: '새 승인으로 대체',
};
export const invalidated = (reason: string | null) => {
  if (!reason) return '';
  if (reason.startsWith('hash_mismatch:'))
    return `승인 때와 다름: ${reason.slice(14).split(',').map((k) => HASH_LABEL[k] ?? k).join(', ')}`;
  return INVALIDATED[reason] ?? reason;
};

export const INTEGRITY_BASIS: Record<string, string> = { raw: '원문 기준', virtual: '가상 기준', approved: '승인 기준', result: '결과 재검사' };
export const INTEGRITY_REASON: Record<string, string> = {
  parse_error: '해석 불가',
  missing_value: '값 없음',
  missing_reference: '연결 대상 없음',
  duplicate_parent: '부모 키 중복',
  ambiguous_join: '연결 모호',
  missing_column: '컬럼 없음',
  excluded_target: '대상 테이블 없음',
};

export const DRIFT_STATE: Record<DriftState, { label: string; color: string }> = {
  no_change: { label: '변경 없음', color: 'teal' },
  changed: { label: '변경 있음', color: 'orange' },
  choose: { label: '비교 기준 선택 필요', color: 'red' },
  baseline_create: { label: '기준 생성', color: 'gray' },
  no_baseline: { label: '기준 없음', color: 'gray' },
};

export const DRIFT_KIND: Record<string, string> = {
  raw_headers: '원래 헤더 변경',
  column_removed: '컬럼 삭제',
  column_added: '컬럼 추가',
  rename_suspected: '이름 변경 의심',
  rename_undetermined: '이름 변경 판단 안 함',
  value_shape: '값 모양 변화',
  shape_undetermined: '값 모양 판단 안 함',
  value_range: '값 범위 변화',
  category_new: '범주 새 값',
};
