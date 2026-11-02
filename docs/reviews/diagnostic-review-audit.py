import csv,json,hashlib,re
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime

root=Path('/Users/gimgyumin/Developer/data-preprocessing-automation')
r=json.loads((root/'reports/diagnostics/shop-v1/report.json').read_text())
cfg=r['config']['tables']
tables={}
for t,meta in r['tables'].items():
    p=Path(meta['file'])
    assert hashlib.sha256(p.read_bytes()).hexdigest()==meta['sha256']
    with p.open(newline='',encoding='utf-8-sig') as f: tables[t]=list(csv.DictReader(f))
for i in r['issues']:
    assert all(tables[i['table']][i['record']-1][k]==v for k,v in i['values'].items())
print('Input hashes and all issue values match CSV.')
types=Counter(); residual=[]; patterns=Counter(); examples={}
for i in r['issues']:
    col=i['columns'][0]; val=i['values'][col]
    if i['code']=='type':
        kind=cfg[i['table']]['columns'][col]['type']
        label='unresolved'
        if kind=='datetime':
            for fmt in ['%Y-%m-%d %H:%M:%S%z','%Y/%m/%d %H:%M:%S%z']:
                try:datetime.strptime(val,fmt);label='datetime_clear_variant';break
                except ValueError:pass
        elif kind=='integer' and re.fullmatch(r'(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?: ?원)?',val):
            label='money_clear_variant'
        types[label]+=1
        examples.setdefault(label,val)
        if label=='unresolved':residual.append({k:i[k] for k in ['table','record','values']})
    if i['code']=='pattern':
        compact=re.sub(r'[ ()-]','',val)
        label='phone_clear_variant' if re.fullmatch(r'010[0-9]{8}',compact) else 'unresolved'
        patterns[label]+=1
        if label=='unresolved':print('PATTERN_UNRESOLVED',i['table'],i['record'],i['values'])
print('TYPE_CLASSES',types,examples,'RESIDUAL',residual[:5])
print('PATTERN_CLASSES',patterns)
print('CANDIDATES',Counter((i['columns'][0],next(iter(i['values'].values()))) for i in r['issues'] if i['code']=='missing_candidate'))
print('BLANK_COLUMNS',Counter((i['table'],i['columns'][0]) for i in r['issues'] if i['code']=='blank'))
unique={}
for i in r['issues']:
    if i['code']=='unique':unique.setdefault((i['table'],i['columns'][0],next(iter(i['values'].values()))),[]).append(i['record'])
for (t,c,v),positions in unique.items():
    rows=[tables[t][n-1] for n in positions]
    changes={k:[x[k] for x in rows] for k in rows[0] if len({x[k] for x in rows})>1}
    print('DUPLICATE_GROUP',t,v,positions,changes)
print('REFERENCE_EXISTS',any(x['옵션코드']=='SKU999999' for x in tables['product_variants']))
print('WHITESPACE',Counter(i['columns'][0] for i in r['issues'] if i['code']=='outer_whitespace'))
print('EMPTY_FAILURE_STATES',Counter(x['결제상태'] for x in tables['payments'] if not x['실패코드']))
print('EMPTY_CANCEL_STATES',Counter(x['주문상태'] for x in tables['orders'] if not x['취소일시']))
print('SEVERITY',Counter(i['severity'] for i in r['issues']))
