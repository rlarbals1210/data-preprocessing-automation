"""Independently validate benchmark labels, complete change coverage and split isolation."""
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def jsonl(p):return [json.loads(x) for x in p.read_text().splitlines() if x]

def validate(clean_root, root):
    clean_root,root=Path(clean_root),Path(root)
    schema=json.loads((clean_root/'schema.json').read_text())
    meta=json.loads((root/'evaluation_private/manifest.json').read_text())
    hashes=json.loads((root/'evaluation_private/input-manifest.json').read_text())
    checks=0; errors=[]
    def check(ok,rule,detail=''):
        nonlocal checks
        checks+=1
        if not ok:errors.append({'rule':rule,'detail':str(detail)})
    for table,h in meta['clean_hashes'].items():check(sha(clean_root/'data/clean'/f'{table}.csv')==h,'clean_unchanged',table)
    check(sha(clean_root/'schema.json')==meta['schema_sha256'],'schema_unchanged')
    baselines={};reports={}
    for split in ['development','evaluation']:
        directory=root/'evaluation_private'/split
        baseline=json.loads((directory/'baseline.json').read_text());baselines[split]=baseline
        labels=jsonl(directory/'cases.jsonl');lineage=jsonl(directory/'row-lineage.jsonl')
        files={};headers={};mapping={}; cases={}
        check(len({x['case_id'] for x in labels})==len(labels),'unique_case_id',split)
        for c in labels:
            key=(c['input_file'],c['record_number'],c['column'])
            check(key not in cases,'unique_case_location',key);cases[key]=c
            if c['expected_action']=='map_column':mapping.setdefault(c['input_file'],{})[c['column']]=c['expected_value']
            if c['expected_action']=='review':check('expected_value' not in c,'review_no_invented_value',c['case_id'])
            else:check(c['expected_value']==c['clean_value'],'expected_clean_value',c['case_id'])
        actual_files={p.name for p in (root/'data/dirty'/split).iterdir() if p.is_file()}
        check(actual_files=={t+'.csv' for t in schema['tables']},'input_allowlist',split)
        for table,definition in schema['tables'].items():
            filename=table+'.csv';p=root/'data/dirty'/split/filename
            with p.open(encoding='utf-8',newline='') as f:
                reader=csv.DictReader(f); rows=list(reader);headers[filename]=reader.fieldnames;files[filename]=rows
            check(sha(p)==hashes[split+'/'+filename]['sha256'],'input_hash',filename)
            check(len(rows)==hashes[split+'/'+filename]['rows'],'input_count',filename)
            check(set(mapping[filename])==set(headers[filename]),'header_coverage',filename)
            check(set(mapping[filename].values())=={f['name'] for f in definition['fields']},'header_bijection',filename)
            check(len(set(headers[filename]))==len(headers[filename]),'unique_headers',filename)
        maps={t:{r[d['fields'][0]['name']]:r for r in baseline[t]} for t,d in schema['tables'].items()}
        # Ensure relational closure BEFORE injecting known foreign-key corruption.
        for t,d in schema['tables'].items():
            check(len(maps[t])==len(baseline[t]),'baseline_unique_pk',t)
            for f in d['fields']:
                if 'references' in f:
                    ref=f['references']['table']
                    for row in baseline[t]:
                        check(not row[f['name']] or row[f['name']] in maps[ref],'baseline_foreign_key',t)
        locations={(x['input_file'],x['record_number']):x for x in lineage}
        check(len(locations)==len(lineage),'unique_lineage',split)
        check(len(lineage)==sum(len(x) for x in files.values()),'lineage_count',split)
        expected_locations={(f,i) for f,rows in files.items() for i in range(1,len(rows)+1)}
        check(set(locations)==expected_locations,'lineage_complete',split)
        changes=0
        for (filename,number),trace in locations.items():
            row=files[filename][number-1];table=filename[:-4];clean=maps[table][trace['clean_pk']]
            group=cases.get((filename,number,'*'))
            if group:
                check(group['observed_value']==row,'row_case_matches',group['case_id'])
                check(group['expected_action']=='review','duplicate_review',group['case_id'])
                pk=schema['tables'][table]['fields'][0]['name'];alias=next(a for a,n in mapping[filename].items() if n==pk)
                peers=[i for i,r in enumerate(files[filename],1) if r[alias]==row[alias]]
                check(len(peers)>1 and all((filename,i,'*') in cases for i in peers),'all_duplicate_peers_review',group['case_id'])
                continue
            for alias,value in row.items():
                n=mapping[filename][alias];c=cases.get((filename,number,alias))
                if value!=clean[n]:
                    changes+=1;check(c is not None,'unlabelled_change',(filename,number,alias))
                if c:
                    check(c['observed_value']==value,'observed_value_matches',c['case_id'])
                    check(c['clean_value']==clean[n],'clean_value_matches',c['case_id'])
                    check(c['canonical_column']==n,'canonical_column_matches',c['case_id'])
                    if c['expected_action']=='keep':check(value==clean[n],'keep_unchanged',c['case_id'])
                    if c['expected_action']=='normalize':
                        issue=c['issue_type'];normalized=value
                        if issue=='phone_format':
                            digits=re.sub(r'\D','',value)
                            if digits.startswith('82'):digits='0'+digits[2:]
                            normalized=digits[:3]+'-'+digits[3:7]+'-'+digits[7:]
                        elif issue=='money_format':normalized=re.sub(r'[, 원₩]','',value)
                        elif issue=='null_token':normalized='' if value.strip() in ['NULL','없음','N/A'] else value
                        elif issue=='enum_case':normalized=value.lower()
                        elif issue=='outer_whitespace':normalized=value.strip()
                        elif issue=='datetime_format':normalized=value[:10].replace('/','-').replace('.','-')+'T'+value[11:]
                        check(normalized==clean[n],'independent_normalization',c['case_id'])
        for c in labels:
            f=c['input_file'];num=c['record_number'];col=c['column']
            check(num==0 and col in headers[f] or (f,num) in locations,'case_location_exists',c['case_id'])
            if num==0:check(c['observed_value']==col,'header_observed',c['case_id'])
            if split=='development':check(c['pattern_group']=='seen','no_unseen_development',c['case_id'])
        action_counts=dict(Counter(x['expected_action'] for x in labels))
        check(action_counts==meta['splits'][split]['actions'],'summary_counts',split)
        check(set(action_counts)=={'map_column','normalize','review','keep'},'action_coverage',split)
        reports[split]={'input_rows':sum(len(v) for v in files.values()),'cases':len(labels),'actions':action_counts,'changed_cells_excluding_duplicate_rows':changes,'unseen_cases':sum(c['pattern_group']=='unseen' for c in labels)}
    for table in schema['tables']:
        pk=schema['tables'][table]['fields'][0]['name']
        a={r[pk] for r in baselines['development'][table]};b={r[pk] for r in baselines['evaluation'][table]}
        if table in ['categories','products','product_variants']:
            check(baselines['development'][table]==baselines['evaluation'][table],'shared_catalogue',table)
        else:check(not a.intersection(b),'split_disjoint',table)
    return {'passed':not errors,'checks':checks,'errors':errors,'splits':reports,'note':'Dataset construction validation, not cleaning-service performance.'}

if __name__=='__main__':
    result=validate(sys.argv[1],sys.argv[2]);target=Path(sys.argv[2])/'reports/dirty-validation.json';target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False,indent=2));sys.exit(0 if result['passed'] else 1)
