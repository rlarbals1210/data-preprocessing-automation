import csv
from pathlib import Path
import tempfile
import unittest
from diagnostics.engine import diagnose, ConfigError, validate_config
from diagnostics.classification import markdown


class ClassificationTests(unittest.TestCase):
    def run_report(self, columns, rows, **extra):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'other.csv'
            with p.open('w', newline='') as f:
                w=csv.writer(f);w.writerow(columns);w.writerows(rows)
            before=p.read_bytes()
            report=diagnose(p, {'tables': {'other': {'columns': columns, **extra}}})
            self.assertEqual(before,p.read_bytes())
            self.assertEqual(sum(report['summary']['category_counts'].values()),len(report['issues']))
            return report

    def test_optional_and_required_blanks(self):
        r=self.run_report({'x': {'required': True}, 'y': {}}, [['','']])
        self.assertTrue(all(i['category']=='business_rule_violation' for i in r['issues'] if i['columns']==['x']))
        self.assertEqual(next(i for i in r['issues'] if i['columns']==['y'])['category'],'normal_observation')

    def test_conditional_missing_token_not_suggested_as_null(self):
        r=self.run_report({'state': {}, 'x': {'missing_values':['NULL'], 'required_if':{'column':'state','in':['done']}}}, [['done','NULL'],['open','NULL']])
        self.assertTrue(all(i['category']=='business_rule_violation' for i in r['issues'] if i['record']==1))
        i=next(i for i in r['issues'] if i['record']==2)
        self.assertEqual(i['category'],'standardization_candidate');self.assertIsNone(i['suggested_value'])

    def test_policy_is_required_and_amount_bound_still_checked(self):
        for policy in (False,True):
            rule={'type':'integer','min':1}
            if policy:rule['standardization']='krw_integer'
            r=self.run_report({'amount':rule}, [['1,200 원'],['0 원'],['1,2 원'],['0']])
            types={i['record']:i for i in r['issues'] if i['code']=='type'}
            self.assertEqual(types[1]['category'],'standardization_candidate' if policy else 'human_review')
            if policy:self.assertEqual(types[1]['suggested_value'],'1200')
            self.assertEqual(types[2]['category'],'human_review')
            self.assertEqual(types[3]['category'],'human_review')
            self.assertEqual(next(i for i in r['issues'] if i['record']==4)['category'],'business_rule_violation')

    def test_dates_and_phone_never_guessed(self):
        columns={'d':{'type':'datetime','standardization':'iso_datetime'},'p':{'type':'string','pattern':'010-[0-9]{4}-[0-9]{4}','standardization':'kr_mobile'}}
        r=self.run_report(columns,[['2026/06/01 09:00:00+09:00','(010) 1234-5678'],['07/08/26 09:30','010-123'],['2026/02/30 09:00:00+09:00','0'],['2026/06/01 09:00:00','010-1234-5678']])
        self.assertTrue(all(i['category']=='standardization_candidate' for i in r['issues'] if i['record']==1))
        self.assertTrue(all(i['category']=='human_review' for i in r['issues'] if i['record']>1))

    def test_candidate_must_pass_other_constraints(self):
        r=self.run_report({'x':{'type':'integer','standardization':'krw_integer','pattern':'[0-9]{2}'}}, [['1,000']])
        self.assertTrue(all(i['category']=='human_review' for i in r['issues']))

    def test_duplicate_group_distinguishes_conflict_and_identical(self):
        r=self.run_report({'id':{},'value':{}}, [['a','x'],['a','x'],['b','y'],['b','z']],unique=[['id']])
        keys=[g for g in r['duplicate_groups'] if g['code']=='unique']
        self.assertEqual([g['kind'] for g in keys],['identical','conflicting'])
        self.assertEqual(keys[1]['differing_columns'],['value'])
        self.assertTrue(all(i['category']=='business_rule_violation' for i in r['issues'] if i['code']=='unique'))
        self.assertTrue(all(i['category']=='human_review' for i in r['issues'] if i['code']=='duplicate_row'))

    def test_case_and_whitespace_need_explicit_policy(self):
        r=self.run_report({'x':{'casefold':True,'allowed':['yes']},'y':{'strip':True},'z':{}},[['YES',' ok ',' ok ']])
        by_col={i['columns'][0]:i for i in r['issues']}
        self.assertEqual(by_col['x']['suggested_value'],'yes')
        self.assertEqual(by_col['y']['suggested_value'],'ok')
        self.assertEqual(by_col['z']['category'],'human_review')

    def test_unknown_policy_or_incompatible_type_rejected(self):
        for rule in [{'standardization':'guess'}, {'type':'string','standardization':'krw_integer'}, {'standardization':[]}]:
            with self.assertRaises(ConfigError):validate_config({'tables':{'t':{'columns':{'x':rule}}}})

    def test_markdown_has_four_groups_and_escapes_input(self):
        r=self.run_report({'x':{}}, [[' <script>|\n ']])
        text=markdown(r)
        for label in ['정상 관측','표준화 후보','업무 규칙 위반','사람 검토 필요']:self.assertIn('## '+label,text)
        self.assertNotIn('<script>',text);self.assertIn('&#124;',text)

if __name__=='__main__':unittest.main()
