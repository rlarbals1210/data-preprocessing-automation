"""표준화 변환 등록표 (docs/technical-design.md 6절, 엔진 확장 ②, M1-8).

변환 ID → (대상 타입, 변환 함수, 정책 근거 키). 설정 검증기와 후보 생성(classification.proposal)이 같은 표를 쓴다.
- 새 변환은 새 ID로 등록한다. 기존 ID(krw_integer·kr_mobile·iso_datetime)의 동작은 바꾸지 않는다(T-01).
- 변환은 명시적 형식만 다룬다. 잘리거나 모호한 값은 추정하지 않고 그대로 돌려준다(→ 후보 없음).
- 변환 결과는 classification.proposal()이 타입·패턴·허용값·범위로 다시 검사해야 후보가 된다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Transform:
    type: str
    func: Callable[[str], str]
    policy_key: str  # config/normalization-policy.json의 항목


_KRW = re.compile(r'[+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?: ?원)?')


def krw_integer(v: str) -> str:
    if _KRW.fullmatch(v):
        return re.sub(r' ?원$', '', v).replace(',', '')
    return v


def krw_integer_symbol(v: str) -> str:
    """krw_integer + 앞에 붙은 ₩(₩ 뒤 공백 하나 허용). 정책: 'KRW 정수. 쉼표·원·₩ 표기는 숫자값 보존하여 정리.'"""
    m = re.fullmatch(r'([+-]?)₩ ?(.+)', v)
    if m:
        inner = krw_integer(m.group(2))
        return m.group(1) + inner if inner != m.group(2) or re.fullmatch(r'[0-9]+', inner) else v
    return krw_integer(v)


def kr_mobile(v: str) -> str:
    compact = re.sub(r'[ ()-]', '', v)
    if re.fullmatch(r'010[0-9]{8}', compact):
        return compact[:3] + '-' + compact[3:7] + '-' + compact[7:]
    return v


def kr_mobile_intl(v: str) -> str:
    """kr_mobile + 명시적 +82 국가코드. 정책: '공백·하이픈·괄호·명시적 +82 국가코드만 정리. 11자리 복원 추측 금지.'
    +82 뒤는 10으로 시작하는 정확히 10자리(국내 0 생략)만 받는다."""
    compact = re.sub(r'[ ()-]', '', v)
    if compact.startswith('+82'):
        rest = compact[3:]
        if re.fullmatch(r'10[0-9]{8}', rest):
            return '010-' + rest[2:6] + '-' + rest[6:]
        return v
    return kr_mobile(v)


_ISO_FORMATS = ('%Y-%m-%d %H:%M:%S%z', '%Y/%m/%d %H:%M:%S%z')
_YMD_FORMATS = _ISO_FORMATS + ('%Y.%m.%d %H:%M:%S%z', '%Y.%m.%dT%H:%M:%S%z', '%Y/%m/%dT%H:%M:%S%z')


def _to_iso(v: str, formats: tuple[str, ...]) -> str:
    # 시간대가 명시된 연-월-일 순서만. 시간대는 추정하지 않는다.
    for fmt in formats:
        try:
            return datetime.strptime(v, fmt).isoformat()
        except ValueError:
            pass
    return v


def iso_datetime(v: str) -> str:
    return _to_iso(v, _ISO_FORMATS)


def iso_datetime_ymd(v: str) -> str:
    """iso_datetime + 점 구분(YYYY.MM.DD)·T 구분 변형. 정책: '시간대 +09:00이 명시되고 연-월-일 순서가 명확한 표현만
    ISO로 통일.' 4자리 연도로 시작하는 연-월-일만 받고 07/08/26 같은 모호한 날짜는 받지 않는다."""
    return _to_iso(v, _YMD_FORMATS)


TRANSFORMS: dict[str, Transform] = {
    'krw_integer': Transform('integer', krw_integer, 'money'),
    'krw_integer_symbol': Transform('integer', krw_integer_symbol, 'money'),
    'kr_mobile': Transform('string', kr_mobile, 'phone'),
    'kr_mobile_intl': Transform('string', kr_mobile_intl, 'phone'),
    'iso_datetime': Transform('datetime', iso_datetime, 'datetime'),
    'iso_datetime_ymd': Transform('datetime', iso_datetime_ymd, 'datetime'),
}
