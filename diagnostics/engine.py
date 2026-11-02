"""Reusable, read-only CSV diagnosis. Standard library only."""
import csv
import hashlib
import io
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

CANDIDATES = ['null', 'none', 'n/a', 'na', '없음', '미입력']
TYPES = {'string', 'integer', 'decimal', 'date', 'datetime'}


class ConfigError(ValueError):
    pass


def keys(obj, allowed, where):
    if not isinstance(obj, dict):
        raise ConfigError(f'{where}: object required')
    unknown = set(obj) - set(allowed)
    if unknown:
        raise ConfigError(f'{where}: unknown settings {sorted(unknown)}')


def strings(value, where, nonempty=False):
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f'{where}: list of strings required')
    if nonempty and not value:
        raise ConfigError(f'{where}: empty list not allowed')


def validate_config(config):
    keys(config, ['version', 'missing_candidates', 'tables'], 'config')
    if type(config.get('version', 1)) is not int or config.get('version', 1) != 1:
        raise ConfigError('Unsupported config version')
    strings(config.get('missing_candidates', CANDIDATES), 'missing_candidates')
    tables = config.get('tables', {})
    if not isinstance(tables, dict):
        raise ConfigError('tables: object required')
    for table, spec in tables.items():
        if not isinstance(table, str) or not table:
            raise ConfigError('Invalid table name')
        keys(spec, ['columns', 'unique', 'references'], table)
        columns = spec.get('columns', {})
        if not isinstance(columns, dict):
            raise ConfigError(f'{table}.columns: object required')
        for col, rule in columns.items():
            if not isinstance(col, str) or not col:
                raise ConfigError(f'{table}: invalid column name')
            where = f'{table}.{col}'
            keys(rule, ['required', 'missing_values', 'strip', 'casefold', 'type',
                        'allowed', 'pattern', 'min', 'max', 'formats', 'required_if', 'standardization'], where)
            for opt in ['required', 'strip', 'casefold']:
                if opt in rule and type(rule[opt]) is not bool:
                    raise ConfigError(f'{where}.{opt}: boolean required')
            for opt in ['missing_values', 'allowed', 'formats']:
                if opt in rule:
                    strings(rule[opt], f'{where}.{opt}', opt != 'missing_values')
            if 'standardization' in rule:
                policies = {'krw_integer': 'integer', 'kr_mobile': 'string', 'iso_datetime': 'datetime'}
                policy = rule['standardization']
                if not isinstance(policy, str) or policy not in policies:
                    raise ConfigError(f'{where}: unsupported standardization policy')
                if rule.get('type', 'string') != policies[policy]:
                    raise ConfigError(f'{where}: standardization policy/type mismatch')
            if rule.get('type', 'string') not in TYPES:
                raise ConfigError(f'{where}: unsupported type')
            if 'formats' in rule and rule.get('type') not in {'date', 'datetime'}:
                raise ConfigError(f'{where}: formats require date/datetime')
            for fmt in rule.get('formats', []):
                try:
                    sample = datetime(2000, 1, 2, 3, 4, 5, tzinfo=UTC).strftime(fmt)
                    datetime.strptime(sample, fmt)
                except (ValueError, OverflowError) as exc:
                    raise ConfigError(f'{where}: invalid date/time format {fmt!r}') from exc
            if 'pattern' in rule:
                if not isinstance(rule['pattern'], str):
                    raise ConfigError(f'{where}: pattern must be a string')
                try:
                    re.compile(rule['pattern'])
                except re.error as exc:
                    raise ConfigError(f'{where}: invalid regex: {exc}') from exc
            for opt in ['min', 'max']:
                if opt in rule:
                    try:
                        number = Decimal(str(rule[opt]))
                        if not number.is_finite():
                            raise ValueError('nonfinite')
                    except (InvalidOperation, ValueError):
                        raise ConfigError(f'{where}.{opt}: finite number required') from None
                    if rule.get('type') not in {'integer', 'decimal'}:
                        raise ConfigError(f'{where}: min/max require numeric type')
            if 'min' in rule and 'max' in rule and Decimal(str(rule['min'])) > Decimal(str(rule['max'])):
                raise ConfigError(f'{where}: min exceeds max')
            if 'required_if' in rule:
                condition = rule['required_if']
                keys(condition, ['column', 'in'], where + '.required_if')
                if condition.get('column') not in columns:
                    raise ConfigError(f'{where}: condition column must have a rule')
                strings(condition.get('in'), where + '.required_if.in', True)
        if not isinstance(spec.get('unique', []), list):
            raise ConfigError(f'{table}.unique: list required')
        for group in spec.get('unique', []):
            strings(group, table + '.unique', True)
            if len(set(group)) != len(group) or any(c not in columns for c in group):
                raise ConfigError(f'{table}: invalid unique columns')
        if not isinstance(spec.get('references', []), list):
            raise ConfigError(f'{table}.references: list required')
        for ref in spec.get('references', []):
            keys(ref, ['columns', 'table', 'target_columns'], table + '.references')
            strings(ref.get('columns'), 'reference.columns', True)
            strings(ref.get('target_columns'), 'reference.target_columns', True)
            target = ref.get('table')
            if not isinstance(target, str) or target not in tables:
                raise ConfigError(f'{table}: reference target must have a table configuration')
            if len(ref['columns']) != len(ref['target_columns']):
                raise ConfigError(f'{table}: reference key lengths differ')
            if any(c not in columns for c in ref['columns']) or any(
                    c not in tables[target].get('columns', {}) for c in ref['target_columns']):
                raise ConfigError(f'{table}: reference columns must have rules')
    return config


