# 기술 설계서 — 데이터 전처리 자동화 프로토타입

| 항목 | 내용 |
|---|---|
| 문서 | 사전 문서 5종 중 ③ 기술 설계서 |
| 버전·상태 | **v0.2 (2026-11-02) — 확정.** Claude 작성 → Codex 리뷰·토론 합의 36건 → 규민님 결정 4건(14절) |
| 상위 문서 | ① `docs/prd.md` v0.2, ② `docs/functional-spec.md` v0.2 — 무엇을 만들지는 두 문서가 정본이고, 이 문서는 어떻게 만들지를 정한다 |
| 다음 문서 | ④ 품질·평가 명세 — 임계값·공식·성능 목표 |

> 기능 ID(F01~F16)·수용 기준(AC-xx)·화면 ID(SC01~SC12)는 기능 명세서 번호를 그대로 쓴다. 이 문서가 새로 정하는 테스트는 `T-xx`로 적는다.
> 이 문서의 JSON 예시는 설명용이다. 확정 계약은 부록 A(정합성 규칙 10개)와 각 형식의 검증 테스트다.
> 선택 기준은 "1인 로컬, 소규모 파일, 설명 가능한 구조"다. 대규모 처리·동시 사용·배포 운영은 고려하지 않는다(PRD 비목표).

---

## 1. 현재 코드에서 출발점과 엔진 변경 범위

| 항목 | 현재 상태(코드로 확인) | 이번 설계 |
|---|---|---|
| 진단 `diagnostics/engine.py` | CSV 파일명 = 테이블, 설정 컬럼 키 = 헤더 정확 일치. 설정 컬럼이 하나라도 없으면 그 테이블 전체를 검사에서 빼고 `status=incomplete`. `rule_counts`는 문제 코드별 건수(실행 증거 아님). 설정 검증기는 `required_if`·`unique`·`references`가 설정 안에서 완결되길 요구 | **확장 ①** 규칙별 실행 기록 출력. 그 밖의 검사 로직은 유지 |
| 분류 `diagnostics/classification.py` | 표준화 정책은 문자열 하나, 세 ID(`krw_integer`·`kr_mobile`·`iso_datetime`)만 허용 | **확장 ②** 변환 등록표와 새 정책 ID |
| 정제 `diagnostics/cleaning.py` | 진단이 incomplete면 정제 거부. 후보 전부 적용, 위반·검토 셀은 보류(중복 행 예외), 재진단에서 위반·검토·중복이 새로 생기면 실패, 전체 셀 대조 후 내부 `data/` 이동 | **확장 ③** `allow`·`extra_blocked` 인자. 기본값이면 지금과 같은 동작 |
| 진단 설정 `config/shop-diagnostics.json` | 개발용 한글 헤더 기준 | 원본 유지, 논리 설정을 따로 만듦(5절) |
| 테스트 | `unittest` 3개 | pytest로 실행. 기존 테스트 전부 통과 + 확장마다 명령줄 회귀 테스트 |
| Python | `.venv` = macOS 기본 3.9.6 | **3.12 + uv**(결정 D3). 기존 테스트·명령줄 회귀 확인이 조건 |
| Git | 저장소 아님 | 초기화 + 평가 정답 제외(11절) |

**엔진 확장 원칙:** 세 확장 모두 새 인자·새 출력 필드를 **추가만** 한다. 인자를 생략하면 기존 명령줄(`scripts/diagnose.py`, `scripts/clean.py`)의 결과·반환값·종료 코드가 바뀌지 않는다(T-01: 기존 개발셋 실행 결과를 확장 전후로 비교).

---

## 2. 아키텍처

```
┌──────────────────────── 이 컴퓨터(127.0.0.1) ────────────────────────┐
│  web/  React + TypeScript (Vite, Mantine)                              │
│    └─ HTTP(JSON) ─────────────┐                                        │
│                               ▼                                        │
│  api/  FastAPI — 요청 검증·화면용 응답 구성만                          │
│                               ▼                                        │
│  prep/  애플리케이션 코어 (순수 Python)                                 │
│    io · mapping · plan(실행 계획) · view(표준화 뷰·역추적) · transforms │
│    integrity · preview · runs(상태·승인·스냅샷) · publish · versions   │
│    reuse · drift · pii · share · quality · report · guard · store      │
│                               ▼ 허용 경로만 넘겨 호출                  │
│  diagnostics/  기존 엔진 + 확장 ①②③                                    │
│  ai/  (P2) SuggestionProvider — 기본 NullProvider                       │
│  workspace/  app.db · blobs/ · config-snapshots/ · runs/ · shares/     │
│  ───────────── 서비스 경계 바깥 ─────────────                           │
│  evaluation/  채점기(별도 명령) · tests/ · 정답·정상 기준·생성기         │
└────────────────────────────────────────────────────────────────────────┘
```

- **계층:** `web → api → prep → diagnostics`. 서비스(`api`·`prep`·`diagnostics`·`ai`)는 `evaluation`을 import하지 않는다.
- **동기 처리:** 각 단계는 요청 하나에서 끝나는 동기 처리로 시작한다. ④의 응답 시간 목표를 넘으면 백그라운드 작업으로 바꾼다.
- **바인딩:** API는 `127.0.0.1`만. CORS는 개발 서버 주소 하나만 허용.

---

## 3. 저장소·보관 위치

```
~/Developer/data-preprocessing-automation/      # Git 저장소(코드·문서 정본)
├── pyproject.toml (uv, Python 3.12, ruff, pytest)
├── diagnostics/   prep/   api/   web/   ai/   evaluation/
├── config/
│   ├── shop-diagnostics.json · normalization-policy.json   # 기존(변경 없음)
│   └── sets/
│       ├── shop/       logical-diagnostics.json · legacy-header-map.json · aliases.json
│       │               integrity.json · pii.json · masking-defaults.json · transforms.json
│       └── inventory/  (재고 예제 설정 세트, F16)
├── tests/  fixtures/(개발용 입력만) · unit/ · integration/ · isolation/ · e2e/
└── workspace/   # 실행 데이터(git 제외)

/Volumes/SSD/공모전·프로젝트자료/데이터-전처리-자동화/   # 저장소에 넣지 않는 자료(결정 D4)
├── 평가정답-백업/<날짜>/   evaluation_private 사본 · evaluation-answers.zip · SHA256SUMS · README
└── 대용량-데이터/          용량이 큰 원천·실데이터·실행 산출물
```

- 평가 정답 원본은 프로젝트 폴더(`evaluation_private/`, `deliverables/evaluation-answers.zip`)에 그대로 두고 Git에서 제외한다. SSD에는 해시를 확인한 사본을 둔다(2026-11-02 첫 백업, 9개 파일 해시 일치 확인).
- 용량이 큰 자료는 SSD에 두고, 서비스는 업로드로만 받는다(서비스가 SSD 경로를 직접 읽지 않음 — 11절 허용 경로).

---

## 4. 데이터 모델

### 4-1. 저장 원칙
- 메타데이터는 SQLite 하나(`workspace/app.db`, 표준 `sqlite3`), 파일은 내용 주소 저장(`workspace/blobs/<sha256>`)과 실행별 폴더.
- 기록은 덧붙이기만 한다. 승인 내용·설정 스냅샷·결정 스냅샷은 만든 뒤 바꾸지 않는다. 현재 상태 필드(`run.status`, `revoked_at` 등)는 갱신할 수 있으나 모든 전이를 `event`에 남긴다.
- 원본 파일은 읽을 때마다 실제 바이트 해시를 DB 값과 대조한다.

### 4-2. 해시와 스냅샷

