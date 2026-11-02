"""Policy-driven presentation; suggestions never mutate input values."""
import re
from collections import Counter
from decimal import Decimal

from .transforms import TRANSFORMS

CATEGORIES = {
    'normal_observation': '정상 관측',
    'standardization_candidate': '표준화 후보',
    'business_rule_violation': '업무 규칙 위반',
    'human_review': '사람 검토 필요',
}


def proposal(value, rule):
    from .engine import normalize, parse
    candidate = normalize(value, rule)
    policy = rule.get('standardization')
    if policy:
        candidate = TRANSFORMS[policy].func(candidate)
    if candidate == value:
        return None
    try:
        parsed = parse(candidate, rule)
    except ValueError:
        return None
    if 'pattern' in rule and not re.fullmatch(rule['pattern'], candidate):
        return None
    if 'allowed' in rule and candidate not in {normalize(v, rule) for v in rule['allowed']}:
        return None
    if 'min' in rule and parsed < Decimal(str(rule['min'])):
        return None
    if 'max' in rule and parsed > Decimal(str(rule['max'])):
        return None
    return candidate


def classify(issues, tables, specs):
    from .engine import missing
    violations = {(i['table'], i['record'], c) for i in issues
                  if i['code'] in {'required', 'allowed', 'min', 'max'} for c in i['columns']}
    groups = {}
    for item in issues:
        table, record, columns = item['table'], item['record'], item['columns']
        rules = specs.get(table, {}).get('columns', {})
        col = columns[0]
        rule = rules.get(col, {})
        value = item['values'][col]
        code = item['code']
        category, reason = 'human_review', '해석 또는 처리 정책을 확인해야 합니다.'
        if code in {'required', 'allowed', 'min', 'max', 'reference', 'unique'}:
            category, reason = 'business_rule_violation', '설정한 업무 규칙을 위반합니다. 올바른 대체값은 별도 확인이 필요합니다.'
        elif code == 'blank':
            if (table, record, col) in violations:
                category, reason = 'business_rule_violation', '이 행에서는 필수값이므로 빈 값을 허용하지 않습니다.'
            elif col in rules:
                category, reason = 'normal_observation', '현재 설정에서 허용하는 빈 값입니다.'
            else:
                category, reason = 'normal_observation', '빈 값 관측입니다. 정책 미설정이므로 업무상 정상 여부는 미확인입니다.'
        elif code in {'missing_candidate', 'outer_whitespace', 'type', 'pattern', 'comparison_normalization'}:
            if (table, record, col) in violations:
                category, reason = 'business_rule_violation', '같은 셀에서 확인된 업무 규칙 위반을 먼저 해결해야 합니다.'
            elif col in rules and missing(value, rule):
                category, reason = 'standardization_candidate', '이 컬럼의 명시된 결측 정책에 따라 null 표현을 통일할 수 있습니다.'
                item['suggested_value'] = None
            else:
                candidate = proposal(value, rule)
                if candidate is not None:
                    category, reason = 'standardization_candidate', '설정이 허용한 변환 후 컬럼의 타입·형식·허용값·범위 검사를 통과합니다.'
                    item['suggested_value'] = candidate
        item['category'] = category
        item['category_reason'] = reason
        if code in {'unique', 'duplicate_row'}:
            # Stable grouping by the same configured key; raw values may differ under comparison options.
            from .engine import normalize
            key_values = tuple(normalize(item['values'][c], rules.get(c, {})) if code == 'unique'
                               else item['values'][c] for c in columns)
            key = (table, code, tuple(columns), key_values)
            if key not in groups:
                groups[key] = {'id': f'duplicate-{len(groups)+1}', 'table': table,
                               'code': code, 'columns': columns, 'records': []}
            group = groups[key]
            group['records'].append(record)
            item['group_id'] = group['id']
    for group in groups.values():
        rows = [tables[group['table']]['rows'][r-1]['values'] for r in group['records']]
        conflicts = [c for c in tables[group['table']]['headers'] if len({row[c] for row in rows}) > 1]
        group['kind'] = 'conflicting' if conflicts else 'identical'
        group['differing_columns'] = conflicts
        group['participant_count'] = len(rows)
    return list(groups.values())


def markdown(report):
    def cell(value):
        return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '&#124;').replace('\r', '\\r').replace('\n', '\\n')
    s = report['summary']
    lines = ['# CSV 진단 결과', '', f'실행 상태: {report["status"]}',
             f'테이블 {s["tables_read"]}개 / {s["rows_read"]}행',
             f'진단 이벤트 {s["issue_count"]}건 — 같은 행·셀에 여러 항목이 겹칠 수 있습니다.', '',
             '표준화 후보는 제안이며 원본을 변경하지 않습니다. 정상 관측도 전체 데이터의 정상 판정을 의미하지 않습니다.',
             '기존 severity는 규칙의 엄격도, 아래 분류는 검토 목적입니다. 둘은 별도 필드로 유지합니다.', '',
             '| 분류 | 이벤트 수 |', '|---|---:|']
    lines += [f'| {label} | {s["category_counts"][key]} |' for key, label in CATEGORIES.items()]
    for key, label in CATEGORIES.items():
        subset = [i for i in report['issues'] if i['category'] == key]
        lines += ['', f'## {label}', '', '| 테이블 | 컬럼 | 규칙 | 건수 |', '|---|---|---|---:|']
        counts = Counter((i['table'], ', '.join(i['columns']), i['code']) for i in subset)
        lines += ['| ' + ' | '.join(map(cell, (*parts, count))) + ' |' for parts, count in sorted(counts.items())]
        if subset:
            lines += ['', '대표 사례 (최대 5개, 전체는 report.json):', '']
            # Show distinct rules/columns instead of five copies of the first condition.
            seen = set()
            for i in subset:
                signature = (i['table'], tuple(i['columns']), i['code'])
                if signature in seen:
                    continue
                seen.add(signature)
                suggestion = f' → 제안 {cell(repr(i["suggested_value"]))}' if 'suggested_value' in i else ''
                lines.append(f'- {cell(i["table"])} 레코드 {i["record"]}: {cell(repr(i["values"]))}{suggestion}. {i["category_reason"]}')
                if len(seen) == 5:
                    break
    lines += ['', '## 중복 그룹', '', '키 중복과 동일 행 중복은 겹칠 수 있습니다. 그룹별 참여 행을 확인하고 임의로 삭제하지 마세요.', '',
              '| 그룹 | 테이블 | 검사 | 유형 | 레코드 | 차이 컬럼 |', '|---|---|---|---|---|---|']
    for g in report['duplicate_groups']:
        lines.append('| ' + ' | '.join(map(cell, [g['id'], g['table'], g['code'],
            '내용 충돌' if g['kind'] == 'conflicting' else '동일 내용', g['records'], ', '.join(g['differing_columns'])])) + ' |')
    lines += ['', f'실행 오류 {len(report["errors"])}건 / 건너뛴 검사 {len(report["skipped_rules"])}건.',
              'completed는 실행 완료, incomplete는 검사 미완료입니다. 상세 오류·원문·줄 위치·입력 해시는 report.json에 있습니다.']
    return '\n'.join(lines) + '\n'
