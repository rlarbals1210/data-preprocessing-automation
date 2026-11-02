# 모아마켓 데이터 사전

생성 데이터이며 실제 고객·상거래 기록이 아닙니다. 정의의 정본은 `schema.json`이며 이 문서는 생성기로 파생합니다.

문자열 ID/전화/우편번호를 숫자로 읽지 마세요. CSV는 UTF-8(BOM 없음), 쉼표 구분, LF 줄바꿈, 빈 필드는 null입니다. 시각은 ISO 8601 +09:00, 금액은 원 단위 정수입니다.

## customers

회원 1명

| 컬럼 | 타입 | null 허용 | 키·제약 | 의미 |
|---|---|---|---|---|
| customer_id | string | 아니오 | PK | 고객 식별자 |
| customer_name | string | 아니오 |  | 가상 고객명 |
| email | string | 예 |  | 이메일 |
| phone | string | 예 |  | 고객 연락처 |
| customer_status | string | 아니오 | active / inactive / withdrawn | 고객 상태 |
| marketing_consent | string | 아니오 | true / false / unknown | 마케팅 동의·거부·미확인 |
| joined_at | datetime | 아니오 |  | 가입 시각 |
| updated_at | datetime | 아니오 |  | 마지막 수정 시각 |

## customer_addresses

고객 저장 배송지 1개

| 컬럼 | 타입 | null 허용 | 키·제약 | 의미 |
|---|---|---|---|---|
| address_id | string | 아니오 | PK | 저장 배송지 식별자 |
| customer_id | string | 아니오 | FK → customers.customer_id | 고객 식별자 |
| address_label | string | 아니오 |  | 배송지 별칭 |
| recipient_name | string | 아니오 |  | 수령인 |
| recipient_phone | string | 아니오 |  | 수령인 연락처 |
| postal_code | string | 아니오 |  | 우편번호 |
| address_line1 | string | 아니오 |  | 기본 주소 |
| address_line2 | string | 예 |  | 상세 주소 |
| is_default | string | 아니오 | true / false | 기본 배송지 여부 |
| created_at | datetime | 아니오 |  | 생성 시각 |
| updated_at | datetime | 아니오 |  | 마지막 수정 시각 |

## categories

카테고리 1개

| 컬럼 | 타입 | null 허용 | 키·제약 | 의미 |
|---|---|---|---|---|
| category_id | string | 아니오 | PK | 카테고리 식별자 |
| parent_category_id | string | 예 | FK → categories.category_id | 상위 카테고리 |
| category_name | string | 아니오 |  | 카테고리명 |
| is_active | string | 아니오 | true / false | 활성 여부 |

## products

상품 1개

| 컬럼 | 타입 | null 허용 | 키·제약 | 의미 |
|---|---|---|---|---|
| product_id | string | 아니오 | PK | 상품 식별자 |
| category_id | string | 아니오 | FK → categories.category_id | 카테고리 식별자 |
| product_name | string | 아니오 |  | 상품명 |
| brand_name | string | 아니오 |  | 브랜드명 |
| description | string | 예 |  | 상품 설명 |
| product_status | string | 아니오 | draft / active / discontinued | 상품 상태 |
| created_at | datetime | 아니오 |  | 생성 시각 |
| updated_at | datetime | 아니오 |  | 마지막 수정 시각 |

## product_variants

판매 옵션 SKU 1개

| 컬럼 | 타입 | null 허용 | 키·제약 | 의미 |
|---|---|---|---|---|
| sku_id | string | 아니오 | PK | 판매 옵션 식별자 |
| product_id | string | 아니오 | FK → products.product_id | 상품 식별자 |
| option_name | string | 아니오 |  | 옵션 이름 |
| barcode | string | 예 |  | 가상 바코드 식별 문자열; GS1 체크섬 검증 대상 아님 |
| current_price | integer | 아니오 |  | 스냅샷 시점 판매가격 KRW |
| weight_g | integer | 예 |  | 그램 단위 무게 |
| variant_status | string | 아니오 | active / out_of_stock / discontinued | 옵션 상태 |
| created_at | datetime | 아니오 |  | 생성 시각 |
| updated_at | datetime | 아니오 |  | 마지막 수정 시각 |

## orders

주문 1건

| 컬럼 | 타입 | null 허용 | 키·제약 | 의미 |
|---|---|---|---|---|
| order_id | string | 아니오 | PK | 주문 식별자 |
| customer_id | string | 아니오 | FK → customers.customer_id | 고객 식별자 |
| ordered_at | datetime | 아니오 |  | 주문 시각 |
| order_channel | string | 아니오 | web / mobile_app / manual | 주문 경로 |
| order_status | string | 아니오 | pending_payment / paid / processing / completed / cancelled | 주문 처리 상태; 반품 상태와 별개 |
| recipient_name | string | 아니오 |  | 수령인 |
| recipient_phone | string | 아니오 |  | 수령인 연락처 |
| postal_code | string | 아니오 |  | 우편번호 |
| address_line1 | string | 아니오 |  | 기본 주소 |
| address_line2 | string | 예 |  | 상세 주소 |
| delivery_memo | string | 예 |  | 배송 요청사항 |
| shipping_fee | integer | 아니오 |  | 최초 주문 배송비 KRW |
| order_total | integer | 아니오 |  | 최초 주문금액 KRW |
| cancelled_at | datetime | 예 |  | 전체 취소 시각 |
| updated_at | datetime | 아니오 |  | 마지막 수정 시각 |

