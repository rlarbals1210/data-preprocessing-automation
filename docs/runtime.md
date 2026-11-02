# 생성·검증 실행 환경

CSV 자체는 별도 패키지 없이 사용할 수 있다. 검증기는 Python 표준 라이브러리만 사용한다. 생성기는 Node.js 24.19.0과 이 Mac의 Codex 번들 `@oai/artifact-tool`을 사용했다. 공개 npm 설치 가능성을 확인한 패키지는 아니므로 다른 컴퓨터에서는 같은 번들 환경을 준비해야 한다.

## 이 Mac에서 실행

프로젝트 루트에서 실행한다. `scripts/node_modules`는 아래 번들 모듈 폴더로 연결한 로컬 심볼릭 링크이며 Git에는 포함하지 않는다.

```sh
/Users/gimgyumin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node scripts/build.mjs .
python3 scripts/validate.py .
python3 scripts/test_validation.py .
```

번들 모듈 위치: `/Users/gimgyumin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules`.

`SKIP_PREVIEWS=1`을 앞에 붙이면 미리보기 렌더를 생략한다. `PREVIEW_DIR`로 PNG 출력 폴더를 지정할 수 있다. 생성 결과 CSV·스키마·사전·manifest는 출력 경로에 덮어쓴다. 업무 규칙·계획 문서는 수동 관리하며 생성기가 덮어쓰지 않는다.

## 내보내기 검증

Artifact Tool에 셀을 기록하고 다시 읽은 값을 원래 배열과 전부 비교한 다음 CSV로 직렬화한다. 번들이 ISO 시각 문자열을 Date로 읽는 경우 명시적으로 +09:00 ISO 문자열로 변환한다. ID·전화·우편번호·바코드는 원문 문자열을 보존한다. 내보낸 CSV는 별도 Python 검증기로 관계·상태·금액·수량·시각을 재검사한다.

동일 seed의 두 독립 출력에서 13개 CSV의 SHA-256 일치를 비교한다. 검증기 변이 테스트는 금액·수량·참조·표기·상태 오류를 일부러 넣어 해당 업무 규칙의 탐지를 확인한다. 합성 데이터의 규칙 검증이며 실제 서비스의 정확도 평가가 아니다.