def read_table(path, encoding='utf-8-sig', delimiter=','):
    data = path.read_bytes()
    reader = csv.reader(io.StringIO(data.decode(encoding), newline=''), delimiter=delimiter, strict=True)
    headers = next(reader, [])
    if not headers or any(not h.strip() for h in headers) or len(set(headers)) != len(headers):
        raise ValueError('Empty or duplicate CSV header')
    rows = []
    previous = reader.line_num
    for index, values in enumerate(reader, 1):
        if len(values) != len(headers):
            raise ValueError(f'Record {index}: {len(values)} fields, expected {len(headers)}')
        rows.append({'record': index, 'line_start': previous + 1, 'line_end': reader.line_num,
                     'values': dict(zip(headers, values, strict=True))})
        previous = reader.line_num
    return {'file': str(path.resolve()), 'sha256': hashlib.sha256(data).hexdigest(),
            'headers': headers, 'rows': rows}


def normalize(value, rule):
    if rule.get('strip', False):
        value = value.strip()
    if rule.get('casefold', False):
        value = value.casefold()
    return value


def missing(value, rule):
    if not value.strip():
        return True
    return normalize(value, rule) in {normalize(v, rule) for v in rule.get('missing_values', [])}


def parse(value, rule):
    value = normalize(value, rule)
    kind = rule.get('type', 'string')
    if kind == 'integer':
        if not re.fullmatch(r'[+-]?[0-9]+', value):
            raise ValueError('Expected plain integer')
        return Decimal(value)
    if kind == 'decimal':
        if not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)', value):
            raise ValueError('Expected plain decimal')
        return Decimal(value)
    if kind in {'date', 'datetime'}:
        formats = rule.get('formats', ['%Y-%m-%d'] if kind == 'date' else ['%Y-%m-%dT%H:%M:%S%z'])
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                pass
        raise ValueError('No accepted date/time format matched')
    return value


def shape(value):
    if not value.strip():
        return 'blank'
    if re.fullmatch(r'[+-]?[0-9]+', value):
        return 'integer_like'
    if re.fullmatch(r'[+-]?(?:[0-9]+\.[0-9]*|\.[0-9]+)', value):
        return 'decimal_like'
    if re.match(r'[0-9]{2,4}[-/.][0-9]{1,2}[-/.][0-9]{1,4}', value):
        return 'date_like'
    return 'text'


def column_rule_kinds(rule):
    """한 컬럼 규칙에서 실제로 검사·후보 생성을 일으키는 규칙 종류 (엔진 확장 ①)."""
    kinds = []
    if rule.get('required') is True:
        kinds.append('required')
    if 'required_if' in rule:
        kinds.append('required_if')
    if 'missing_values' in rule:
        kinds.append('missing_values')
    if rule.get('strip') or rule.get('casefold'):
        kinds.append('comparison_normalization')
    kinds += [k for k in ('allowed', 'pattern') if k in rule]
    if rule.get('type', 'string') != 'string':
        kinds.append('type')
    kinds += [k for k in ('min', 'max', 'standardization') if k in rule]
    return kinds


