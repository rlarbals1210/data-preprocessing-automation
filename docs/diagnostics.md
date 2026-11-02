# 공통 CSV 진단 엔진 v1.1

Python 표준 라이브러리만 사용한다. AI·MySQL 연결 없이 CSV 파일 하나 또는 한 폴더의 CSV를 검사한다.
쇼핑몰 컬럼명이나 고객번호 규칙은 엔진에 들어 있지 않으며 데이터별 JSON 설정으로 지정한다.

## 실행

프로젝트 루트에서 기본 프로파일링:

```sh
uv run python scripts/diagnose.py data/dirty/development
```

쇼핑몰 기준으로 추가 검사:

```sh
uv run python scripts/diagnose.py data/dirty/development --config config/shop-diagnostics.json
```

다른 도메인 재사용 예제:

```sh
uv run python scripts/diagnose.py examples/inventory/input --config examples/inventory/rules.json
```

다른 데이터에는 입력 경로와 JSON 설정을 바꾼다. `--encoding cp949`, `--delimiter ';'`처럼 읽기 설정도 지정할 수 있다.
DB에 적재할 필요가 없다. CSV–MySQL 대조를 통과한 쇼핑몰 CSV는 현재 적재 데이터와 같은 값의 입력이다.

## 설정 없이 검사하는 내용

빈 문자열·공백만 있는 값, 앞뒤 공백, 결측 표현 후보, 컬럼별 고유값·상위 빈도·최대 길이·표현 형태,
완전히 동일한 행의 중복을 분석한다. `0`을 기본 결측 후보로 지정하지 않는다.
결측 표현 후보는 `null`, `none`, `n/a`, `na`, `없음`, `미입력`이며 관측 정보(info)일 뿐이다.
후보 목록은 `missing_candidates`로 교체할 수 있다. 자료형처럼 보이는 문자열 집계는 의미나 오류를 확정하지 않는다.

## 설정 예시

```json
{
  "version": 1,
  "tables": {
    "contacts": {
      "columns": {
        "id": {"required": true},
        "phone": {"missing_values": ["NULL", "없음"], "pattern": "010-[0-9]{4}-[0-9]{4}"},
        "discount": {"type": "integer", "min": 0},
        "state": {"allowed": ["open", "closed"]},
        "closed_on": {"type": "date", "required_if": {"column": "state", "in": ["closed"]}}
      },
      "unique": [["id"]]
    }
  }
}
```

`tables`의 키는 확장자를 제외한 CSV 파일명, `columns`의 키는 CSV 헤더와 정확히 일치해야 한다.
설정하지 않은 테이블·컬럼은 기본 프로파일링만 한다. 컬럼명 추론·자동 매핑은 v1 범위가 아니다.

| 설정 | 의미 |
|---|---|
| required | 해당 컬럼이 비어 있으면 규칙 위반 |
| missing_values | 이 컬럼에서 결측으로 인정한 문자열 목록. 전역 후보와 별개 |
| strip / casefold | 비교할 때만 앞뒤 공백/대소문자 처리. 기본 false, 원문은 유지 |
| type | string, integer, decimal, date, datetime |
| min / max | 정수·소수형에 적용하는 포함 경계 |
| allowed | 허용하는 문자열 목록 |
| pattern | Python 정규식 전체 일치 검사 |
| formats | 날짜의 허용 strptime 형식 목록 |
| required_if | 다른 설정 컬럼의 값이 in 목록에 있을 때 필수 |
| unique | 유일해야 하는 단일/복합 컬럼 목록의 목록 |
| references | columns, table, target_columns로 지정하는 단일/복합 참조 |