| 이름 | 범위 |
|---|---|
| 설정 스냅샷 해시 | 실행이 기대는 파일 전체(설정 세트 폴더 + `schema.json` + `normalization-policy.json`)를 스냅샷 폴더에 복사하고, (상대 경로, 내용)을 경로순으로 정렬해 해시. 실행은 디스크 원본이 아닌 스냅샷을 쓴다 |
| 입력 해시 | 실행 대상(업로드 항목 ID, blob 해시, 시트, **읽기 조건: 인코딩·구분자**) 정렬 목록 |
| 매핑 버전 해시 | 확정 매핑(항목·시트 → 표준 테이블, 원래 컬럼 → 논리 컬럼/추가 컬럼) |
| 실행 계획 해시 | 실행할 규칙·실행 안 할 규칙과 사유, 정합성 규칙 켜짐/꺼짐 |
| 뷰 해시 | 표준화 뷰 CSV들 + 역추적 표(행 순서 포함) |
| 후보 해시 | 서버가 만든 최신 표준화 후보(원본 위치·논리 컬럼·원문·제안값·변환 ID) + 보류 목록 |
| 적용 계획 해시 | 서버가 검증한 최종 허용 셀 목록(승인 묶음·제외 셀 반영) |

실행 승인은 위 7개 해시를 모두 저장한다. 하나라도 현재와 다르면 승인을 무효로 하고 해당 단계부터 다시 만든다(AC-06-4, AC-07-6).

### 4-3. 테이블

| 테이블 | 주요 컬럼 | 비고 |
|---|---|---|
| config_snapshot | id, hash, path, created_at | 과거 설정 내용 조회 가능 |
| source | id, name, created_at | 출처 |
| template | id, source_id, std_table, header_signature, headers(json: 정규화·정렬 헤더), created_in_run | 비교·재사용 단위(출처+표준 테이블+양식) |
| run_template | run_id, mapping_version_id, upload_item_id, template_id | 매핑 버전마다 항목→양식 연결(M6-2) |
| template_profile | id, template_id, result_version_id, run_id, upload_item_id, order_no, profile(json) | 비교 기준 프로파일. 그 실행의 정제 결과 버전에만 붙음(트리거, M6-2) |
| batch | id, source_id, created_at | |
| upload_item | id, batch_id, filename, blob_sha256, kind, sheet, read_options, read_status, read_errors | 시트마다 한 행 |
| run | id, batch_id, retry_of, status, config_snapshot_id, created_at | 실패 시도도 남음 |
| run_target | run_id, upload_item_id, order_no, included, exclusion_reason | 실행 대상과 **행 순서** 고정 |
| mapping_version | id, run_id, version, auto_proposal(json), final(json), hash, created_at | 무효화돼도 이전 버전 보존 |
| run_plan | id, run_id, mapping_version_id, executed_rules(json), skipped_rules(json), integrity_enabled(json), hash | 실행 계획 |
| view_snapshot | id, run_id, run_plan_id, path, lineage_path, hash | 표준화 뷰·역추적 |
| apply_plan | id, run_id, view_snapshot_id, candidates_hash, allow(json), excluded(json), hash | 서버가 검증한 적용 계획 |
| approval | id, run_id, kind(structure/execution/reuse), subject_id, input_hash, mapping_hash, plan_hash, view_hash, candidate_hash, apply_hash, config_hash, created_at, invalidated_at, invalidated_reason | |
| attempt | id, run_id, path, started_at, ended_at, outcome | 엔진 정제 시도 폴더(완료 판정 아님) |
| result_version | id, run_id(nullable), kind(cleaned/restore), restored_from, attempt_id, data_hashes(json), manifest_path, created_at | **완료 판정의 유일한 기준** |
| run_metrics | run_id, review_items(json), inputs(json), created_at | F09 검토 항목 수와 계산 근거. 실행 승인 때 한 번, 덧붙이기만(M6-8, DB 7). review_items = mapping·groups·human_review·business_groups·held·total |
| reuse_rule | id, template_id, kind(mapping/standardize), rule_key, version, spec(json), transform_id, config_hash, approval_id(같은 실행의 reuse 승인), approved_in_run, approved_at, revoked_at, revoked_reason | 키(양식·종류·rule_key)마다 버전. 취소 표시만 한 번(M6-5) |
| masking_config | id, template_id, version, decisions(json), confirmed_at | 템플릿별 결정 |
| masking_snapshot | id, result_version_id, decisions(json: 템플릿·표준 테이블·컬럼 키별), hash, created_at | 한 번의 공유에 쓴 전체 결정 |
| share_output | id, result_version_id, masking_snapshot_id, path, hashes(json), created_at | 재생성은 새 행·새 폴더 |
| quality_score | id, result_version_id, config_hash, scope(json), definition_version, scores(json), computed_at | 캐시 키 = 버전·설정·범위·**점수 정의 버전** |
| event | id, subject_type, subject_id, kind, payload(json), created_at | 실행·복원·규칙 취소·마스킹 확정 등 모두 |

---

## 5. 실행 계획과 표준화 뷰 (F03·F04)

### 5-1. 논리 설정 (한 번 만들고 검토 후 커밋)
- `legacy-header-map.json`(한글 헤더 → 논리 컬럼, 한글 파일명 → 표준 테이블)을 사람이 작성·검토한다.
- 변환 스크립트가 컬럼 키와 `required_if.column`·`unique`·`references.columns/table/target_columns`를 모두 논리 이름으로 바꿔 `logical-diagnostics.json`을 만든다. 원 설정 파일은 바꾸지 않는다.
- T-02: 원 설정의 모든 컬럼 참조가 변환됐는지, 변환 후 참조가 모두 `schema.json`에 있는지, 테이블·규칙 수가 같은지.
- 논리 설정은 새 정책 ID(6절)를 쓸 수 있다. 원 설정은 기존 ID 그대로다.

### 5-2. 실행 계획 (엔진 호출 전, `prep/plan.py`)
1. 확정 매핑으로 각 표준 테이블에 실제로 있는 논리 컬럼을 구한다.
2. 논리 설정의 규칙을 하나씩 판정한다:
   - 컬럼이 없음 → 실행 안 함(`missing_column`)
   - 참조 대상 테이블이 실행 대상에 없음 → 실행 안 함(`excluded_target`) — 참조 오류로 바꾸지 않음
   - 실행 안 하는 컬럼에 기대는 `required_if`·`unique`·`references` → 실행 안 함(`dependency_skipped`)
3. 실행할 규칙만으로 엔진용 설정을 만든다(엔진 검증기를 통과하는 완결된 설정). 실행 안 할 규칙은 사유와 함께 `run_plan.skipped_rules`에 남긴다. 빠진 컬럼을 빈 값으로 채우지 않는다.
4. 테이블 판정: 진단 가능(실행할 규칙 1개 이상) / 발행 가능(필수 표준 컬럼이 모두 있고 진단 가능) / 발행 제외(사유).
5. 서비스의 검사 완전성 = 실행 계획 기준(전체/부분). 엔진의 `status`는 엔진 내부 판정으로만 쓴다. 기존 명령줄의 "incomplete면 실패" 계약은 그대로다.

**엔진 확장 ①:** `diagnose()` 결과에 `rule_execution` 목록(테이블, 컬럼 또는 규칙 그룹, 규칙 종류, executed, reason)을 추가한다. 실행 증거는 이 목록으로 확인한다(`rule_counts` 아님).

T-03: 단일 테이블 입력, 선택 컬럼 누락, 필수 컬럼 누락, 부모 테이블 제외 — 각각 진단·검사 완전성·발행 판정이 FS와 일치.