def rule_execution(specs, tables, ready, read_failed):
    """규칙별 실행 기록. rule_counts(문제 코드별 건수)는 실행 증거가 아니므로 이 목록으로 확인한다.

    reason: read_error(파일 읽기 실패) · table_unavailable(설정 테이블 입력 없음) ·
    columns_unavailable(설정 컬럼 일부 없음 → 테이블 규칙 전체 제외) ·
    reference_target_unavailable(참조 대상 테이블이 검사 불가).
    """
    records = []

    def add(table, kind, columns, executed, reason, target=None):
        entry = {'table': table, 'kind': kind, 'columns': list(columns)}
        if target is not None:
            entry['target'] = target
        entry.update(executed=executed, reason=reason)
        records.append(entry)

    for name in tables:
        add(name, 'profile', [], True, None)
    for name in read_failed:
        add(name, 'profile', [], False, 'read_error')
    for name, spec in specs.items():
        if name in ready:
            reason = None
        elif name in read_failed:
            reason = 'read_error'
        elif name not in tables:
            reason = 'table_unavailable'
        else:
            reason = 'columns_unavailable'
        for col, rule in spec.get('columns', {}).items():
            for kind in column_rule_kinds(rule):
                add(name, kind, [col], reason is None, reason)
        for group in spec.get('unique', []):
            add(name, 'unique', group, reason is None, reason)
        for ref in spec.get('references', []):
            ref_reason = reason or (None if ref['table'] in ready else 'reference_target_unavailable')
            add(name, 'reference', ref['columns'], ref_reason is None, ref_reason, ref['table'])
    return records