정수·소수는 쉼표/통화기호 없는 명시적 표기만 파싱한다. 날짜 기본 형식은 `%Y-%m-%d`,
시각은 `%Y-%m-%dT%H:%M:%S%z`다. 다른 형식은 설정으로 추가한다.
빈 문자열과 공백만 있는 값은 빈 값으로 관측하며, 오류 여부는 required로 결정한다.
결측 후보 문자열은 missing_values에 별도로 지정하지 않는 한 값으로 검사된다.
유일성은 결측을 제외하고 비교한다. 참조 검사도 복합키 일부가 결측이면 건너뛰며 필수값 규칙이 담당한다.
부모 키의 중복 검사는 부모 unique 설정이 담당한다. 부모와 자식의 비교 옵션은 각 컬럼 설정을 따른다.

## 결과 해석

매번 새로운 `reports/diagnostics/<실행ID>/`에 `summary.md`와 `report.json`을 만든다.
`--output`으로 새 폴더를 지정할 수도 있다. 기존 폴더나 입력 폴더 아래로의 출력은 거부한다.
보고서는 원문 값을 포함하는 로컬 검토 자료이며 해당 기본 출력 폴더는 Git에서 제외한다.

- info: 빈 값·결측 표현 후보·공백·설정된 비교 정규화 관측. 정상일 수 있음.
- warning: 중복 행, 형식 차이, 파싱 실패, 참조 연결 실패. 검토 대상.
- error: 설정된 필수·유일·허용값·범위 규칙 위반.
- completed: 가능한 검사가 실행 완료됐다는 뜻. 데이터 정상 판정이 아님.
- incomplete: 잘못된 CSV, 누락 파일/컬럼, 참조 검사 불가 등으로 검사 미완료.

종료 코드 0은 완료+info만 존재, 1은 완료+warning/error 존재, 2는 실행/설정/구조 문제다.
한 셀에 여러 진단이 있을 수 있어 진단 건수는 오류 셀 수·오류율과 다르다.
JSON은 파일 SHA-256, 실행 설정, 테이블 프로파일, 각 항목의 원문·컬럼·레코드 번호·물리 줄 범위·근거를 포함한다.
레코드 번호는 헤더 다음을 1로 센다. 여러 줄 필드는 물리 줄 범위와 구분한다.
참조 대상이 없어 검사할 수 없으면 건너뛴 이유를 별도로 기록한다. 근거 없이 모든 자식 행을 참조 오류로 처리하지 않는다.

`rule_execution`은 규칙별 실행 기록이다. 항목마다 `table`·`kind`·`columns`(참조는 `target` 추가)·`executed`·`reason`을 가진다. 문제 0건인 규칙도 실행됐으면 `executed: true`로 남으므로, 실행 여부는 문제 코드별 건수인 `summary.rule_counts`가 아니라 이 목록으로 확인한다.

- `kind`: `profile`(설정 없이 하는 기본 관측) · 컬럼 규칙 `required`·`required_if`·`missing_values`·`comparison_normalization`(strip/casefold)·`allowed`·`pattern`·`type`(string 외)·`min`·`max`·`standardization` · 테이블 규칙 `unique`·`reference`.
- `reason`(실행 안 됨): `read_error` 파일 읽기 실패 · `table_unavailable` 설정한 테이블 입력 없음 · `columns_unavailable` 설정 컬럼 일부 없음(그 테이블 규칙 전체 제외) · `reference_target_unavailable` 참조 대상 테이블을 검사할 수 없음.

## 범위와 한계

v1은 메모리에 CSV 전체를 읽는 소규모 데이터용이다. XLSX·비정형 문서·MySQL 직접 입력·스트리밍은 후속이다.
통계적 이상치, 테이블 간 금액 합계·시간 선후관계, 자유 수식, 자동 정제는 아직 구현하지 않았다.
쇼핑몰 설정은 현재 스키마·업무 규칙과 개발용 헤더에서 작성한 명시적 기준이며 평가용 정답은 읽지 않는다.
정규식/규칙 설정은 신뢰하는 사용자가 작성한다. 제공받은 임의 정규식은 실행시간 보장이 없으며 서비스 공개 전 제한이 필요하다.
결측/중복 후보를 자동 삭제하거나 복원하지 않으며 검사 결과를 정제 성능으로 해석하지 않는다.