### 5-3. 표준화 뷰 (실행마다, `prep/view.py`)
- **행 순서:** `run_target.order_no`(사용자가 확정한 실행 대상 순서) → 원래 레코드 번호. 뷰 해시가 승인에 묶인다.
- **컬럼:** 매핑된 논리 컬럼 + 보조 컬럼 `__extras_digest__`. 매핑하지 않은 추가 컬럼은 뷰에 넣지 않고 원문을 역추적 표에 보관한다. "진단 대상에서 제외"와 "출력에서 삭제"는 다르며, 결과 출력과 공유 출력에서 추가 컬럼을 다시 붙인다(공유 출력에서는 마스킹 결정을 따름).
- **`__extras_digest__`:** 그 행의 (템플릿 ID, 추가 컬럼 이름·값 목록 — 없는 컬럼은 '없음' 표시)의 해시. 설정되지 않은 컬럼이라 엔진은 프로파일만 하며, 엔진의 동일 행 중복 판정에 포함되어 **추가 컬럼만 다른 두 행이 중복으로 묶이지 않는다.** 화면·출력·프로파일·공유에는 나오지 않는다. PK 중복은 유일성 규칙이 잡는다.
- **컬럼 구성이 다른 파일 통합:**
  - 어떤 파일에 필수 논리 컬럼이 없으면 그 파일은 통합하지 않는다. 사용자가 그 파일을 실행 대상에서 빼거나, 그 테이블을 발행 제외로 둘지 고른다(합집합 헤더로 숨기지 않음).
  - 선택 논리 컬럼이 일부 파일에만 있으면, 없는 파일의 셀은 뷰에 빈 문자열로 쓰되 역추적 표에 `absent`로 표시한다. 그 셀의 진단 항목은 "원래 컬럼 없음"으로 바꿔 보여 주고, 결과 출력에서도 실제 빈 셀과 구분해 `absent-cells.json`에 남긴다.
- **역추적 표:** 뷰 (테이블, 레코드) → (업로드 항목 ID, 시트, 원래 레코드 번호, CSV 물리 줄 범위 또는 엑셀 셀 좌표), 뷰 컬럼 → 원래 컬럼 이름, 셀 메타(`absent`, 엑셀 원래 자료형). 엔진 결과의 **모든 위치 정보**(진단 항목, 중복 그룹 레코드, 변경 내역, 보류 목록, 검증 실패 항목)를 이 표로 바꾼다.
- **용어:** 뷰는 다시 쓴 파일이므로 "셀 문자열 동일"은 보장하지만 "파일 바이트 동일"은 아니다. 원본 해시는 업로드 원본으로 관리한다.

T-04: 표준 컬럼은 같고 추가 컬럼만 다른 두 행 / 같은 이름 파일 중복 업로드 / 여러 줄 CSV 필드 / 파일마다 다른 추가 컬럼 / 한 파일에만 필수 컬럼 없음 — 값 손실·오중복·위치 오연결 없음.
T-05(AC-04-1·04-2): 개발용 한글 헤더 입력과 같은 내용을 영문 별칭·다른 순서로 바꾼 fixture의 (분류, 규칙 코드, 원본 위치, 논리 컬럼) 집합과 `rule_execution`이 같다.

---

## 6. 정책 변환 (F04, 결정 Q11) — 엔진 확장 ②

- `classification.py`에 **변환 등록표**를 둔다: 변환 ID → (대상 타입, 변환 함수, 정책 근거 키). 설정 검증기(`engine.py`)도 같은 등록표로 정책 ID·타입 적합성을 검사한다.
- 새 변환은 **새 정책 ID**로 등록한다(예: `kr_mobile_intl` = 기존 `kr_mobile` + 명시적 `+82`, `krw_integer_symbol` = 기존 `krw_integer` + `₩`). 논리 설정만 새 ID를 쓰고, 원 설정·기존 ID의 동작은 그대로다(T-01).
- 정책 문서에 있는 지원 대상 변환은 모두 P0에서 구현한다. 정책에도 없는 표현, 그리고 시간대 없는 날짜·시각(결정 Q8)만 사람 검토로 남긴다.
- 변환은 기존 계약을 따른다: 명시적 형식만, 잘린·모호한 값 추정 금지, 변환 후 타입·패턴·허용값·범위 검사를 통과해야 후보, 같은 셀의 중복 제안 합치기·충돌 제안은 오류.
- `transforms.json`은 정책 항목 ↔ 변환 ID ↔ 테스트 이름 ↔ 결정 근거의 **기록**이며 실행 설정이 아니다. 실행에 쓰이는 것은 논리 설정의 `standardization` 값뿐이다.
- T-06(AC-04-5): 변환마다 "정상 변환"과 "변환하면 안 되는 값" 테스트 쌍.

**시간대 없는 날짜·시각:** 현재 시각 형식은 시간대(`%z`)를 요구하므로, 시간대 없는 CSV 값과 엑셀에서 온 값(7절) 모두 엔진의 기존 동작대로 "형식 해석 실패 → 사람 검토"가 된다. 별도 항목을 추가하지 않고, 역추적 셀 메타로 이유 표시만 "시간대 없음"으로 보강한다(이중 집계 없음). T-07: 같은 시간대 없는 값이 CSV와 엑셀에서 같은 분류·보류·승인 판정을 받는다.

---

## 7. 입력 읽기 (F01·F02)

**CSV:** 기존 `read_table()`(문자열 그대로, 인코딩·구분자 지정). 읽기 조건은 업로드 항목에 저장하고 입력 해시에 포함한다.

**xlsx:** `openpyxl` 일반 모드(병합 셀 정보), `data_only=False`(수식 감지).

| 셀 | 저장값 처리 | 셀 메타 |
|---|---|---|
| 텍스트 | 그대로 | text |
| 숫자 | 정수값이면 정수 문자열(`123`), 아니면 최소 표현. 앞자리 0을 붙이지 않음 | number |
| 날짜·시각 | 워크북 날짜 체계를 openpyxl이 해석한 값을 시간대 없는 ISO 문자열로 | datetime, tz=none → 사람 검토(6절) |
| 불리언 | `TRUE`/`FALSE` | bool |
| 빈 셀 | 빈 문자열 | empty |
| 수식 | 읽지 않음 | 지원 안 하는 구조 |

- **구현(M4-5, `prep/sheets.py`):** 시트 하나 = 업로드 항목 하나(읽기 조건 `{format: xlsx, sheet}`, 원본 바이트는 파일 하나로 보관). 역추적 표의 엑셀 행은 `line_start/line_end` 대신 `sheet`·`sheet_row`·원래 셀 자료형(텍스트 외)을 두고, 원본 위치는 셀 좌표(예: `G3`)로 바꾼다. 뒤쪽 빈 행·열은 버리고 중간 빈 행은 레코드로 남긴다. fixture는 테스트에서 openpyxl로 만든다(`tests/test_xlsx.py`).
- 지원 안 하는 구조(첫 버전): 병합 셀, 수식 셀, 첫 행의 빈 머리글 열, 첫 행 중복 머리글. 이 목록과 `tests/fixtures/xlsx/`가 AC-02-2의 범위다. 그 밖의 모호한 구조는 감지를 보장하지 않으며, 의심 신호는 "사용자 확인" 표시만 한다.

---

## 8. 승인·정제·발행 (F06·F07)

### 8-1. 상태 전이

```
업로드됨 ─(실행 대상 확정)→ 확인 필요[스키마 변경] ─→ 확인 필요[매핑]
  ─(구조 승인)→ 실행 계획·뷰 생성 → 진단 완료 ─→ 승인 대기
  ─(실행 승인: 해시 7종)→ 시도(attempt) ─┬→ 발행 성공 → 완료 / 완료(문제 남음)
                                          └→ 실패 (결과 없음, 실패 리포트만)
입력·매핑·계획·뷰·후보·적용 계획·설정 중 하나라도 바뀌면 → 해당 단계부터 다시, 이후 승인 무효
```

### 8-2. 적용 계획 검증
- 사용자의 선택(승인 묶음, 제외 셀)을 받아 서버가 최종 허용 셀 목록을 만든다.
- 허용 셀은 반드시 **현재 뷰의 최신 후보 − 보류**의 부분집합이어야 한다. 없는 셀·후보가 아닌 셀·보류 셀·이전 뷰의 레코드 키는 거부한다(T-08).
- 미리보기와 실행의 적용 건수는 같은 적용 계획에서 나온다(AC-06-5).

### 8-3. 보류 대응표

보류는 사용자 선택과 무관하게 **원문 기준과 가상 기준(후보를 모두 적용)** 으로 정한다. 그래서 선택을 바꿔도 보류 여부가 바뀌지 않는다(순환 없음).