def diagnose(input_path, config=None, encoding='utf-8-sig', delimiter=','):
    config = validate_config({} if config is None else config)
    path = Path(input_path)
    files = [path] if path.is_file() else sorted(path.glob('*.csv'))
    if not files:
        raise ValueError('No CSV input files found')
    if len(delimiter) != 1:
        raise ValueError('Delimiter must be exactly one character')
    names = [p.stem for p in files]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate table names')
    tables, summaries, issues, errors, skipped = {}, {}, [], [], []
    read_failed = []
    specs = config.get('tables', {})
    candidates = {v.strip().casefold() for v in config.get('missing_candidates', CANDIDATES)}

    def issue(table, row, columns, code, severity, reason, basis='observation'):
        issues.append({'table': table, 'file': tables[table]['file'],
                       'record': row['record'], 'line_start': row['line_start'],
                       'line_end': row['line_end'], 'columns': columns,
                       'values': {c: row['values'][c] for c in columns},
                       'code': code, 'severity': severity, 'reason': reason, 'basis': basis})

    for file in files:
        name = file.stem
        try:
            table = read_table(file, encoding, delimiter)
            tables[name] = table
        except (OSError, UnicodeError, ValueError, csv.Error) as exc:
            errors.append({'table': name, 'file': str(file), 'error': str(exc)})
            read_failed.append(name)
            continue
        rows = table['rows']
        profiles = {}
        for column in table['headers']:
            values = [r['values'][column] for r in rows]
            counts = Counter(values)
            profiles[column] = {
                'empty_count': counts[''],
                'whitespace_only_count': sum(bool(v) and not v.strip() for v in values),
                'outer_whitespace_count': sum(v != v.strip() for v in values),
                'distinct_count': len(counts),
                'missing_candidate_count': sum(v.strip().casefold() in candidates for v in values),
                'shape_counts': dict(Counter(shape(v) for v in values)),
                'max_length': max((len(v) for v in values), default=0),
                'top_values': [{'value': v, 'count': n} for v, n in counts.most_common(5)],
            }
            for row in rows:
                value = row['values'][column]
                if not value.strip():
                    issue(name, row, [column], 'blank', 'info', 'Empty or whitespace-only value; may be valid')
                elif value.strip().casefold() in candidates:
                    issue(name, row, [column], 'missing_candidate', 'info', 'Possible missing-value expression; meaning requires policy')
                if value != value.strip():
                    issue(name, row, [column], 'outer_whitespace', 'info', 'Leading or trailing whitespace observed')
        groups = defaultdict(list)
        for row in rows:
            groups[tuple(row['values'][c] for c in table['headers'])].append(row)
        repeated = [group for group in groups.values() if len(group) > 1]
        for group in repeated:
            for row in group:
                issue(name, row, table['headers'], 'duplicate_row', 'warning',
                      f'Exact row appears {len(group)} times; review before deletion')
        summaries[name] = {'file': table['file'], 'sha256': table['sha256'], 'row_count': len(rows),
                           'column_count': len(table['headers']), 'profiles': profiles,
                           'duplicate_row_excess': sum(len(g) - 1 for g in repeated),
                           'configured_columns': list(specs.get(name, {}).get('columns', {}))}

    # Missing configured tables/columns invalidate their rules, never imply a pass.
    ready = {}
    for name, spec in specs.items():
        if name not in tables:
            errors.append({'table': name, 'error': 'Configured table unavailable'})
            continue
        absent = sorted(set(spec.get('columns', {})) - set(tables[name]['headers']))
        if absent:
            errors.append({'table': name, 'error': 'Configured columns unavailable', 'columns': absent})
            continue
        ready[name] = spec

    for name, spec in ready.items():
        columns = spec.get('columns', {})
        for row in tables[name]['rows']:
            for col, rule in columns.items():
                value = row['values'][col]
                required = rule.get('required', False)
                if 'required_if' in rule:
                    condition = rule['required_if']
                    other_rule = columns[condition['column']]
                    other = normalize(row['values'][condition['column']], other_rule)
                    required = required or other in {normalize(v, other_rule) for v in condition['in']}
                if missing(value, rule):
                    if required:
                        issue(name, row, [col], 'required', 'error', 'Required value is missing under configured policy', 'configured')
                    continue
                normalized = normalize(value, rule)
                if normalized != value and value == value.strip():
                    issue(name, row, [col], 'comparison_normalization', 'info',
                          'Configured comparison changes the representation; original preserved', 'configured')
                if 'allowed' in rule and normalized not in {normalize(v, rule) for v in rule['allowed']}:
                    issue(name, row, [col], 'allowed', 'error', 'Value is outside configured allowed values', 'configured')
                if 'pattern' in rule and not re.fullmatch(rule['pattern'], normalized):
                    issue(name, row, [col], 'pattern', 'warning', 'Value does not match configured full pattern; may be a format variant', 'configured')
                try:
                    parsed = parse(value, rule)
                except ValueError:
                    issue(name, row, [col], 'type', 'warning', f'Cannot parse as configured {rule.get("type")}; review format', 'configured')
                    continue
                if 'min' in rule and parsed < Decimal(str(rule['min'])):
                    issue(name, row, [col], 'min', 'error', f'Value is below {rule["min"]}', 'configured')
                if 'max' in rule and parsed > Decimal(str(rule['max'])):
                    issue(name, row, [col], 'max', 'error', f'Value is above {rule["max"]}', 'configured')
        for group in spec.get('unique', []):
            buckets = defaultdict(list)
            for row in tables[name]['rows']:
                if not any(missing(row['values'][c], columns[c]) for c in group):
                    buckets[tuple(normalize(row['values'][c], columns[c]) for c in group)].append(row)
            for bucket in buckets.values():
                if len(bucket) > 1:
                    for row in bucket:
                        issue(name, row, group, 'unique', 'error', f'Configured key appears {len(bucket)} times', 'configured')

    for name, spec in ready.items():
        for ref in spec.get('references', []):
            target = ref['table']
            if target not in ready:
                skipped.append({'table': name, 'rule': 'reference', 'target': target,
                                'reason': 'Target table or configured columns unavailable'})
                continue
            source_rules, target_rules = spec['columns'], ready[target]['columns']
            target_keys = set()
            for row in tables[target]['rows']:
                if not any(missing(row['values'][c], target_rules[c]) for c in ref['target_columns']):
                    target_keys.add(tuple(normalize(row['values'][c], target_rules[c]) for c in ref['target_columns']))
            for row in tables[name]['rows']:
                if any(missing(row['values'][c], source_rules[c]) for c in ref['columns']):
                    continue  # MATCH SIMPLE; required rules handle mandatory missing parts.
                key = tuple(normalize(row['values'][c], source_rules[c]) for c in ref['columns'])
                if key not in target_keys:
                    issue(name, row, ref['columns'], 'reference', 'warning',
                          f'No matching key in {target}; formatting may affect matching', 'configured')

    from .classification import CATEGORIES, classify
    duplicate_groups = classify(issues, tables, ready)
    category_counts = {key: sum(i['category'] == key for i in issues) for key in CATEGORIES}
    severity_counts = dict(Counter(i['severity'] for i in issues))
    return {'engine_version': '1.1.0', 'status': 'incomplete' if errors or skipped else 'completed',
            'mode': 'configured' if specs else 'profile', 'config': config,
            'summary': {'tables_read': len(tables), 'rows_read': sum(len(t['rows']) for t in tables.values()),
                        'issue_count': len(issues), 'severity_counts': severity_counts,
                        'category_counts': category_counts,
                        'rule_counts': dict(Counter(i['code'] for i in issues)),
                        'unconfigured_tables': sorted(set(tables) - set(specs))},
            'duplicate_groups': duplicate_groups, 'tables': summaries, 'issues': issues, 'errors': errors, 'skipped_rules': skipped,
            'rule_execution': rule_execution(specs, tables, ready, read_failed)}
