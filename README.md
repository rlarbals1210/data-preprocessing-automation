# Data preprocessing automation

여러 사람이 서로 다른 방식으로 작성한 쇼핑몰 CSV를 표준화하는 프로젝트.

## 개발 환경

Python 3.12와 [uv](https://docs.astral.sh/uv/)를 쓴다. 프로젝트 폴더에서:

```sh
uv sync                     # .venv 생성(Git 제외), uv.lock 기준
uv run pytest tests         # 테스트 — 경로를 꼭 tests로 지정
uv run ruff check .         # 린트
```

`scripts/test_*.py`·`scripts/validate_dirty.py`는 평가 자료를 읽는 데이터셋 구축 검증 도구라 개발 중 실행하지 않는다([개발 계획](docs/development-plan.md) 3·8절).

## 데이터 사용

- `data/clean/`: 가상 쇼핑몰 정상 기준 CSV 13개. 실제 고객 데이터가 아니다.
- `schema.json`: 필드 타입·null·PK/FK·범주 정의 정본.
- [데이터 사전](docs/data-dictionary.md): 스키마에서 생성한 한글 설명.
- [ERD](docs/erd.md): 테이블 관계도.
- [업무 규칙](docs/business-rules.md): 금액·수량·상태·시간 관계와 정상 예외.
- [전체 계획](docs/project-plan.md): 승인된 단계별 계획·완료 기준.
- `data/manifest.json`: 생성 조건, 행 수, 파일 SHA-256.
- `reports/validation.json`: 내보낸 CSV의 독립 검증 결과.
- `reports/validator-mutation-tests.json`: 고의 오류 9종 탐지 확인.
- `reports/reproducibility.json`: 같은 seed로 생성한 CSV 해시 비교.

CSV를 엑셀에서 더블클릭하면 전화·우편번호의 앞자리 0이 사라질 수 있다. 가져오기에서 해당 열을 텍스트로 지정하거나 CSV를 문자열로 읽는다. null은 빈 필드이며 문자열 NULL과 다르다.

## 오염 입력과 평가 정답

- `data/dirty/development/`, `data/dirty/evaluation/`: 각각 13개 서비스 입력 CSV.
- `evaluation_private/`: 평가 정답·원본 행 연결. 서비스 입력과 분리한다.
- [벤치마크 사용법](docs/benchmark.md) · [실제 정답 예시](docs/benchmark-examples.md).
- `deliverables/service-inputs.zip`: 입력 CSV·목표 스키마·정규화 정책 묶음.
- `deliverables/evaluation-answers.zip`: 채점용 정답과 검증 근거 묶음.

## 재현

> **실행 제한:** 현재 개발 단계의 실행·접근 규칙은 [개발 계획](docs/development-plan.md) 8절을 따른다. 아래 재현 명령은 데이터셋 구축 이력이며 개발용 테스트 명령이 아니다. 정상 기준·평가 자료의 재생성이나 관련 검증 도구 실행은 별도 승인 없이 진행하지 않는다.

Node.js와 `@oai/artifact-tool`이 필요하다. 사용한 환경은 `docs/runtime.md`에 기록한다. 검증은 Python 표준 라이브러리만 사용한다.

```sh
node scripts/build.mjs .
python3 scripts/validate.py .
python3 scripts/test_validation.py .
```

생성기는 같은 seed로 기준 CSV를 덮어쓰므로 수동 편집본은 별도 보관한다. CSV 내용 변경은 `data/manifest.json` 해시로 탐지한다. 출력에는 작업 경로·현재 시각 대신 고정된 생성 조건을 기록한다.

기준 데이터는 정제기의 평가 대상이 될 정상 원본이다. 데이터 검증 스크립트는 정제 서비스 구현이 아니다. 서비스 구현 단계와 현재 상태는 AI-brain `GOALS.md`에 기록한다.

## MySQL 적재 원문 검증

개발용 CSV와 `shop_raw`의 전체 값을 비교하는 [검증 스크립트 사용법](docs/mysql-csv-verification.md).
행 순서는 무시하며 공백·빈 문자열·SQL NULL·중복 개수는 구분한다. DB에는 쓰지 않는다.

## 공통 진단 엔진

[진단 엔진 사용법](docs/diagnostics.md): 설정 없는 CSV 프로파일링과 JSON 규칙 기반 검사를 지원한다. 결과는 정상 관측·표준화 후보·업무 규칙 위반·사람 검토 필요로 나누며 중복 그룹과 변경 제안을 표시한다. 원본은 변경하지 않는다.
쇼핑몰 설정과 창고 재고 예제를 제공하며 원본 값은 수정하지 않는다.

## 원본 보존 표준화

[정제 실행·변경 내역 안내](docs/cleaning.md): `scripts/clean.py`로 표준화 후보를 새 CSV에 적용한다. 정제 전후 진단과 셀별 변경 기록을 함께 저장한다.
