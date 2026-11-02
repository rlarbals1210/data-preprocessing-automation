-- 개발용 오염 CSV용 빈 테이블 생성. MySQL에서 실행하세요.
-- 원본 CSV의 컬럼명과 순서를 유지하며 모든 컬럼은 TEXT NULL입니다.
-- 기존 테이블이 있으면 오류가 납니다. 삭제하거나 덮어쓰지 않습니다.
-- CSV import: UTF-8, comma delimiter, top header, double quote enclosure.
-- Trim whitespaces OFF; Set empty strings to NULL OFF.
-- NULL value mark: __DB_NULL_UNUSED_20261016__ (현재 개발용 CSV에 없는 값임을 확인).
-- 고객번호 등의 원본 키에 PK/UNIQUE/FK 제약을 걸지 않습니다.
-- 실행 후 아래 컬럼 점검 결과가 13행이며 모든 컬럼 수가 TEXT 수와 같아야 합니다.

-- categories.csv: 헤더 제외 15행
CREATE TABLE `shop_raw_dev_text`.`categories` (
    `분류번호` TEXT NULL,
    `상위분류번호` TEXT NULL,
    `분류명` TEXT NULL,
    `사용여부` TEXT NULL
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- customer_addresses.csv: 헤더 제외 311행
CREATE TABLE `shop_raw_dev_text`.`customer_addresses` (
    `주소번호` TEXT NULL,
    `고객번호` TEXT NULL,
    `배송지별칭` TEXT NULL,
    `받는분` TEXT NULL,
    `수령인연락처` TEXT NULL,
    `우편번호` TEXT NULL,
    `기본주소` TEXT NULL,
    `상세주소` TEXT NULL,
    `기본배송지` TEXT NULL,
    `등록일시` TEXT NULL,
    `수정일시` TEXT NULL
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- customers.csv: 헤더 제외 208행
CREATE TABLE `shop_raw_dev_text`.`customers` (
    `고객번호` TEXT NULL,
    `고객명` TEXT NULL,
    `이메일` TEXT NULL,
    `휴대폰` TEXT NULL,
    `회원상태` TEXT NULL,
    `마케팅동의` TEXT NULL,
    `가입일시` TEXT NULL,
    `수정일시` TEXT NULL
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- order_items.csv: 헤더 제외 1712행
CREATE TABLE `shop_raw_dev_text`.`order_items` (
    `주문상세번호` TEXT NULL,
    `주문번호` TEXT NULL,
    `옵션코드` TEXT NULL,
    `주문상품명` TEXT NULL,
    `주문옵션명` TEXT NULL,
    `수량` TEXT NULL,
    `단가` TEXT NULL,
    `할인액` TEXT NULL,
    `항목금액` TEXT NULL
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- orders.csv: 헤더 제외 689행
CREATE TABLE `shop_raw_dev_text`.`orders` (
    `주문번호` TEXT NULL,
    `고객번호` TEXT NULL,
    `주문일시` TEXT NULL,
    `주문경로` TEXT NULL,
    `주문상태` TEXT NULL,
    `받는분` TEXT NULL,
    `수령인연락처` TEXT NULL,
    `우편번호` TEXT NULL,
    `기본주소` TEXT NULL,
    `상세주소` TEXT NULL,
    `배송요청` TEXT NULL,
    `배송비` TEXT NULL,
    `주문총액` TEXT NULL,
    `취소일시` TEXT NULL,
    `수정일시` TEXT NULL
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- payments.csv: 헤더 제외 743행
CREATE TABLE `shop_raw_dev_text`.`payments` (
    `결제번호` TEXT NULL,
    `주문번호` TEXT NULL,
    `결제수단` TEXT NULL,
    `결제상태` TEXT NULL,
    `금액` TEXT NULL,
    `요청일시` TEXT NULL,
    `결제일시` TEXT NULL,
    `실패코드` TEXT NULL
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- product_variants.csv: 헤더 제외 200행
CREATE TABLE `shop_raw_dev_text`.`product_variants` (
    `옵션코드` TEXT NULL,
    `상품번호` TEXT NULL,
    `옵션명` TEXT NULL,
    `바코드` TEXT NULL,
    `판매가` TEXT NULL,
    `무게_g` TEXT NULL,
    `판매상태` TEXT NULL,
    `등록일시` TEXT NULL,
    `수정일시` TEXT NULL
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- products.csv: 헤더 제외 100행
CREATE TABLE `shop_raw_dev_text`.`products` (
    `상품번호` TEXT NULL,
    `분류번호` TEXT NULL,
    `상품명` TEXT NULL,
    `브랜드` TEXT NULL,
    `상품설명` TEXT NULL,
    `상품상태` TEXT NULL,
    `등록일시` TEXT NULL,
    `수정일시` TEXT NULL
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- refunds.csv: 헤더 제외 41행
CREATE TABLE `shop_raw_dev_text`.`refunds` (
    `환불번호` TEXT NULL,
    `결제번호` TEXT NULL,
    `반품번호` TEXT NULL,
    `환불유형` TEXT NULL,
    `환불상태` TEXT NULL,
    `금액` TEXT NULL,
    `요청일시` TEXT NULL,
    `환불일시` TEXT NULL,
    `실패코드` TEXT NULL
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- return_items.csv: 헤더 제외 54행
CREATE TABLE `shop_raw_dev_text`.`return_items` (
    `반품상세번호` TEXT NULL,
    `반품번호` TEXT NULL,
    `출고상세번호` TEXT NULL,
    `수량` TEXT NULL,
    `반품사유코드` TEXT NULL,
    `상세사유` TEXT NULL,
    `물품상태` TEXT NULL
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- returns.csv: 헤더 제외 41행
CREATE TABLE `shop_raw_dev_text`.`returns` (
    `반품번호` TEXT NULL,
    `주문번호` TEXT NULL,
    `반품상태` TEXT NULL,
    `요청일시` TEXT NULL,
    `입고일시` TEXT NULL,
    `종료일시` TEXT NULL
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- shipment_items.csv: 헤더 제외 1624행
CREATE TABLE `shop_raw_dev_text`.`shipment_items` (
    `출고상세번호` TEXT NULL,
    `출고번호` TEXT NULL,
    `주문상세번호` TEXT NULL,
    `수량` TEXT NULL
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- shipments.csv: 헤더 제외 671행
CREATE TABLE `shop_raw_dev_text`.`shipments` (
    `출고번호` TEXT NULL,
    `주문번호` TEXT NULL,
    `택배사` TEXT NULL,
    `송장번호` TEXT NULL,
    `배송상태` TEXT NULL,
    `등록일시` TEXT NULL,
    `출고일시` TEXT NULL,
    `배송완료일시` TEXT NULL
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

SELECT TABLE_NAME, COUNT(*) AS column_count,
       SUM(DATA_TYPE = 'text') AS text_column_count
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'shop_raw_dev_text'
GROUP BY TABLE_NAME
ORDER BY TABLE_NAME;
