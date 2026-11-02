# 개발용 정답 예시

개발용 정답에서 유형당 1개를 추린 예시다. 빈 문자열은 `빈칸`으로 표시한다. 전체 판정 규칙은 [benchmark.md](benchmark.md)를 따른다.

| 파일 / 레코드 / 컬럼 | 입력값 | 기대 행동 | 기대값 |
|---|---|---|---|
| customers.csv / 0 / 고객번호 | `고객번호` | map_column | `customer_id` |
| customers.csv / 1 / 고객명 | `빈칸` | review | `원문 유지·검토 요청` |
| customers.csv / 1 / 휴대폰 | `010-1063` | review | `원문 유지·검토 요청` |
| customers.csv / 1 / 회원상태 | `ACTIVE` | normalize | `active` |
| customers.csv / 2 / 휴대폰 | `0` | review | `원문 유지·검토 요청` |
| customers.csv / 4 / 고객명 | ` 가상고객095 ` | normalize | `가상고객095` |
| customers.csv / 4 / 휴대폰 | `빈칸` | keep | `빈칸` |
| customers.csv / 6 / 가입일시 | `2026/06/01 09:00:00+09:00` | normalize | `2026-06-01T09:00:00+09:00` |
| customers.csv / 13 / 휴대폰 | `01012884896` | normalize | `010-1288-4896` |
| customers.csv / 76 / 이메일 | `NULL` | normalize | `빈칸` |
| categories.csv / 4 / 상위분류번호 | `빈칸` | keep | `빈칸` |
| product_variants.csv / 1 / 판매가 | `35,000` | normalize | `35000` |
| orders.csv / 1 / 주문일시 | `07/08/26 09:30` | review | `원문 유지·검토 요청` |
| orders.csv / 1 / 주문총액 | `486200` | review | `원문 유지·검토 요청` |
| order_items.csv / 1 / 옵션코드 | `SKU999999` | review | `원문 유지·검토 요청` |
| order_items.csv / 1 / 수량 | `0` | review | `원문 유지·검토 요청` |
| order_items.csv / 1 / 할인액 | `0` | keep | `0` |
| order_items.csv / 12 / 단가 | `29000` | keep | `29000` |
| order_items.csv / 411 / 수량 | `60` | keep | `60` |
| shipments.csv / 56 / 송장번호 | `빈칸` | keep | `빈칸` |
