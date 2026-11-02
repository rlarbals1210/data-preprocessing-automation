import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diagnostics.engine import ConfigError, diagnose, validate_config


class DiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, headers, rows):
        path = self.root / (name + '.csv')
        with path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(headers)
            writer.writerows(rows)
        return path

    def config(self, columns, **extra):
        return {'version': 1, 'tables': {'sample': {'columns': columns, **extra}}}

    def codes(self, report):
        return [(i['record'], i['code']) for i in report['issues']]

    def test_profile_does_not_call_zero_missing_or_change_source(self):
        path = self.write('sample', ['any_column'], [['0'], ['NULL'], [' 없음 '], [''], ['   '], ['001']])
        before = path.read_bytes()
        report = diagnose(path)
        self.assertEqual(report['status'], 'completed')
        self.assertNotIn((1, 'missing_candidate'), self.codes(report))
        self.assertEqual(report['summary']['severity_counts'], {'info': 6})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(report['tables']['sample']['sha256'], hashlib.sha256(before).hexdigest())

    def test_column_specific_missing_and_zero_bounds(self):
        self.write('sample', ['phone', 'discount'], [['0', '0'], ['NULL', '0']])
        config = self.config({'phone': {'required': True, 'missing_values': ['0', 'NULL']},
                              'discount': {'required': True, 'type': 'integer', 'min': 0}})
        report = diagnose(self.root, config)
        violations = [i for i in report['issues'] if i['severity'] == 'error']
        self.assertEqual([i['columns'] for i in violations], [['phone'], ['phone']])

    def test_numeric_date_and_format_errors(self):
        self.write('sample', ['n', 'day', 'code'], [['-1', '2026-02-30', 'AB'],
                                                 ['1,000', '2026-10-01', '012'],
                                                 ['0', '2026-10-01', '123']])
        report = diagnose(self.root, self.config({'n': {'type': 'integer', 'min': 0},
                           'day': {'type': 'date'}, 'code': {'pattern': '[0-9]{3}'}}))
        self.assertIn((1, 'min'), self.codes(report))
        self.assertIn((1, 'type'), self.codes(report))
        self.assertIn((1, 'pattern'), self.codes(report))
        self.assertIn((2, 'type'), self.codes(report))
        self.assertFalse(any(i['record'] == 3 for i in report['issues']))

    def test_composite_unique_flags_all_rows(self):
        self.write('sample', ['a', 'b'], [['x', '1'], ['x', '2'], ['x', '1']])
        report = diagnose(self.root, self.config({'a': {}, 'b': {}}, unique=[['a', 'b']]))
        self.assertEqual([i['record'] for i in report['issues'] if i['code'] == 'unique'], [1, 3])
        self.assertEqual(report['tables']['sample']['duplicate_row_excess'], 1)

    def test_conditional_required_and_approved_normalization(self):
        self.write('sample', ['state', 'closed'], [[' OPEN ', ''], ['CLOSED', ''], ['closed', '2026-10-01']])
        config = self.config({'state': {'strip': True, 'casefold': True, 'allowed': ['open', 'closed']},
                              'closed': {'required_if': {'column': 'state', 'in': ['closed']}, 'type': 'date'}})
        report = diagnose(self.root, config)
        self.assertEqual([i['record'] for i in report['issues'] if i['code'] == 'required'], [2])
        self.assertFalse(any(i['code'] == 'allowed' for i in report['issues']))

    def test_cross_table_reference_and_optional_empty(self):
        self.write('parent', ['site', 'id'], [['A', '01']])
        self.write('sample', ['site', 'id'], [['A', '01'], ['A', '02'], ['A', '']])
        config = self.config({'site': {}, 'id': {}}, references=[{
            'columns': ['site', 'id'], 'table': 'parent', 'target_columns': ['site', 'id']}])
        config['tables']['parent'] = {'columns': {'site': {}, 'id': {}}}
        report = diagnose(self.root, config)
        self.assertEqual([i['record'] for i in report['issues'] if i['code'] == 'reference'], [2])

    def test_unavailable_reference_is_incomplete_not_orphan_flood(self):
        self.write('sample', ['id'], [['1']])
        config = self.config({'id': {}}, references=[{
            'columns': ['id'], 'table': 'parent', 'target_columns': ['id']}])
        config['tables']['parent'] = {'columns': {'id': {}}}
        report = diagnose(self.root, config)
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(len(report['skipped_rules']), 1)
        self.assertFalse(any(i['code'] == 'reference' for i in report['issues']))

    def test_multiline_location_and_original_unicode(self):
        path = self.write('sample', ['text'], [['가\n나'], ['  é ']])
        report = diagnose(path)
        issue = next(i for i in report['issues'] if i['code'] == 'outer_whitespace')
        self.assertEqual((issue['record'], issue['line_start'], issue['line_end']), (2, 4, 4))
        self.assertEqual(issue['values']['text'], '  é ')

    def test_bad_csv_does_not_pass(self):
        for text in ['x,x\n1,2', 'x,y\n1', 'x\n"unfinished', '']:
            with self.subTest(text=text):
                path = self.root / 'sample.csv'
                path.write_text(text)
                report = diagnose(path)
                self.assertEqual(report['status'], 'incomplete')
                self.assertEqual(report['summary']['tables_read'], 0)

    def test_invalid_config_rejected(self):
        configs = [self.config({'x': {'requried': True}}), self.config({'x': {'required': 'yes'}}),
                   self.config({'x': {'pattern': '['}}), self.config({'x': {'type': 'integer', 'min': 'NaN'}}),
                   self.config({'x': {'type': 'integer', 'min': 3, 'max': 1}}),
                   self.config({'x': {}}, unique=[['absent']]), {'tables': []},
                   self.config({'x': {'type': 'date', 'formats': ['%Q']}}), {'version': True}]
        for config in configs:
            with self.subTest(config=config), self.assertRaises(ConfigError):
                validate_config(config)

    def test_missing_column_is_reported(self):
        self.write('sample', ['present'], [['ok']])
        report = diagnose(self.root, self.config({'absent': {'required': True}}))
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(report['errors'][0]['columns'], ['absent'])

    def test_header_only_and_custom_delimiter(self):
        path = self.root / 'sample.csv'
        path.write_text('id;name\n', encoding='utf-8-sig')
        report = diagnose(path, delimiter=';')
        self.assertEqual(report['summary']['rows_read'], 0)
        self.assertEqual(report['tables']['sample']['column_count'], 2)

    def test_nan_and_infinity_are_not_valid_decimals(self):
        self.write('sample', ['n'], [['NaN'], ['Infinity'], ['1e3'], ['0.1']])
        report = diagnose(self.root, self.config({'n': {'type': 'decimal', 'min': 0, 'max': 1}}))
        self.assertEqual([i['record'] for i in report['issues'] if i['code'] == 'type'], [1, 2, 3])


if __name__ == '__main__':
    unittest.main()
