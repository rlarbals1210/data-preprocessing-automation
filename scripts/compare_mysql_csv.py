#!/usr/bin/env python3
"""Compare parsed CSV cell values with MySQL, without changing either input."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import getpass
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TEXT_TYPES = {'char', 'varchar', 'tinytext', 'text', 'mediumtext', 'longtext'}


def identifier(value):
    return '`' + value.replace('`', '``') + '`'


def read_csv(path):
    content = path.read_bytes()
    reader = csv.reader(io.StringIO(content.decode('utf-8-sig'), newline=''), strict=True)
    headers = next(reader, [])
    if not headers or any(not h for h in headers) or len(set(headers)) != len(headers):
        raise ValueError('CSV header is empty, duplicated, or contains an empty name')
    rows, positions = Counter(), defaultdict(list)
    for record, values in enumerate(reader, start=1):
        if len(values) != len(headers):
            raise ValueError(f'CSV record {record}: expected {len(headers)} fields, got {len(values)}')
        row = tuple(v.encode('utf-8') for v in values)
        rows[row] += 1
        positions[row].append(record)
    return headers, rows, positions, hashlib.sha256(content).hexdigest()


def display_row(row, headers):
    return {h: {'value': None if v is None else v.decode('utf-8'),
                'utf8_hex': None if v is None else v.hex()}
            for h, v in zip(headers, row)}


def differences(expected, actual, headers, positions):
    # No matching by business ID: dirty input can contain duplicated/missing IDs.
    missing, extra = expected - actual, actual - expected
    return {
        'csv_only_count': sum(missing.values()),
        'db_only_count': sum(extra.values()),
        'csv_only': [{'count': count, 'csv_record_numbers': positions[row],
                      'cells': display_row(row, headers)} for row, count in missing.items()],
        'db_only': [{'count': count, 'cells': display_row(row, headers)}
                    for row, count in extra.items()],
    }


def compare_table(connection, database, path):
    headers, expected, positions, digest = read_csv(path)
    result = {'table': path.stem, 'csv_file': str(path), 'csv_sha256': digest,
              'csv_rows': sum(expected.values()), 'columns': headers}
    with connection.cursor() as cursor:
        cursor.execute('SELECT ENGINE FROM information_schema.TABLES '
                       'WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND TABLE_TYPE=%s',
                       (database, path.stem, 'BASE TABLE'))
        table = cursor.fetchone()
        if table is None:
            raise ValueError('Target base table does not exist')
        if table[0] != 'InnoDB':
            raise ValueError('InnoDB required for a consistent read snapshot')
        cursor.execute('SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS '
                       'WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION',
                       (database, path.stem))
        columns = cursor.fetchall()
        names = [name for name, _ in columns]
        if set(names) != set(headers):
            return dict(result, status='error', error='Column names differ',
                        csv_only_columns=sorted(set(headers) - set(names)),
                        db_only_columns=sorted(set(names) - set(headers)))
        unsupported = {name: kind for name, kind in columns if kind not in TEXT_TYPES}
        if unsupported:
            return dict(result, status='error', error='Raw columns must be text types',
                        unsupported_types=unsupported)
        # Binary results avoid server collation comparisons and distinguish NULL/b''.
        projection = ', '.join(f'CAST(CONVERT({identifier(h)} USING utf8mb4) AS BINARY)'
                               for h in headers)
        cursor.execute(f'SELECT {projection} FROM {identifier(database)}.{identifier(path.stem)}')
        actual = Counter(tuple(row) for row in cursor.fetchall())
    diff = differences(expected, actual, headers, positions)
    return dict(result, status='match' if expected == actual else 'different',
                db_rows=sum(actual.values()), **diff)


def run_comparison(connection, database, csv_dir, tables):
    results = []
    for table in sorted(tables):
        try:
            result = compare_table(connection, database, csv_dir / f'{table}.csv')
        except Exception as exc:
            result = {'table': table, 'status': 'error',
                      'error': f'{type(exc).__name__}: {exc}'}
        results.append(result)
        print(f'{table:22} {result["status"]:10} '
              f'CSV={result.get("csv_rows", "?")} DB={result.get("db_rows", "?")}', flush=True)
    unexpected = sorted(p.stem for p in csv_dir.glob('*.csv') if p.stem not in tables)
    status = ('error' if unexpected or any(r['status'] == 'error' for r in results)
              else 'different' if any(r['status'] == 'different' for r in results) else 'match')
    return {'status': status, 'unexpected_csv_files': unexpected, 'tables': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=3306)
    parser.add_argument('--user', default='root')
    parser.add_argument('--database', default='shop_raw')
    parser.add_argument('--csv-dir', type=Path, default=ROOT / 'data/dirty/development')
    parser.add_argument('--schema', type=Path, default=ROOT / 'schema.json')
    parser.add_argument('--report', type=Path, default=ROOT / 'reports/mysql-csv-comparison.json')
    args = parser.parse_args()
    report_path = args.report.resolve()
    try:
        import pymysql
    except ImportError:
        print('Install dependency: python -m pip install -r requirements-mysql.txt', file=sys.stderr)
        return 2
    try:
        csv_dir = args.csv_dir.resolve()
        tables = list(json.loads(args.schema.read_text(encoding='utf-8'))['tables'])
        if not tables or not csv_dir.is_dir():
            raise ValueError('Missing table definitions or CSV directory')
        inputs = {args.schema.resolve()} | set(csv_dir.glob('*.csv'))
        if report_path in inputs:
            raise ValueError('Report must not overwrite a source file')
        password = getpass.getpass('MySQL password (입력 숨김): ')
        connection = pymysql.connect(host=args.host, port=args.port, user=args.user,
                                     password=password, database=args.database, charset='utf8mb4',
                                     autocommit=False, local_infile=False, connect_timeout=10,
                                     read_timeout=60, write_timeout=10)
        del password
        try:
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                cursor.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
            report = run_comparison(connection, args.database, csv_dir, tables)
        finally:
            connection.rollback()
            connection.close()
        report.update(database=args.database, host=args.host, port=args.port,
                      checked_at=datetime.now(timezone.utc).isoformat(),
                      comparison='Parsed UTF-8 cell values and duplicate row counts; row order ignored')
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'Overall: {report["status"]}\nReport: {report_path}')
        return {'match': 0, 'different': 1, 'error': 2}[report['status']]
    except (Exception, KeyboardInterrupt) as exc:
        print(f'Verification did not complete: {type(exc).__name__}: {exc}', file=sys.stderr)
        print('No new completed report. Do not interpret an old report as this run.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
