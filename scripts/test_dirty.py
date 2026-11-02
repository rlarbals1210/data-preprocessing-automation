import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

spec=importlib.util.spec_from_file_location('validator',Path(__file__).with_name('validate_dirty.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
clean,source=map(Path,sys.argv[1:3]);results=[]
for scenario in ['drop_label','wrong_observed','invent_review_value','leak_truth_to_inputs']:
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        for name in ['evaluation_private','data']:shutil.copytree(source/name,root/name)
        file=root/'evaluation_private/development/cases.jsonl'
        cases=[json.loads(s) for s in file.read_text().splitlines()]
        if scenario=='drop_label':cases.pop(next(i for i,c in enumerate(cases) if c['expected_action']=='normalize'));rule='unlabelled_change'
        elif scenario=='wrong_observed':next(c for c in cases if c['expected_action']=='normalize')['observed_value']='tampered';rule='observed_value_matches'
        elif scenario=='invent_review_value':next(c for c in cases if c['expected_action']=='review')['expected_value']='invented';rule='review_no_invented_value'
        else:(root/'data/dirty/development/answers.json').write_text('{}');rule='input_allowlist'
        file.write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in cases))
        result=module.validate(clean,root)
        assert not result['passed'] and any(e['rule']==rule for e in result['errors']),scenario
        results.append({'scenario':scenario,'expected_rule':rule,'detected':True})
result={'passed':True,'cases':results}
(source/'reports/dirty-validator-tests.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
