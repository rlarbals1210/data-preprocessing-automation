"""Mutation tests ensure important business checks actually detect bad exports."""
import csv
import importlib.util
import json
import shutil
import tempfile
from pathlib import Path
import sys

module=importlib.util.spec_from_file_location('validator',Path(__file__).with_name('validate.py'))
v=importlib.util.module_from_spec(module);module.loader.exec_module(v)
root=Path(sys.argv[1])
cases=[
 ('order_items','line_total',lambda rows:0,'1','line_total'),
 ('orders','order_total',lambda rows:0,'1','order_total'),
 ('shipment_items','quantity',lambda rows:0,'999','shipment_quantity'),
 ('return_items','quantity',lambda rows:0,'999','return_quantity'),
 ('refunds','amount',lambda rows:next(i for i,r in enumerate(rows) if r['refund_status']=='succeeded'),'999999999','refund_cap'),
 ('orders','customer_id',lambda rows:0,'CUS999999','foreign_key'),
 ('customers','phone',lambda rows:0,'123','phone_format'),
 ('payments','paid_at',lambda rows:next(i for i,r in enumerate(rows) if r['payment_status']=='failed'),'2026-07-01T10:00:00+09:00','payment_timestamp'),
 ('categories','parent_category_id',lambda rows:0,'CAT000001','category_two_levels'),
]
results=[]
for table,column,selector,value,rule in cases:
 with tempfile.TemporaryDirectory(prefix='moa-validator-') as td:
  target=Path(td)/'project';target.mkdir()
  shutil.copy2(root/'schema.json',target/'schema.json')
  shutil.copytree(root/'data',target/'data')
  file=target/'data/clean'/f'{table}.csv'
  with file.open(newline='') as f:
   reader=csv.DictReader(f);fields=reader.fieldnames;rows=list(reader)
  rows[selector(rows)][column]=value
  with file.open('w',newline='') as f:
   writer=csv.DictWriter(f,fieldnames=fields,lineterminator='\n');writer.writeheader();writer.writerows(rows)
  report=v.validate(target)
  found=any(e['rule']==rule for e in report['errors'])
  assert not report['passed'] and found,(rule,report['errors'][:5])
  results.append({'mutation':f'{table}.{column}','expected_rule':rule,'detected':found})
report={'passed':True,'cases':results}
(root/'reports/validator-mutation-tests.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