| 같은 셀에 있는 미해결 문제 | 그 셀의 표준화 후보 |
|---|---|
| 업무 규칙 위반(필수·허용값·범위·유일성·참조) | 보류 |
| 사람 검토(정책 없는 표현·해결 안 된 형식·시간대 없음) | 보류 |
| 정합성 위반 묶음에서 계산에 직접 쓰인 셀 — 원문 또는 가상 기준에서 위반 | 보류 |
| 정합성 계산 불가(파싱 불가)의 원인 셀이고, 가상 기준에서도 위반이 아님 | 보류 안 함(표준화가 해결책) |
| 동일 행 중복 그룹에만 속함 | 보류 안 함(기존 예외) |
| 같은 셀에 서로 다른 제안 | 실행 오류(기존 계약) |

예: `line_total="1,000"`, `quantity=2`, `unit_price=600`, `discount_amount=0` → 가상 기준 1000 ≠ 1200(위반) → `line_total` 후보는 보류, 원문 유지, 결과에서 미해결 검토로 남음. 화면의 "승인 기준" 계산은 보여 주기만 한다.

### 8-4. 정제 — 엔진 확장 ③
- `clean(..., allow=None, extra_blocked=None)`:
  - `allow=None`이면 기존처럼 보류되지 않은 후보 전부 적용(명령줄 동작 유지). `allow=set()`이면 0셀 변경.
  - `extra_blocked`: 정합성 보류·서비스 보류 셀. 기존 `blocked`에 합친다.
  - `allow`에 없는 후보는 적용하지 않고 `not_approved`로 기록한다. 이 후보들은 정제 후 진단에서도 표준화 후보로 남을 뿐 문제 항목(`problem_keys`는 위반·검토만 셈)이 아니므로 실패로 세지 않는다.
  - 후보 재생성·충돌 검사·같은 셀 제안 합치기·재진단·전체 셀 대조는 기존 그대로.
- 엔진은 서비스가 만든 시도 폴더(`runs/<run_id>/attempt-<n>/`)에 결과를 쓴다. 엔진 내부의 `data/` 이동은 **서비스 발행이 아니다.**

### 8-5. 발행 순서 (`prep/publish.py`)
1. 엔진 정제를 시도 폴더에 실행한다(실패하면 시도 기록만 남김).
2. 시도 결과로 켜 둔 정합성 규칙을 다시 평가한다. 실행 전 대비 **신규 위반**(비교 키 = 규칙 ID + 관련 원본 위치 정렬 목록)이 있으면 실패.
3. 결과 파일 해시를 계산하고, 역추적으로 원본 위치 기준 전체 셀 대조를 확인한다.
4. **DB 트랜잭션 하나**로 `result_version` 행과 실행 상태(완료/완료(문제 남음))를 기록한다.
- 완료 판정 기준은 `result_version` 행 하나다. 행이 없는 시도 폴더는 미완료 흔적으로 남고 다운로드 대상이 아니다. 다운로드 시 해시를 다시 대조하고, 다르면 오류를 낸다.
- 결과 폴더(`attempt-n/result/`, M1-10c·M3-3): 발행 가능한 테이블 CSV, `changes.csv`·`changes.json`(논리 null 구분), `diagnosis-before.json`·`diagnosis-after.json`, `held.json`, `absent-cells.json` — 모두 원본 위치 포함, 해시는 `result_version`에 기록. `manifest.json`은 적힌 파일 해시가 그 기록과 같을 때만 내려받는다. 실패한 실행은 결과 데이터 없이 실패 진단 리포트(`GET /failure-report`)만 준다.
- T-09: 빈 허용 목록, 일부 승인, 후보 외 허용, 보류 셀 승인 시도, 정합성 재검사 실패, 발행 중 파일 실패, DB 기록 실패, 기존 명령줄 회귀.

---

## 9. 정합성 검사 (F05)

### 9-1. 규칙 문법

모든 규칙은 하나의 **기준 테이블(anchor)** 의 각 행에 대해 평가한다. 표현식은 JSON AST이며 문자열을 실행하지 않는다.

- **공통 키:** `id`, `type`, `enabled`, `anchor`(테이블), `when`(선택, 조건), `description`.
- **조건:** `{"col": c, "in": [...]}`, `{"col": c, "not_in": [...]}`, `{"present": c}`(비어 있지 않음), `{"all": [조건...]}`, `{"any": [조건...]}`.
- **연결 경로 `path`:** 기준 행에서 시작하는 단계 목록. 단계 = `{"to": 테이블, "on": [[현재 컬럼, 대상 컬럼], ...], "where": 조건(선택)}`. 여러 단계를 이어 다단계 연결(반품 → 반품 상세 → 출고 상세 → 출고)을 표현한다.
- **값 표현식:** `{"col": c}`(기준 행), `{"const": 정수}`, `{"add": [..]}`, `{"sub": [a, b]}`, `{"mul": [..]}`, `{"sum": {"path": [...], "col": c}}`, `{"count": {"path": [...]}}`, `{"max": {"path": [...], "col": c}}`, `{"min": ...}`. 정수 컬럼은 논리 설정의 타입으로 해석하고, 시각 컬럼은 시간대가 있는 ISO 형식만 비교한다.
- **규칙 종류 5개:**

| type | 필수 키 | 판정 |
|---|---|---|
| `row_equation` | `left`, `right` (행 값 표현식) | left = right |
| `parent_sum_equation` | `left`, `right`(집계 포함), `empty_child` | left = right |
| `cumulative_le` | `limit`(행 값), `total`(sum 집계), `empty_child` | total ≤ limit |
| `time_order` | `earlier`, `later`(행 값 또는 경로 max/min), `each`(선택: 연결 행마다 비교), `empty_child` | earlier ≤ later |
| `status_requires` | `requires`: [{`count`: 경로, `op`: eq/le/ge, `value`}] | 모든 요구 충족 |

- **`empty_child`(연결 결과가 0행일 때):** `ok` / `violation` / `not_computable` 중 규칙마다 지정한다. 연결 대상 테이블이 실행 대상에 없으면 이 값과 무관하게 `not_computable(missing_reference)`.

### 9-2. 결과 상태와 사유

| 상태 | 뜻 |
|---|---|
| `ok` | 계산했고 맞음 |
| `violation` | 계산했고 어긋남(업무 규칙 위반) |
| `not_applicable` | `when` 조건에 해당하지 않음(정상·미실행 어느 쪽으로도 세지 않음) |
| `not_computable` | 계산 불가 — 사유: `parse_error`, `missing_value`, `missing_reference`, `duplicate_parent`, `ambiguous_join`, `missing_column`, `excluded_target` |
| (규칙 단위) `disabled` | 규칙이 꺼짐 — 실행 계획에 기록, 품질 점수에서 정상 처리 안 함 |

- 부모 키가 중복이면 그 기준 행은 `duplicate_parent`(조인 증폭 방지). 경로가 한 행을 기대하는데 여러 행이 나오면 `ambiguous_join`.
- **값 공급자 3종:** 원문 / 가상(모든 후보 적용) / 승인(현재 적용 계획). 같은 평가기에 공급자만 바꿔 돌린다. 보류는 원문·가상 기준으로 정하고(8-3), 최종 판정은 발행 단계의 실제 결과 재평가다(8-5).
- **위반 묶음:** 규칙 ID + 기준 행과 연결 행의 원본 위치 + 계산값·기대값 + 공급자. 비교 키는 규칙 ID + 원본 위치 정렬 목록.
- **구현 보충(M4-2·M4-3, 2026-11-04):** 범주 조건(`in`·`not_in`)은 논리 설정 컬럼의 비교 정규화(`strip`·`casefold`)를 거쳐 비교한다 — 엔진 허용값 검사와 같은 기준이며, 원문 기준에서 `SUCCEEDED` 같은 표기만으로 위반을 만들어 정상 표준화 후보를 보류하지 않기 위해서다. 금액·수량·시각 값 자체는 공급자 값 그대로 파싱한다. 보류(8-3)는 원문·가상 기준 위반 묶음의 값 계산 셀만 막고, 실행 엔진에도 같은 보류 목록을 넘긴다. 발행 전 재검사의 기준 비교 키는 규칙 ID + 관련 행(뷰 레코드)이며 결과 k번째 행 = 뷰 레코드 k라서 원본 위치 비교와 같다.
- 승인된 10개 규칙의 전체 정의는 **부록 A**. T-10: 문법 검증기 + 규칙별 고의 오류·정상 예외·참조 부족·부모 중복·파싱 불가 fixture.

