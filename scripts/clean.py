#!/usr/bin/env python3
"""Apply fresh standardization candidates into new CSV copies with a cell audit."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from diagnostics.cleaning import clean


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--encoding',default='utf-8-sig')
    parser.add_argument('--delimiter',default=',')
    args=parser.parse_args()
    try:
        config=json.loads(args.config.read_text(encoding='utf-8'))
        output=args.output or ROOT/'reports/cleaning'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
        result=clean(args.input,config,output,args.encoding,args.delimiter)
        print(f'정제 완료: {result["changed_cells"]}셀 변경 / {result["rows"]}행 유지')
        print(f'결과: {Path(output).resolve() / "summary.md"}')
        remaining=result['after_categories']
        return 1 if remaining['business_rule_violation'] or remaining['human_review'] else 0
    except (Exception,KeyboardInterrupt) as exc:
        print(f'Cleaning failed: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
