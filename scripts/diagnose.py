#!/usr/bin/env python3
"""Diagnose one CSV or a directory of CSVs without changing source values."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diagnostics.engine import diagnose
from diagnostics.classification import markdown, CATEGORIES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='CSV file or directory')
    parser.add_argument('--config', type=Path, help='Optional JSON rules')
    parser.add_argument('--encoding', default='utf-8-sig')
    parser.add_argument('--delimiter', default=',')
    parser.add_argument('--output', type=Path, help='New output directory; existing directory is rejected')
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding='utf-8')) if args.config else {}
        if not isinstance(config, dict):
            raise ValueError('Config must be an object')
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
        output = (args.output or ROOT / 'reports/diagnostics' / run_id).resolve()
        source_dir = args.input.resolve() if args.input.is_dir() else args.input.resolve().parent
        if output == source_dir or source_dir in output.parents:
            raise ValueError('Output must be outside the input directory')
        if output.exists():
            raise ValueError('Output directory already exists; choose a new path')
        report = diagnose(args.input, config, args.encoding, args.delimiter)
        report['checked_at'] = datetime.now(timezone.utc).isoformat()
        report['input'] = str(args.input.resolve())
        output.mkdir(parents=True, exist_ok=False)
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        summary = report['summary']
        (output / 'summary.md').write_text(markdown(report), encoding='utf-8')
        print(f'실행 상태: {report["status"]} / {summary["rows_read"]}행')
        for key, label in CATEGORIES.items():
            print(f'{label}: {summary["category_counts"][key]}건')
        print(f'Report: {output / "report.json"}')
        print(f'Summary: {output / "summary.md"}')
        return 2 if report['status'] == 'incomplete' else 1 if summary['severity_counts'].get('error', 0) or summary['severity_counts'].get('warning', 0) else 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f'Diagnosis failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