## order_items

주문 상품 항목 1개

| 컬럼 | 타입 | null 허용 | 키·제약 | 의미 |
|---|---|---|---|---|
| order_item_id | string | 아니오 | PK | 주문 항목 식별자 |
| order_id | string | 아니오 | FK → orders.order_id | 주문 식별자 |
| sku_id | string | 아니오 | FK → product_variants.sku_id | 판매 옵션 식별자 |
| product_name_snapshot | string | 아니오 |  | 주문 당시 상품명 |
| option_name_snapshot | string | 아니오 |  | 주문 당시 옵션명 |
| quantity | integer | 아니오 |  | 해당 테이블의 주문·출고·반품 수량 |
| unit_price | integer | 아니오 |  | 주문 당시 개당 가격 KRW |
| discount_amount | integer | 아니오 |  | 주문 항목 전체 할인액 KRW |
| line_total | integer | 아니오 |  | 할인 후 항목 금액 KRW |

## payments

결제 시도 1회

| 컬럼 | 타입 | null 허용 | 키·제약 | 의미 |
|---|---|---|---|---|
| payment_id | string | 아니오 | PK | 결제 시도 식별자 |
| order_id | string | 아니오 | FK → orders.order_id | 주문 식별자 |
| payment_method | string | 아니오 | card / bank_transfer / easy_pay | 결제 수단 |
| payment_status | string | 아니오 | pending / succeeded / failed / cancelled | 결제 시도 상태 |
| amount | integer | 아니오 |  | 해당 결제·환불 시도 금액 KRW |
| requested_at | datetime | 아니오 |  | 요청 시각 |
| paid_at | datetime | 예 |  | 결제 성공 시각 |
| failure_code | string | 예 |  | 실패 코드 |

## shipments

출고 묶음 1개

| 컬럼 | 타입 | null 허용 | 키·제약 | 의미 |
|---|---|---|---|---|
| shipment_id | string | 아니오 | PK | 출고 묶음 식별자 |
| order_id | string | 아니오 | FK → orders.order_id | 주문 식별자 |
| carrier_code | string | 예 |  | 가상 택배사 코드 |
| tracking_number | string | 예 |  | 가상 운송장 |
| shipment_status | string | 아니오 | preparing / shipped / delivered / cancelled | 출고·배송 상태 |
| created_at | datetime | 아니오 |  | 생성 시각 |
| shipped_at | datetime | 예 |  | 출고 시각 |
| delivered_at | datetime | 예 |  | 배송 완료 시각 |

## shipment_items

출고 배정 항목 1개

| 컬럼 | 타입 | null 허용 | 키·제약 | 의미 |
|---|---|---|---|---|
| shipment_item_id | string | 아니오 | PK | 출고 항목 식별자 |
| shipment_id | string | 아니오 | FK → shipments.shipment_id | 출고 묶음 식별자 |
| order_item_id | string | 아니오 | FK → order_items.order_item_id | 주문 항목 식별자 |
| quantity | integer | 아니오 |  | 해당 테이블의 주문·출고·반품 수량 |

## returns

반품 접수 1건

| 컬럼 | 타입 | null 허용 | 키·제약 | 의미 |
|---|---|---|---|---|
| return_id | string | 아니오 | PK | 반품 접수 식별자 |
| order_id | string | 아니오 | FK → orders.order_id | 주문 식별자 |
| return_status | string | 아니오 | requested / approved / rejected / received / closed / cancelled | 반품 처리 상태 |
| requested_at | datetime | 아니오 |  | 요청 시각 |
| received_at | datetime | 예 |  | 반품 입고 시각 |
| closed_at | datetime | 예 |  | 반품 종료 시각; rejected/cancelled 포함 |

## return_items

반품 대상 출고 항목 1개

| 컬럼 | 타입 | null 허용 | 키·제약 | 의미 |
|---|---|---|---|---|
| return_item_id | string | 아니오 | PK | 반품 항목 식별자 |
| return_id | string | 아니오 | FK → returns.return_id | 반품 접수 식별자 |
| shipment_item_id | string | 아니오 | FK → shipment_items.shipment_item_id | 출고 항목 식별자 |
| quantity | integer | 아니오 |  | 해당 테이블의 주문·출고·반품 수량 |
| reason_code | string | 아니오 | damaged / wrong_item / changed_mind / other | 반품 사유 분류 |
| reason_detail | string | 예 |  | 자유 서술 반품 사유 |
| item_condition | string | 아니오 | unopened / opened / damaged / unknown | 반품 물품 상태 |

## refunds

환불 시도 1회

| 컬럼 | 타입 | null 허용 | 키·제약 | 의미 |
|---|---|---|---|---|
| refund_id | string | 아니오 | PK | 환불 시도 식별자 |
| payment_id | string | 아니오 | FK → payments.payment_id | 결제 시도 식별자 |
| return_id | string | 예 | FK → returns.return_id | 반품 접수 식별자 |
| refund_type | string | 아니오 | order_cancellation / return | 환불 원인 |
| refund_status | string | 아니오 | pending / succeeded / failed | 환불 처리 상태 |
| amount | integer | 아니오 |  | 해당 결제·환불 시도 금액 KRW |
| requested_at | datetime | 아니오 |  | 요청 시각 |
| refunded_at | datetime | 예 |  | 환불 성공 시각 |
| failure_code | string | 예 |  | 실패 코드 |
