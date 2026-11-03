# M2 중간 점수표 — 개발셋 (2026-11-03)

> **현재 구현 범위(M1 코어 + M2 채점기)의 중간 개발 결과다.** 최종 평가 결과가 아니며 성능 주장에 쓰지 않는다.
> 개발셋은 규칙·별칭을 만들 때 근거로 쓴 분할이라(별칭 사전은 개발용 한글 헤더, 엔진 변환은 개발 입력 표현 기준)
> 높은 값이 나오는 것이 정상이다. 일반화 성능은 M10에서 고정 후 평가셋으로 한 번 잰다(④ 7절).
> 재현: `make score-dev` (같은 실행을 다시 채점하면 표 내용 해시가 같다 — AC-14-4 확인).

**읽는 법**
- 탐지 실패 2건은 그대로 둔다: `total_conflict`는 정합성 검사 R02가 M4 범위라 미구현, `ambiguous_missing`은 현재 엔진에 결측 의미를 사람 검토로 올리는 규칙이 없다. M4~M7 후 다시 채점해 회귀를 확인한다.
- 비대상 셀 경고(위반 12 · 사람 검토 64)는 정답에 없는 파생 경고라 오탐으로 판정하지 않는다(중복 행·중복 키의 나머지 컬럼 등).
- 사례별 상세는 `workspace/scores/`(Git 제외)에만 있다.

---

> 합성 벤치마크 결과이며 실제 업무 효과가 아니다. 이 표는 현재 구현 범위의 결과다.

- 코드 커밋: b32ca9725c8be0696e2027d3d0fb211400236fd0
- 작업 트리 변경 있음: False
- 채점기: judge-v1 (quality-evaluation-spec v0.2 5절)
- 분할: development
- 평가 조건: rules-only
- 실행: 20261103T055610Z-496015
- 실행 상태: completed_with_issues
- 설정 스냅샷: 7f216cf2fcb955223eb2205d1e74d22cc9efbde2dcf9ec433a67e1d0a26e34d3
- 재시도 사유: None
- 표 내용 해시: `95ce8446a1f2fdff24b3d374ca927060fb4b2b392e9d96ab9d0f49a689ad42e9`

## 헤더 매핑 (map_column)

| 구분 | 유실 | 정답 | 오답 | 확인 필요 | 후보 없음 |
|---|---|---|---|---|---|
| 자동 | 0 | 106 | 0 | 0 | 0 |

## 헤더 매핑 — 최종

| 구분 | 유실 | 정답 | 오답 | 미완료 |
|---|---|---|---|---|
| 최종 | 0 | 106 | 0 | 0 |

- 자동 정확도 1.0 · 자동 범위 1.0 · 최종 정확도 1.0 (n=106)

## 셀 표준화 (normalize)

| pattern_group | issue_type | n | 유실 | 정답 | 오변환 | 보류 | 누락 | 정답률 |
|---|---|---|---|---|---|---|---|---|
| seen | datetime_format | 1577 | 0 | 1577 | 0 | 0 | 0 | 1.0 |
| seen | enum_case | 1135 | 0 | 1135 | 0 | 0 | 0 | 1.0 |
| seen | money_format | 1227 | 0 | 1227 | 0 | 0 | 0 | 1.0 |
| seen | null_token | 139 | 0 | 139 | 0 | 0 | 0 | 1.0 |
| seen | outer_whitespace | 90 | 0 | 90 | 0 | 0 | 0 | 1.0 |
| seen | phone_format | 256 | 0 | 256 | 0 | 0 | 0 | 1.0 |

## 검토 (review) — 건수

| pattern_group | issue_type | n | 탐지 | 보존 |
|---|---|---|---|---|
| seen | ambiguous_date | 1 | 1 | 1 |
| seen | ambiguous_missing | 1 | 0 | 1 |
| seen | duplicate_pk | 12 | 12 | 12 |
| seen | required_missing | 1 | 1 | 1 |
| seen | total_conflict | 1 | 0 | 1 |
| seen | truncated_phone | 1 | 1 | 1 |
| seen | unknown_fk | 1 | 1 | 1 |
| seen | zero_quantity | 1 | 1 | 1 |

## 유지 (keep) — 건수

| pattern_group | issue_type | n | 유실 | 값 변경 | 오류 확정 | 추가 검토 요청 | 참고 관측 | 표시 없음 | 오판 |
|---|---|---|---|---|---|---|---|---|---|
| seen | valid_historical_price | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 0 |
| seen | valid_large_quantity | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 0 |
| seen | valid_optional_null | 3 | 0 | 0 | 0 | 0 | 3 | 0 | 0 |
| seen | valid_pending_tracking_null | 3 | 0 | 0 | 0 | 0 | 3 | 0 | 0 |
| seen | valid_root_null | 3 | 0 | 0 | 0 | 0 | 3 | 0 | 0 |
| seen | valid_zero_discount | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 0 |

## 불필요 변경

| 범주 | 건수 | 분모 | 유실 |
|---|---|---|---|
| normalize 오변환 | 0 | 4424 | — |
| review 보호 대상 변경·삭제·병합 | 0 | 19 | — |
| keep 대상 변경 | 0 | 18 | — |
| 그 밖의 셀 변경 | 0 | 47840 | 0 |

## 0건 조건

| 항목 | 값 |
|---|---|
| 승인 없는 변경(서비스 감사) | 0 |
| 감사 통과 | True |
| 정답 기준 비대상 오수정 | 0 |
| 변경 기록 없는 정답 | 0 |
| 논리 null 불일치 | 0 |
| 매핑이 정답 컬럼과 다른 사례 | 0 |

비대상 셀 경고(오탐으로 판정하지 않음): 위반 12 · 사람 검토 64

## 주의

- total_conflict: 정합성 검사(R02) 미구현 — M4
