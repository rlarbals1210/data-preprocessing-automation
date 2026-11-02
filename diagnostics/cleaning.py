"""Create audited CSV copies from fresh, policy-approved diagnosis candidates."""
import csv
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .classification import CATEGORIES, markdown
from .engine import diagnose, read_table


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def problem_keys(report):
    return {(i['table'], i['record'], tuple(i['columns']), i['code']) for i in report['issues']
            if i['category'] in {'business_rule_violation', 'human_review'}}


def duplicate_keys(report):
    return {(g['table'], g['code'], tuple(g['columns']), tuple(g['records']))
            for g in report['duplicate_groups']}


def clean(input_path, config, output, encoding='utf-8-sig', delimiter=','):
    source, output = Path(input_path).resolve(), Path(output).resolve()
    source_dir = source if source.is_dir() else source.parent
    if output == source_dir or source_dir in output.parents:
        raise ValueError('Output must be outside the input directory')
    if output.exists():
        raise ValueError('Output already exists; choose a new directory')
    before = diagnose(source, config, encoding, delimiter)
    if before['status'] != 'completed':
        raise ValueError('Input diagnosis incomplete; no cleaned files produced')
    tables = {name: read_table(Path(meta['file']), encoding, delimiter)
              for name, meta in before['tables'].items()}
    for name, table in tables.items():
        if table['sha256'] != before['tables'][name]['sha256']:
            raise ValueError('Input changed during diagnosis')
    # Recompute now, never trust a previously generated or edited report.
    blocked = {(i['table'], i['record'], c) for i in before['issues']
               if i['category'] in {'business_rule_violation', 'human_review'}
               and i['code'] != 'duplicate_row' for c in i['columns']}
    candidates, held = {}, []
    for item in before['issues']:
        if item['category'] != 'standardization_candidate':
            continue
        if len(item['columns']) != 1 or 'suggested_value' not in item:
            raise ValueError('Invalid candidate')
        col = item['columns'][0]
        key = (item['table'], item['record'], col)
        if key in blocked:
            held.append(item)
            continue
        new = item['suggested_value']
        if new is not None and not isinstance(new, str):
            raise ValueError('Candidate must be string or null')
        if key in candidates and candidates[key]['suggested_value'] != new:
            raise ValueError('Conflicting suggestions for one cell')
        if key not in candidates:
            candidates[key] = dict(item, codes=[item['code']])
        elif item['code'] not in candidates[key]['codes']:
            candidates[key]['codes'].append(item['code'])
    changes = []
    for (name, record, col), item in sorted(candidates.items()):
        row = tables[name]['rows'][record-1]
        old = row['values'][col]
        proposed = item['suggested_value']
        new = '' if proposed is None else proposed
        if new == old:
            continue
        rule = config.get('tables', {}).get(name, {}).get('columns', {}).get(col, {})
        changes.append({'table': name, 'source_file': tables[name]['file'], 'record': record,
                        'source_line_start': row['line_start'], 'source_line_end': row['line_end'],
                        'column': col, 'before': old, 'after': new,
                        'logical_after': proposed, 'codes': item['codes'],
                        'column_rule': rule, 'reason': item['category_reason']})
        row['values'][col] = new
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'cleaner_version': '1.0.0', 'status': 'running',
                'started_at': datetime.now(UTC).isoformat(),
                'input': str(source), 'output': str(output), 'config': config,
                'input_encoding': encoding, 'input_delimiter': delimiter,
                'output_encoding': 'utf-8', 'output_delimiter': ',',
                'null_representation': 'empty CSV field; logical null recorded in changes.json',
                'proposed_changed_cells': len(changes), 'held_candidate_events': len(held)}
    dump(output/'manifest.json', manifest)
    dump(output/'before.json', before)
    dump(output/'held.json', held)
    try:
        with tempfile.TemporaryDirectory(prefix='.staging-', dir=output) as staging:
            stage = Path(staging)
            for name, table in tables.items():
                with (stage/(name+'.csv')).open('w', encoding='utf-8', newline='') as f:
                    writer = csv.writer(f, lineterminator='\n')
                    writer.writerow(table['headers'])
                    writer.writerows([[r['values'][c] for c in table['headers']] for r in table['rows']])
            after = diagnose(stage, config)
            dump(output/'after.json', after)
            new_problems = problem_keys(after) - problem_keys(before)
            new_groups = duplicate_keys(after) - duplicate_keys(before)
            if after['status'] != 'completed' or new_problems or new_groups:
                dump(output/'validation-failure.json', {'new_problems': sorted(new_problems),
                                                       'new_duplicate_groups': sorted(new_groups)})
                raise ValueError('Post-clean diagnosis introduced problems; cleaned CSVs were not published')
            # Independently re-read output, compare every cell with the intended change ledger.
            ledger = {(x['table'], x['record'], x['column']): x for x in changes}
            hashes = {}
            for name, table in tables.items():
                original = read_table(Path(table['file']), encoding, delimiter)
                if original['sha256'] != table['sha256']:
                    raise ValueError('Source changed during cleaning')
                reread = read_table(stage/(name+'.csv'))
                if original['headers'] != reread['headers'] or len(original['rows']) != len(reread['rows']):
                    raise ValueError('Output changed headers or row count')
                for row, cleaned in zip(original['rows'], reread['rows'], strict=True):
                    for col, value in row['values'].items():
                        change = ledger.get((name, row['record'], col))
                        expected = change['after'] if change else value
                        if cleaned['values'][col] != expected:
                            raise ValueError('Output differs from change ledger')
                hashes[name] = {'input_sha256': original['sha256'], 'output_sha256': reread['sha256'],
                                'rows': len(original['rows'])}
            data_dir = output/'data'
            stage.rename(data_dir)
            # Final paths in post-clean diagnosis must not point at removed staging paths.
            for table in after['tables'].values():
                table['file'] = str(data_dir/Path(table['file']).name)
            for item in after['issues']:
                item['file'] = str(data_dir/Path(item['file']).name)
            dump(output/'after.json', after)
            (output/'after-summary.md').write_text(markdown(after), encoding='utf-8')
            dump(output/'changes.json', changes)
            with (output/'changes.csv').open('w', encoding='utf-8-sig', newline='') as f:
                fields = ['table','record','column','before','after','logical_after','codes','source_file','source_line_start','source_line_end','reason']
                writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
                writer.writeheader()
                for change in changes:
                    writer.writerow({**change, 'logical_after': json.dumps(change['logical_after'],ensure_ascii=False), 'codes': ','.join(change['codes'])})
            manifest.update(status='completed', changed_cells=len(changes), files=hashes,
                            rows=sum(x['rows'] for x in hashes.values()),
                            before_categories=before['summary']['category_counts'],
                            after_categories=after['summary']['category_counts'],
                            source_unchanged=True, all_cells_verified=True,
                            completed_at=datetime.now(UTC).isoformat())
            dump(output/'manifest.json', manifest)
            lines = ['# 표준화 실행 결과', '', f'변경 셀: {len(changes)}개 / 행 수: {manifest["rows"]}',
                     '원본 해시·행 수·컬럼·변경 내역과 전체 출력 셀 대조 완료.',
                     '새 업무 위반·검토 항목·중복 그룹 없음. 기존 문제는 아래에 남아 있습니다.', '',
                     '| 분류 | 정제 전 | 정제 후 |','|---|---:|---:|']
            lines += [f'| {label} | {manifest["before_categories"][key]} | {manifest["after_categories"][key]} |' for key,label in CATEGORIES.items()]
            lines += ['', '정제 CSV: data/ · 셀별 변경: changes.csv / changes.json · 남은 문제: after-summary.md',
                      'CSV의 null은 빈 필드로 저장하며 changes.json에는 원문과 논리 null을 구분합니다.',
                      '행 삭제·병합·모호한 값 추정은 수행하지 않았습니다. 진단 건수는 오류 행 수와 다릅니다.']
            (output/'summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    except BaseException as exc:
        manifest.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        dump(output/'manifest.json', manifest)
        raise
    return manifest