## 테스트

```sh
uv run pytest tests/test_diagnostics.py -v
```

정상 0, 컬럼별 결측, 조건부 필수값, 복합키·참조, 원문/줄 위치 보존, 파싱·설정 오류 등을 검사한다.
포트폴리오 평가에서는 별도 입력과 탐지 정답 기준을 준비해야 하며 v1 실행 성공 자체는 범용 정확도의 증명이 아니다.

## 네 가지 결과 분류 (v1.1)

summary.md와 터미널에는 다음 네 분류의 이벤트 수를 표시한다. JSON의 기존 code/severity/reason/원문은 유지하며 category, category_reason, 가능한 경우 suggested_value를 추가했다. 원본 CSV를 수정하거나 정제 파일을 만들지는 않는다.

| category | 표시 | 기준 |
|---|---|---|
| normal_observation | 정상 관측 | 필수 조건을 위반하지 않는 빈 값. 미설정 컬럼은 정상 확정이 아니라 단순 관측이라고 설명 |
| standardization_candidate | 표준화 후보 | 컬럼 정책이 허용하고 변환 후 타입·정규식·허용값·범위를 통과한 표현, 허용된 결측 표현 |
| business_rule_violation | 업무 규칙 위반 | 필수·범위·허용값·유일성·참조 규칙 위반. 같은 셀의 빈 값 관측도 위반으로 분류 |
| human_review | 사람 검토 필요 | 정책 없는 표현, 해결되지 않은 형식/타입, 동일 행 중복의 삭제 여부 |

분류는 후속 처리 목적이고 severity와 다르다. 기존 warning이 표준화 후보가 되어도 원래 엄격한 형식 검사의 severity를 보존한다. 종료 코드도 이전 계약을 유지한다. 한 이벤트에는 분류 하나가 붙지만 한 셀·행에는 여러 이벤트가 가능하다. 참조 불일치는 현재 설정의 비교 규칙 기준이며 원값 복원을 확정하지 않는다.

컬럼별 standardization은 선택 항목이다:

- krw_integer: type=integer에서 엄격한 천 단위 쉼표·선택적 원 접미사 제거. 잘못된 쉼표 배치나 임의 통화는 추정하지 않는다.
- kr_mobile: type=string에서 공백·괄호·하이픈 제거 후 010+8자리인 경우만 하이픈형 제안. 잘린 번호나 국가번호는 추정하지 않는다.
- iso_datetime: type=datetime에서 연월일 순서와 시간대가 명확한 두 형식만 ISO 제안: `%Y-%m-%d %H:%M:%S%z`, `%Y/%m/%d %H:%M:%S%z`.
- strip/casefold가 true인 컬럼은 설정된 비교 표현을 제안할 수 있다. 대소문자만 다른 값도 comparison_normalization 이벤트로 표시한다. 이전 보고서보다 이벤트 수가 늘어날 수 있다.

예: `{"type":"integer","min":0,"standardization":"krw_integer"}`. 공통 코드에는 쇼핑몰 컬럼명이 없고, 사용할 정책은 데이터별 설정으로 선택한다. 표준화 제안은 컬럼 검사까지 확인하며 변환 후 테이블 간 관계·중복을 다시 검증한 결과는 아니다. 표기 변환이 가능해도 범위 등 다른 제약을 위반하면 후보로 제안하지 않고 검토 대상으로 남긴다.

duplicate_groups는 각 검사의 그룹 ID, 참여 레코드, 동일/충돌 구분, 차이 컬럼을 포함한다. 동일 행과 키 중복 그룹은 겹칠 수 있으므로 합산해 독립 사고 건수로 표현하지 않는다. 업무 규칙 위반 그룹의 수정 방향 역시 사람 확인이 필요할 수 있다.

분류 회귀 테스트:

```sh
uv run pytest tests/test_classification.py -v
```