---

## 10. 결과 버전·재사용·스키마 변경·개인정보·품질

### 10-1. 결과 버전과 복원 (F08)
- `result_version`은 시도 폴더의 결과(정제 CSV·`changes`·`before/after`·`integrity`·`held`·`absent-cells`·`manifest`)와 매핑 버전·실행 계획·뷰·적용 계획·설정 스냅샷을 참조한다.
- 복원: `result_version(kind=restore, restored_from=X)`를 만들고 데이터·근거는 모두 X를 참조한다. `changes`를 새로 만들지 않고, 복원은 `event(subject_type=result_version)`에 남긴다. 현재 규칙으로 다시 계산한 점수를 붙이지 않는다.
- 스키마 비교 기준은 업로드 실행의 결과만 대상으로 하며 `kind=restore`는 제외한다(AC-08-4).
- **구현(M5, `prep/versions.py`):** 복원 행의 `run_id`는 선택한 버전의 실행, `data_hashes`·`manifest_path`는 선택한 버전과 같다. 복원의 복원은 `restored_from`이 선택한 복원 버전을 가리키고 근거는 원래 정제 버전(origin)까지 따라가 조회한다. 출처의 현재 결과 = 그 출처 실행들의 마지막 버전 행. 실행 결과 화면·다운로드(`/runs/{id}/…`)는 그 실행의 정제 버전만 보고, 버전 이력은 `/sources/{id}/versions`·`/versions/{id}`(내려받기 해시 재대조, 복원 `POST /versions/{id}/restore`)로 본다.

### 10-2. 재사용 규칙 (F09)
- **형태 서명:** 숫자→`9`, 한글→`가`, 영문→`a`, 기호·공백 그대로, 같은 문자 연속은 하나로. 서로 다른 값이 같은 서명을 가질 수 있으므로 **엔진이 다시 검증한 유효 후보 안에서만** 미리 선택에 쓴다.
- 표준화 규칙 spec = (논리 컬럼, 변환 ID, 승인 서명 집합) + 승인 당시 설정 해시. 변환 ID·규칙 버전·설정 스냅샷이 바뀌면 예전 서명 승인은 미리 선택에 쓰지 않는다.
- 매핑 규칙 spec = (원래 헤더 → 논리 컬럼). 같은 템플릿에서만.
- 승인 취소는 준비 중인 실행의 미리 선택을 해제하고 해당 실행 승인을 무효로 한다.
- 서명의 효과·오수정·검토 감소 평가는 ④.
- **구현(M6-5, `prep/reuse.py`):** 최종 확인(`POST /runs/{id}/execute`의 `reuse: {groups, mappings}`) 때 실행 승인과 별개로 `approval(kind=reuse)` 한 행과 규칙 행을 기록한다(승인하지 않은 묶음·적용 셀이 없는 묶음·실행 대상이 아닌 파일은 실행 승인 전에 400 `bad_reuse`). 표준화 규칙: rule_key = 묶음 ID(테이블.컬럼.진단 코드), 변환 ID = 진단 코드(`type`·`pattern`이면 `:컬럼 표준화 정책`), 서명 = 허용 셀 원문의 형태 서명 집합(양식별로 나눔). 매핑 규칙: rule_key = `mapping`, spec = 표준 테이블·원래 헤더→논리 컬럼. 상태: effective(키마다 발행된 최신 버전, 취소 안 됨) · superseded · pending(그 실행이 발행 전이거나 실패 — 결정 M6-D1) · revoked.
- **구현(M6-6b, `prep/reuse_step.py`, 결정 M6-D4):** 미리보기 기본 선택(저장된 선택이 없을 때)을 셀의 양식(`run_template`)으로 정한다. 양식에 효력 있는 표준화 규칙이 없으면(이 실행이 승인한 규칙은 제외) 지금처럼 전체 선택. 있으면 셀의 모든 진단 코드에 같은 묶음 ID 규칙이 있고, 규칙의 설정 해시·변환 ID가 지금과 같고, 원문 형태 서명이 승인 서명 집합에 있고, 그 셀의 논리 컬럼에 스키마 변경(F10)이 감지되지 않은 셀만 미리 선택한다(AC-09-2). 미리 선택된 셀이 하나라도 있는 묶음은 승인, 그 묶음의 나머지 셀은 제외 셀로 둔다. 규칙과 맞는 셀이 없는 묶음은 선택 해제로 시작한다. 이렇게 만든 선택도 일반 선택과 같이 서버가 검증한 적용 계획이며 최종 확인 없이는 적용되지 않는다(AC-09-3). 미리보기 묶음에 `reuse`(규칙과 맞는 셀 수), 묶음 셀 목록에 셀별 `reuse`를 준다. 설정이 바뀌어 규칙이 하나도 맞지 않으면 그 양식의 묶음은 모두 선택 해제로 시작한다(예전 승인이 지금 설정을 덮지 않으므로).
- **구현(M6-7, `prep/reuse.py`·`prep/reuse_step.py`, SC12):** `GET /reuse-rules` = 규칙이 있는 비교·재사용 단위(출처·표준 테이블·양식 헤더)별 규칙 버전 목록(상태·승인 실행·취소 사유). `POST /reuse-rules/{id}/revoke {reason}`(사유 1~500자 필수, 없는 규칙 404, 효력 있는 규칙이 아니면 409). 취소한 버전이 그 키의 최신 발행 버전으로 남으므로 **이전 버전이 되살아나지 않는다**(키 전체가 다음 실행부터 후보 아님, AC-09-4). 준비 중인 실행에 바로 반영: 표준화 규칙은 `diagnosed`·`awaiting_approval` 실행의 저장된 적용 계획에서 그 규칙과 맞는 허용 셀(그 양식 항목·같은 묶음 ID·승인 서명)을 제외 셀로 돌려 계획을 다시 기록한다(실행 승인 무효 → 재확인, 규칙 밖 사용자 선택은 유지). 매핑 규칙은 매핑을 확정하지 않은 실행에서 그 규칙으로 올린 제안(테이블 근거 `reuse`)을 처음 제안으로 되돌린다. 확정된 매핑·끝난 실행·과거 결과 버전은 바꾸지 않는다. 응답 `runs` = 반영한 실행, event `reuse_revoked`(양식)·`reuse_revoked_applied`(실행).

