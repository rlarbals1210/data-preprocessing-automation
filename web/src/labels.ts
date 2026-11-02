import type { Category } from './api';

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

export const TABLE_BASIS: Record<string, string> = { name: '이름 일치', headers: '헤더 비율', user: '직접 선택' };

export const where = (s?: { file: string | null; record: number; line_start: number; line_end: number } | null) =>
  !s ? '' : `${s.file ?? ''} ${s.record}행 · ${s.line_start === s.line_end ? `${s.line_start}줄` : `${s.line_start}–${s.line_end}줄`}`;
