# CSV와 MySQL 원문 값 비교

개발용 오염 CSV 13개와 `shop_raw`를 비교한다. DB와 CSV를 변경하지 않는다.
Python 3.9 이상과 MySQL 8 이상을 기준으로 한다.

## 실행

프로젝트 폴더의 터미널에서 최초 한 번 환경을 준비한다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-mysql.txt
```

이후 검증할 때 실행한다. 비밀번호는 프롬프트에서 입력하며 화면·인자·보고서에 저장하지 않는다.

```sh
.venv/bin/python scripts/compare_mysql_csv.py --database shop_raw --user root
```

기본 연결은 `127.0.0.1:3306`. 다르면 `--host`, `--port`, `--user`로 DBeaver와 같은 서버·계정을 지정한다.
보고서 경로는 `--report`로, 입력 폴더는 `--csv-dir`로 바꿀 수 있다.

## 판정

- `match`: 컬럼명과 전체 행의 값·중복 개수 일치.
- `different`: 값 또는 행 개수 불일치. 종료 코드 1.
- `error`: 파일·테이블·컬럼 누락, 숫자형 컬럼, 연결/조회 실패 등. 종료 코드 2.
- 전부 일치하면 종료 코드 0. 보고서 `reports/mysql-csv-comparison.json`의 실행 시각도 확인한다.

기본 입력 목록은 `schema.json`의 13개 테이블이다. CSV 헤더를 실제 DB 컬럼에 대응하므로 DB 컬럼 순서는 달라도 된다.
UTF-8 CSV를 문자열로 읽고 MySQL 문자열을 UTF-8 바이너리로 조회한다. 앞자리 0, 앞뒤 공백, 대소문자, Unicode 구성 차이,
빈 문자열과 SQL NULL을 구분한다. BOM은 파일 인코딩 표식으로 처리한다. 숫자·날짜·결측 자동 변환은 하지 않는다.
행 순서는 무시하되 동일한 행의 개수는 보존한다. InnoDB 읽기 전용 일관 스냅샷으로 13개 테이블을 검사하며 실행 중 DDL 변경은 피한다.

## 불일치 보고서

각 테이블에 `csv_only`와 `db_only`를 저장한다. `count`는 해당 행의 초과 개수이며,
`csv_record_numbers`는 헤더 다음을 1로 센 원본 레코드 번호 목록이다(여러 줄 필드가 있어 물리적 줄 번호와 다를 수 있다).
각 셀의 `value`와 `utf8_hex`로 보이지 않는 공백도 확인할 수 있다. SQL NULL은 JSON null, 빈 문자열은 `""`다.

오염 ID는 중복·누락될 수 있으므로 두 불일치 행을 임의로 같은 행이라고 짝짓지 않는다.
따라서 보고서는 사라진/추가된 행의 전체 셀을 보여주며, 변경된 컬럼 하나를 확정하려면 두 행의 대응 근거를 추가로 확인해야 한다.
일치 판정은 CSV 파싱 후 셀 값 보존을 뜻한다. CSV 따옴표·파일 줄바꿈 같은 직렬화 방식이나 업무적 정확성을 검증하는 것은 아니다.
원본 CSV SHA-256도 기록하지만 생성 시점 manifest와의 일치까지 보장하는 기능은 아니다.
보고서는 원본 불일치 값을 포함하므로 로컬 검토용으로 사용하며 기본 경로는 Git에서 제외한다.

## 로컬 테스트

```sh
python3 scripts/test_compare_mysql_csv.py
```

비밀번호 없이 비교 로직·입력 오류를 검사한다. 실제 DB와의 일치 여부는 위 연결 명령을 실행해야 확인된다.

연결 구현 참고: https://pymysql.readthedocs.io/en/latest/modules/connections.html