### 10-3. 템플릿과 스키마 변경 (F10)
- 템플릿 = (출처, 표준 테이블, `header_signature` — 원래 헤더를 정규화·정렬한 목록의 해시).
- **매핑 전:** 같은 출처에서 원래 헤더가 가장 많이 겹치는 기존 템플릿을 비교 **후보**로 고른다. 겹침이 같거나, 없거나, 후보가 여럿이면 자동 확정하지 않고 사용자에게 고르게 한다(또는 "기준 생성").
- **매핑 후:** 표준 테이블이 정해지면 비교 기준을 다시 확인한다. 헤더가 바뀌어 새 템플릿이 생기면 "기존 규칙 유지 / 이번만 / 새 버전" 선택과 승인 기록을 새 템플릿 ID에 연결한다. 규칙을 새 템플릿으로 자동으로 옮기지 않는다.
- **비교 기준 프로파일:** 템플릿의 업로드 실행이 완료될 때 저장한다. 현재 실행은 자기 비교 기준이 될 수 없다.
- 변화 판단 기준은 ④.
- **구현(M6-2, `prep/templates.py`·`prep/profile.py`):** `header_signature` = 빈 머리글을 뺀 원래 헤더를 QE 3-1로 정규화·정렬한 JSON 목록의 SHA-256. 구조 승인(매핑 버전)마다 실행 대상 항목을 양식에 연결한다(`run_template`, 매핑을 다시 확정하면 새 연결). 기준 프로파일은 결과 발행과 같은 DB 트랜잭션에서 발행 대상 테이블의 항목마다 원문 기준으로 저장하고(발행 제외 테이블·실패 실행·복원 버전은 기준 아님, 결정 M6-D3), 저장 실패 시 발행도 되돌린다. 양식의 기준 = 그 양식 프로파일을 남긴 마지막 정제 결과 버전(`templates.baseline`). 프로파일은 원래 헤더 목록·정규화 헤더 집합·컬럼별 형태 서명 분포·비어 있지 않은 값 수·논리 타입 범위(파싱 불가·시간대 없는 시각은 건수만)·허용값 집합이다.
- **구현(M6-3, `prep/drift.py`, 순수 함수):** `candidates` = 같은 출처 양식을 정규화 헤더 겹침 수로 정렬(겹침 0은 후보 아님, 1위 동률·후보 없음은 자동 확정 안 함). 기준 실행에 같은 양식 항목이 여럿이면 원래 헤더가 가장 많이 같은 프로파일, 동률이면 실행 대상 순서가 앞선 것과 비교한다. 새 표에는 같은 정규화 헤더의 기준 매핑 논리 규칙을 빌려 결측 표현·타입·허용값을 같은 기준으로 잰다. 변화 종류: `raw_headers`(순서만 바뀐 경우 표시) · `column_removed`/`column_added` · `rename_suspected`/`rename_undetermined`(사라진×새 컬럼 쌍마다) · `value_shape`/`shape_undetermined` · `value_range`(새 최소·최대가 기준 범위 **밖**일 때만 알림 — 좁아지는 변화는 알리지 않음, 파싱 불가·시간대 없는 건수 함께) · `category_new`(최대 20개 표시와 전체 건수). 비율 경계(80%·10%)는 분수로 계산해 "같음"을 포함한다.
- **구현(M6-4, `prep/drift_step.py`):** 업로드·재시도 때 항목별로 비교한다. 정규화 헤더 구성이 정확히 같은 양식이 하나면 그 양식(양식 정의상 같은 단위, 겹침 동률보다 우선). 항목 상태 `no_change`·`changed`·`choose`(후보 동률)·`baseline_create`(겹치는 양식 없음 또는 사용자가 기준 생성 선택)·`no_baseline`(양식은 있으나 발행한 기준 없음). `changed`·`choose`가 있으면 확인이 필요하고(실행 상태 사유 `schema_drift`), 확인 전 매핑 확정은 409 `drift_unconfirmed`. 비교 기준은 후보 안에서만 고르고 매핑 확정 뒤에는 바꿀 수 없다(409 `mapping_confirmed`). event: `drift_checked`·`drift_selected`·`drift_confirmed`(항목 요약·결과 해시).
- **구현(M6-6a, `prep/reuse_step.py`·`mapping.apply_reuse`):** 업로드 때 변경 감지가 고른 양식이 파일 자신의 양식(헤더 구성 동일)일 때만 그 양식의 효력 있는 매핑 규칙을 확신 등급 `reuse`로 올린다(변경 감지가 걸린 컬럼 제외, 테이블 분류 근거 `reuse`). 매핑 확정 때 영향(F03-6): `mapping_changed`(같은 양식 규칙과 다름 — 차이 목록) · `header_changed`(자기 양식에 규칙이 없고 같은 출처·표준 테이블의 다른 양식에 효력 규칙이 있음, AC-10-2). 영향이 있으면 `reuse_impact: {파일 ID: this_run|new_version}` 없이는 409 `reuse_impact_needed`(details.impacts). "기존 규칙 유지"는 화면이 규칙대로 되돌려 다시 확정하는 것. `new_version`은 최종 확인 때 매핑 규칙으로 함께 기록(발행 시 효력, M6-D1). 규칙은 새 양식으로 자동 이전하지 않는다.

### 10-4. 개인정보·공유 출력 (F11·F13)
- **결정 키:** (템플릿, 표준 테이블, 컬럼 키). 컬럼 키는 논리 컬럼 또는 `extra:<원래 헤더>`. 같은 원래 헤더가 다른 템플릿·테이블에 있어도 섞이지 않는다.
- **마스킹 함수**(같은 입력 → 같은 출력): `full` → 고정 `***`(결정 D1) / `phone_partial` → 형식이 정확한 `010-1234-5678`일 때만 `010-****-5678`, 아니면 `***` / `drop` → 모든 공유 표면에서 그 컬럼 제거 / `none` → 원문.
- **공유 모델:** 내부 JSON을 그대로 넘기지 않는다. `prep/share.py`가 내부 결과에서 공유에 필요한 필드만 골라 마스킹을 거친 "공유 모델"을 만들고, 공유 CSV·JSON·HTML은 이 모델만 받는다. 포함 대상: 정제 데이터, 추가 컬럼, 변경 전후 값, 남은 문제의 원문, 프로파일 상위 값(`top_values`), 중복 그룹의 다른 값, 정합성 계산값·기대값, 문제 설명 문구, 예시.
- 공유 출력은 (결과 버전, `masking_snapshot`)에 묶여 `shares/<share_id>/`에 새로 만든다.
- **검증:** ① 표면별 구조 기대값 테스트(결정마다 기대 출력 확인) ② 직렬화된 CSV·JSON·HTML을 **파싱·이스케이프 해제한 뒤** 원문 검색(보조). 부분 가림·`none`으로 의도한 노출은 실패로 치지 않는다. 짧은 값은 ①로 확인한다. `drop` 컬럼은 모든 표면에 원문이 없어야 한다.
- HTML은 Jinja2 자동 이스케이프로 업로드 문자열이 실행 가능한 HTML이 되지 않게 한다. 리포트는 외부 파일·네트워크 없이 열린다.

### 10-5. 품질 점수 (F12)
- 입력 = 결과 버전의 `after`·`integrity`·실행 계획(검사 범위)·설정 해시, 출력 = 차원별 {score 또는 `not_measured`, scope, numerator, denominator}. 공식은 ④.
- 캐시 키 = (결과 버전, 설정 해시, 범위, **점수 정의 버전**).

---

## 11. 평가 격리와 Git (F14·F16)

### 11-1. 서비스 경계
1. **허용 경로 목록(`prep/guard.py`):** 서비스가 여는 모든 경로는 실제 경로(심볼릭 링크·`..` 해석 후)가 `workspace/` 또는 업로드 임시 폴더 안이어야 한다. 그 밖은 모두 거부한다(금지 패턴이 아닌 허용 목록 방식).
2. **설정은 스냅샷으로만:** 서비스의 실행 근거는 `workspace/config-snapshots/<hash>/`뿐이다. `schema.json`·`normalization-policy.json`도 스냅샷에 복사돼 들어간다. 스냅샷을 만드는 단계만 프로젝트의 `config/`와 두 파일을 읽으며, 읽을 파일 목록이 코드에 고정돼 있다.
3. **엔진 호출:** 엔진은 받은 경로와 그 폴더 안만 읽고 쓴다. 서비스는 호출 직전에 입력(뷰 폴더)·출력(시도 폴더)을 guard로 확인한다.
4. **import 규칙:** 서비스 패키지는 `evaluation`을 import하지 않는다(AST 테스트).
5. **정적 문자열 검사(보조):** 서비스 코드(guard·스냅샷 생성기 제외)에 `evaluation_private`·`data/clean`·`deliverables` 문자열이 없는지 확인한다. 격리의 증명이 아니라 보조 검사다.

**보장 범위:** 서비스는 허용 경로 밖을 읽지 않는다. 사용자가 정답 파일의 이름을 바꿔 직접 업로드하는 것까지 자동 판별하지는 않는다.

T-11: workspace 안의 정답 폴더 심볼릭 링크, `../` 경로, 업로드 경로 조작, 서비스 코드의 `evaluation` import — 모두 차단·실패.

### 11-2. 채점기
- `evaluation/score.py <결과 폴더> --split development|evaluation` — 서비스 결과와 정답을 대조(집계 단위는 PRD 8절·④).
  - 결과 폴더는 서비스가 쓰는 평가용 내보내기(`prep/export.py`, 형식 `export-v1`: 실행 상태·입력 등록표 초안·자동 제안 동결본과 최종 매핑·역추적 표·진단·적용 계획·시도 산출물·발행 결과와 파일 해시)다. 채점기는 이 폴더와 정답·고정 입력만 읽고 서비스 DB·내부 파일은 보지 않는다(M2-1).
  - 판정 규칙은 `evaluation/judge.py`(`judge-v1`). review 탐지 근거는 ④ 5-3 표를 코드로 옮긴 것이다. 개발셋 일괄 실행은 `make score-dev`(M2-4).
