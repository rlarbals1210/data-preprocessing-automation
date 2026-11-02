import csv
import json
from pathlib import Path
import tempfile
import unittest
from diagnostics.cleaning import clean


class CleaningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'input';self.source.mkdir();self.out=self.root/'output'
    def tearDown(self):self.tmp.cleanup()
    def write(self,columns,rows):
        p=self.source/'sample.csv'
        with p.open('w',newline='',encoding='utf-8') as f:
            w=csv.writer(f);w.writerow(columns);w.writerows(rows)
        return p
    def cfg(self,columns,**extra):return {'tables':{'sample':{'columns':columns,**extra}}}
    def test_audit_preserves_original_and_untouched_cells_and_null(self):
        p=self.write(['id','amount','phone','note'],[['001','1,200 원','NULL','line1\nline2'],['002','0','010-12',' unchanged ']])
        original=p.read_bytes()
        config=self.cfg({'id':{},'amount':{'type':'integer','min':0,'standardization':'krw_integer'},'phone':{'pattern':'010-[0-9]{4}-[0-9]{4}','missing_values':['NULL']},'note':{}})
        r=clean(self.source,config,self.out)
        self.assertEqual(p.read_bytes(),original);self.assertEqual(r['changed_cells'],2)
        with (self.out/'data/sample.csv').open(newline='') as f:rows=list(csv.DictReader(f))
        self.assertEqual(rows[0],{'id':'001','amount':'1200','phone':'','note':'line1\nline2'})
        self.assertEqual(rows[1]['phone'],'010-12');self.assertEqual(rows[1]['note'],' unchanged ')
        changes=json.loads((self.out/'changes.json').read_text())
        null=next(x for x in changes if x['column']=='phone')
        self.assertIsNone(null['logical_after']);self.assertEqual(null['before'],'NULL')
        self.assertEqual(null['source_line_end'],3)
        r2=clean(self.out/'data',config,self.root/'again')
        self.assertEqual(r2['changed_cells'],0)
        self.assertEqual((self.out/'data/sample.csv').read_bytes(),(self.root/'again/data/sample.csv').read_bytes())
    def test_multiple_events_one_cell_only_one_change(self):
        self.write(['n'],[[' 1,200 원 ']])
        r=clean(self.source,self.cfg({'n':{'type':'integer','strip':True,'standardization':'krw_integer'}}),self.out)
        self.assertEqual(r['changed_cells'],1)
        change=json.loads((self.out/'changes.json').read_text())[0]
        self.assertEqual(set(change['codes']),{'type','outer_whitespace'})
    def test_new_duplicate_prevents_publication(self):
        self.write(['name'],[['a'],[' a ']])
        with self.assertRaisesRegex(ValueError,'introduced problems'):
            clean(self.source,self.cfg({'name':{'strip':True}}),self.out)
        self.assertFalse((self.out/'data').exists())
        self.assertEqual(json.loads((self.out/'manifest.json').read_text())['status'],'failed')
    def test_new_conditional_required_prevents_publication(self):
        self.write(['n','detail'],[['1,000','']])
        config=self.cfg({'n':{'type':'integer','standardization':'krw_integer'},'detail':{'required_if':{'column':'n','in':['1000']}}})
        with self.assertRaisesRegex(ValueError,'introduced problems'):clean(self.source,config,self.out)
        self.assertFalse((self.out/'data').exists())
    def test_unique_key_candidate_held(self):
        p=self.write(['id'],[[' A '],['a']])
        r=clean(self.source,self.cfg({'id':{'strip':True,'casefold':True}},unique=[['id']]),self.out)
        self.assertEqual(r['changed_cells'],0);self.assertGreater(r['held_candidate_events'],0)
        with (self.out/'data/sample.csv').open(newline='') as f:self.assertEqual(list(csv.reader(f))[1],[' A '])
    def test_existing_or_nested_output_refused(self):
        self.write(['x'],[['ok']]);cfg=self.cfg({'x':{}})
        for dest in [self.source/'out',self.source,self.root]:
            with self.assertRaises(ValueError):clean(self.source,cfg,dest)
        self.assertFalse((self.source/'out').exists())
    def test_incomplete_source_produces_no_output(self):
        self.write(['x'],[['ok']])
        with self.assertRaisesRegex(ValueError,'incomplete'):clean(self.source,self.cfg({'absent':{}}),self.out)
        self.assertFalse(self.out.exists())
    def test_semicolon_and_bom_are_read_explicitly(self):
        (self.source/'sample.csv').write_text('id;name\n001; hello \n',encoding='utf-8-sig')
        r=clean(self.source,self.cfg({'id':{},'name':{'strip':True}}),self.out,delimiter=';')
        self.assertEqual(r['changed_cells'],1)
        self.assertEqual((self.out/'data/sample.csv').read_text(),'id,name\n001,hello\n')

if __name__=='__main__':unittest.main()
