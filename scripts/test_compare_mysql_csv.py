import csv
from collections import Counter
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('comparison', Path(__file__).with_name('compare_mysql_csv.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ComparisonTests(unittest.TestCase):
    def test_sensitive_values_and_duplicates(self):
        values = ['', 'NULL', ' NULL ', '0', '01234', '010-1234-5678', '가,나', '줄\n바꿈', 'é', 'e\u0301', 'x', 'x']
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sample.csv'
            with path.open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['값'])
                writer.writerows([[v] for v in values])
            headers, rows, positions, _ = module.read_csv(path)
        self.assertEqual(sum(rows.values()), len(values))
        diff = module.differences(rows, Counter(reversed(list(rows.elements()))), headers, positions)
        self.assertEqual(diff['csv_only_count'], 0)
        self.assertEqual(diff['db_only_count'], 0)
        for original, changed in [('', None), ('01234', '1234'), (' NULL ', 'NULL'), ('x', 'X'), ('é', 'e\u0301')]:
            with self.subTest(original=original, changed=changed):
                mutated = rows.copy()
                row = (original.encode(),)
                mutated[row] -= 1
                mutated[(None if changed is None else changed.encode(),)] += 1
                diff = module.differences(rows, mutated, headers, positions)
                self.assertEqual((diff['csv_only_count'], diff['db_only_count']), (1, 1))
        mutated = rows.copy()
        mutated[(b'x',)] += 1
        self.assertEqual(module.differences(rows, mutated, headers, positions)['db_only_count'], 1)

    def test_corrupt_csv_is_rejected(self):
        for content in ['a,a\n1,2\n', 'a,b\n1\n', 'a,b\n"unfinished']:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'sample.csv'
                path.write_text(content)
                with self.assertRaises((ValueError, csv.Error)):
                    module.read_csv(path)

    def test_same_column_counts_do_not_hide_wrong_names(self):
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, *args): pass
            def fetchone(self): return ('InnoDB',)
            def fetchall(self): return [('wrong', 'text')]
        class Connection:
            def cursor(self): return Cursor()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sample.csv'
            path.write_text('right\nvalue\n')
            result = module.compare_table(Connection(), 'shop_raw', path)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['csv_only_columns'], ['right'])

    def test_missing_csv_and_unexpected_file_are_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / 'unexpected.csv').write_text('a\nx\n')
            report = module.run_comparison(None, 'shop_raw', path, ['missing'])
        self.assertEqual(report['status'], 'error')
        self.assertEqual(report['unexpected_csv_files'], ['unexpected'])

    def test_identifiers_are_escaped(self):
        self.assertEqual(module.identifier('고객`번호'), '`고객``번호`')

    def test_table_adapter_preserves_nulls_and_column_mapping(self):
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, query, args=None): self.query = query
            def fetchone(self): return ('InnoDB',)
            def fetchall(self):
                if 'information_schema.COLUMNS' in self.query:
                    return [('phone', 'text'), ('id', 'text')]
                self.assert_projection()
                return [(b'002', b''), (b'001', b'010-1234'), (b'001', b'010-1234')]
            def assert_projection(self):
                assert self.query.index('`id`') < self.query.index('`phone`')
                assert 'AS BINARY' in self.query
        class Connection:
            def cursor(self): return Cursor()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sample.csv'
            path.write_text('id,phone\n001,010-1234\n001,010-1234\n002,\n')
            result = module.compare_table(Connection(), 'shop_raw', path)
        self.assertEqual(result['status'], 'match')
        self.assertEqual(result['db_rows'], 3)


if __name__ == '__main__':
    unittest.main()