- 음성 테스트(AC-14-2): 정답 변조 사본, review 복원값을 넣은 결과, 입력 폴더에 정답 파일이 섞인 경우.
- 평가셋 실행은 `--split evaluation --confirm-final` + 실행 기록(코드 커밋·설정 해시·조건·재시도 사유).

### 11-3. Git과 백업
- `.gitignore`에 `workspace/`, `evaluation_private/`, `deliverables/evaluation-answers.zip` 추가. GitHub 비공개 저장소. 커밋 작성자는 규민님 계정, Claude 표기·트레일러 없음.
- 평가 정답 백업은 SSD `평가정답-백업/<날짜>/`에 사본과 `SHA256SUMS`(결정 D4). Git·서비스 입력·CI에는 넣지 않는다.

---

## 12. API·프론트엔드·개발 환경

### 12-1. API (FastAPI)

| 메서드·경로 | 기능 |
|---|---|
| `GET/POST /sources` | 출처 목록·생성 |
| `POST /sources/{id}/batches` (multipart, 읽기 조건 포함) | 업로드 → 묶음·실행 생성 |
| `GET /runs/{id}` | 상태·검사 완전성·다음 할 일 |
| `PUT /runs/{id}/targets` | 실행 대상·순서 확정 |
| `GET /runs/{id}/drift` · `POST /runs/{id}/drift/confirm` | 스키마 변경(매핑 전), 비교 기준 선택 |
| `GET /runs/{id}/mapping` · `PUT /runs/{id}/mapping` | 매핑 제안·확정(구조 승인), 재사용 영향 선택 |
| `GET /runs/{id}/plan` | 실행 계획(실행/미실행 규칙, 발행 가능 테이블) |
| `GET /runs/{id}/diagnostics` · `GET /runs/{id}/integrity?basis=raw|virtual|approved` | 진단·정합성 |
| `GET /runs/{id}/preview` · `PUT /runs/{id}/selection` | 미리보기·선택 변경 → 적용 계획 갱신 |
| `POST /runs/{id}/execute` (해시 7종 포함) | 실행 승인 + 실행 + 발행 |
| `POST /runs/{id}/retry` | 새 실행 시도 |
| `GET /results/{id}` · `GET /results/{id}/files/{name}` | 결과·내부용 다운로드(해시 재확인) |
| `POST /results/{id}/restore` | 복원 버전 |
| `GET /results/{id}/pii` · `PUT /results/{id}/masking` · `POST /results/{id}/shares` | 탐지·마스킹 확정·공유 출력 |
| `GET /results/{id}/quality` · `GET /sources/{id}/quality` | 점수·추이 |
| `GET /templates/{id}/reuse-rules` · `POST /reuse-rules/{id}/revoke` | 재사용 규칙·취소 |

- 응답 모델은 pydantic, OpenAPI에서 `openapi-typescript`로 프론트 타입 생성.
- 오류 응답: `{code, message, details, location}`.

**현재 구현 경로 (M3, 2026-11-03).** 웹 미리보기(W1)를 먼저 만들면서 위 설계와 경로 이름이 다르게 자랐다. 기능 대응은 같고, 출처·묶음·결과 단위 경로와 F08~F13 경로는 해당 마일스톤에서 맞춘다.

| 메서드·경로 (`/api` 아래) | 기능 |
|---|---|
| `GET /sets` · `GET /sources` · `GET/POST /runs` | 설정 세트·출처 목록, 실행 목록·업로드(출처 필수) |
| `GET /runs/{id}` | 실행(상태·상태 이유·승인 기록·표준 목록) |
| `GET /runs/{id}/drift` · `POST /drift/select` · `POST /drift/confirm` | 매핑 전 스키마 변경(SC03): 항목별 상태·비교 후보·변경 목록, 비교 기준 선택(`template_id` 또는 `null`=기준 생성), 확인(M6-4) |
| `POST /runs/{id}/propose` · `PUT /runs/{id}/mapping` | 테이블 재제안, 매핑 확정(구조 승인) |
| `POST /runs/{id}/diagnose` · `GET /diagnosis` · `GET /issues` · `GET /duplicates` · `GET /source` | 진단, 항목·중복 그룹, 원본 행 |
| `GET /runs/{id}/preview` · `GET /preview/groups/{묶음}` · `PUT /selection` | 미리보기(묶음별 재사용 규칙과 맞는 셀 수 `reuse`), 묶음 셀 목록(셀별 `reuse`), 선택(묶음·제외 셀) |
| `GET /reuse-rules` · `POST /reuse-rules/{id}/revoke` | 재사용 규칙(SC12): 단위별 규칙 버전 목록, 승인 취소(사유 필수, 준비 중 실행 반영 — 10-2) |
| `POST /runs/{id}/execute` · `POST /retry` | 실행(해시 7종, 선택 `reuse: {groups, mappings}` = 재사용 규칙 승인 M6-5), 실패 실행 재시도 |
| `GET /runs/{id}/result` · `GET /changes` · `GET /remaining` · `GET /files/{name}` · `GET /failure-report` | 결과·변경 내역·남은 문제·내려받기(해시 재대조)·실패 진단 리포트 |

### 12-2. 프론트엔드
- React + TypeScript + Vite, **Mantine**(결정 D2, 정확한 버전·호환성은 구현 시 잠금 파일로 확정), React Router, TanStack Query, TanStack Table(가상 스크롤), Recharts.
- 단계형 화면(SC02→SC08)은 실행 ID에 묶인 진행 표시줄로 이동하고, `GET /runs/{id}`가 알려 주는 "다음 할 일"만 활성화한다(승인 무효화를 화면이 따로 계산하지 않음).

### 12-3. 개발 환경·테스트·CI
- Python 3.12(uv, 결정 D3). 의존성: fastapi, uvicorn, pydantic, openpyxl, jinja2 / 개발: pytest, ruff, httpx. Node LTS, npm. `make dev`·`make test`·`make e2e`.
- 테스트 층: 단위 → 통합(파이프라인·승인 무효화·발행) → 격리(T-11) → E2E(Playwright: S1~S5 + 재고 예제 AC-16-2. M6까지 S1·S2·S3·S5와 SC03·SC04 재사용 영향·SC07 재사용 승인·미리 선택·SC12 승인 취소) → 명령줄 회귀(T-01).
- CI(GitHub Actions): ruff, pytest, 웹 빌드·타입 검사, Playwright headless. CI에는 평가 정답이 없으므로 채점기는 개발셋 fixture로만 테스트한다.

---

## 13. 구현 순서 제안 (⑤ 개발 계획에서 확정)

1. 기반: Git·uv·pyproject·pytest 이전·명령줄 회귀(T-01)·격리 골격 → 2. 엔진 확장 ①(규칙 실행 기록) → 3. 논리 설정(T-02)·실행 계획(T-03)·표준화 뷰·역추적(T-04·05) → 4. 매핑(규칙 기반) → 5. 엔진 확장 ②(정책 변환, T-06·07) → 6. 엔진 확장 ③·적용 계획·발행·SQLite(T-08·09) → 7. 채점기(개발셋) → 8. API·화면 S1 → 9. 정합성(T-10) → 10. 엑셀·통합 → 11. 버전·복원 → 12. 재사용·스키마 변경 → 13. 개인정보·공유 출력 → 14. 품질·리포트 → 15. 재고 예제 웹 → 16. (재확인 후) AI → 17. README·최종 평가.

---

## 14. 결정 기록 (2026-11-02 규민님)

| ID | 질문 | 결정 |
|---|---|---|
| D1 | 전체 가림 표시 | 고정 `***` |
| D2 | UI 라이브러리 | Mantine |
| D3 | Python 버전 | 3.12 + uv (기존 테스트·명령줄 회귀 확인 조건) |
| D4 | 평가 정답 백업·대용량 자료 | SSD `/Volumes/SSD/공모전·프로젝트자료/데이터-전처리-자동화/`에서 관리. 평가 정답 사본은 `평가정답-백업/<날짜>/`(해시 확인), 용량이 큰 자료는 `대용량-데이터/` |

---

## 부록 A. 첫 버전 정합성 규칙 10개 (확정 계약)

상태 값은 `schema.json` 범주를 따른다: `order_status` ∈ {pending_payment, paid, processing, completed, cancelled}, `payment_status` ∈ {pending, succeeded, failed, cancelled}, `shipment_status` ∈ {preparing, shipped, delivered, cancelled}, `return_status` ∈ {requested, approved, rejected, received, closed, cancelled}, `refund_status` ∈ {pending, succeeded, failed}. 근거 번호는 `docs/business-rules.md`의 거래 관계 번호다.

```json
{
  "version": 1,
  "rules": [
    {"id": "R01_line_total", "type": "row_equation", "enabled": true, "anchor": "order_items",
     "left": {"col": "line_total"},
     "right": {"sub": [{"mul": [{"col": "quantity"}, {"col": "unit_price"}]}, {"col": "discount_amount"}]},
     "description": "항목 금액 = 수량 × 단가 − 할인 (규칙 4)"},

    {"id": "R02_order_total", "type": "parent_sum_equation", "enabled": true, "anchor": "orders",
     "left": {"col": "order_total"},
     "right": {"add": [{"sum": {"path": [{"to": "order_items", "on": [["order_id", "order_id"]]}], "col": "line_total"}},
                       {"col": "shipping_fee"}]},
     "empty_child": "violation",
     "description": "주문 금액 = 항목 금액 합계 + 배송비. 주문은 상세를 1개 이상 가짐 (규칙 4)"},

    {"id": "R03_paid_before_shipped", "type": "time_order", "enabled": true, "anchor": "shipments",
     "when": {"all": [{"col": "shipment_status", "in": ["shipped", "delivered"]}, {"present": "shipped_at"}]},
     "earlier": {"max": {"path": [{"to": "payments", "on": [["order_id", "order_id"]],
                                   "where": {"col": "payment_status", "in": ["succeeded"]}}], "col": "paid_at"}},
     "later": {"col": "shipped_at"},
     "empty_child": "not_computable",
     "description": "출고는 성공 결제 이후 (규칙 9). 성공 결제가 없으면 R06이 다룸"},

    {"id": "R04_shipped_before_delivered", "type": "time_order", "enabled": true, "anchor": "shipments",
     "when": {"all": [{"col": "shipment_status", "in": ["delivered"]}, {"present": "shipped_at"}, {"present": "delivered_at"}]},
     "earlier": {"col": "shipped_at"}, "later": {"col": "delivered_at"},
     "description": "배송 완료는 출고 시각 이후. 준비 상태의 빈 시각은 적용 대상 아님 (규칙 9)"},

    {"id": "R05_delivered_before_return", "type": "time_order", "enabled": true, "anchor": "returns",
     "when": {"present": "requested_at"},
     "earlier": {"path": [{"to": "return_items", "on": [["return_id", "return_id"]]},
                          {"to": "shipment_items", "on": [["shipment_item_id", "shipment_item_id"]]},
                          {"to": "shipments", "on": [["shipment_id", "shipment_id"]]}],
                 "col": "delivered_at"},
     "later": {"col": "requested_at"},
     "each": true, "missing_earlier": "violation",
     "empty_child": "not_computable",
     "description": "연결된 모든 출고의 배송 완료 시각 ≤ 반품 접수. 연결 출고에 배송 완료 시각이 없으면 위반 (규칙 11)"},

    {"id": "R06_paid_order_has_one_success_payment", "type": "status_requires", "enabled": true, "anchor": "orders",
     "when": {"col": "order_status", "in": ["paid", "processing", "completed"]},
     "requires": [{"count": {"path": [{"to": "payments", "on": [["order_id", "order_id"]],
                                       "where": {"col": "payment_status", "in": ["succeeded"]}}]},
                   "op": "eq", "value": 1}],
     "description": "결제 완료·처리 중·완료 주문은 성공 결제 정확히 1건. 시도는 여러 번 가능 (규칙 7)"},

    {"id": "R07_pending_order_has_no_success_or_shipment", "type": "status_requires", "enabled": true, "anchor": "orders",
     "when": {"col": "order_status", "in": ["pending_payment"]},
     "requires": [{"count": {"path": [{"to": "payments", "on": [["order_id", "order_id"]],
                                       "where": {"col": "payment_status", "in": ["succeeded"]}}]}, "op": "eq", "value": 0},
                  {"count": {"path": [{"to": "shipments", "on": [["order_id", "order_id"]]}]}, "op": "eq", "value": 0}],
     "description": "결제 대기 주문은 성공 결제·출고 없음. 경과 일수로 판정하지 않음 (규칙 8)"},

    {"id": "R08_shipped_le_ordered", "type": "cumulative_le", "enabled": true, "anchor": "order_items",
     "limit": {"col": "quantity"},
     "total": {"sum": {"path": [{"to": "shipment_items", "on": [["order_item_id", "order_item_id"]]},
                                {"to": "shipments", "on": [["shipment_id", "shipment_id"]],
                                 "where": {"col": "shipment_status", "not_in": ["cancelled"]}}],
                       "col": "shipment_items.quantity"}},
     "empty_child": "ok",
     "description": "취소되지 않은 출고 배정 수량 합계 ≤ 주문 수량 (규칙 10)"},

    {"id": "R09_returned_le_shipped", "type": "cumulative_le", "enabled": true, "anchor": "shipment_items",
     "limit": {"col": "quantity"},
     "total": {"sum": {"path": [{"to": "return_items", "on": [["shipment_item_id", "shipment_item_id"]]},
                                {"to": "returns", "on": [["return_id", "return_id"]],
                                 "where": {"col": "return_status", "not_in": ["rejected", "cancelled"]}}],
                       "col": "return_items.quantity"}},
     "empty_child": "ok",
     "description": "거절·취소를 제외한 반품 누적 수량 ≤ 출고 수량 (규칙 11)"},

    {"id": "R10_refund_le_payment", "type": "cumulative_le", "enabled": true, "anchor": "payments",
     "when": {"col": "payment_status", "in": ["succeeded"]},
     "limit": {"col": "amount"},
     "total": {"sum": {"path": [{"to": "refunds", "on": [["payment_id", "payment_id"]],
                                 "where": {"col": "refund_status", "in": ["succeeded"]}}], "col": "amount"}},
     "empty_child": "ok",
     "description": "성공 환불 합계 ≤ 원결제액. 실패 환불 제외, 반품 없는 출고 전 전체 취소 환불도 포함해 합산 (규칙 14·15)"}
  ]
}
```

**문법 보충(부록 A를 표현하려고 9-1에 더한 것):**
- 경로 마지막 단계보다 앞선 테이블의 컬럼을 집계할 때는 `"col": "테이블.컬럼"`으로 쓴다(R08·R09: 수량은 중간 테이블, 상태 필터는 마지막 테이블).
- `time_order`의 `each: true`는 연결 행마다 비교하고 하나라도 어긋나면 위반이다. `missing_earlier`는 연결 행의 비교 값이 비어 있을 때의 판정이다(R05).
- 경로 중간 단계의 `where`는 그 단계에서 걸러진 연결만 남긴다.

**표현 가능성 확인:** 10개 규칙 모두 9-1의 5개 종류 + 위 보충으로 표현된다. 결제 시도 여러 건(R06 `count` + `where`), 취소 출고 제외(R08), 거절·취소 반품 제외(R09), 실패 환불 제외(R10), 준비 상태 빈 시각(R04 `when`), 다단계·여러 출고(R05 `path` + `each`), 결제 대기 0건 조건(R07)을 포함한다. 구현 시 T-10에서 이 JSON을 그대로 검증기에 넣어 통과해야 한다.
