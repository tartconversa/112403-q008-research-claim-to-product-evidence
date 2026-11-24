"""检查合成样例的表结构和关联。"""
import csv
from pathlib import Path
import unittest

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
HEADERS = {'studies': ['study', 'material', 'concentration', 'condition', 'result', 'version'], 'products': ['product', 'formula', 'material', 'batch', 'from', 'to'], 'claims': ['claim', 'product', 'formula', 'study', 'channel', 'valid_to', 'state']}
REFERENCES = [('claims', 'product', 'products', 'product'), ('claims', 'study', 'studies', 'study'), ('products', 'material', 'studies', 'material')]


class SampleInputs(unittest.TestCase):
    """验证提供的样例文件。"""

    def read(self, name):
        with (SAMPLES / f"{name}.csv").open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source, strict=True)
            self.assertEqual(reader.fieldnames, HEADERS[name])
            rows = list(reader)
        self.assertTrue(rows)
        for row in rows:
            self.assertNotIn(None, row)
            self.assertNotIn(None, row.values())
        return rows

    def test_documented_columns(self):
        for name in HEADERS:
            with self.subTest(name=name):
                self.read(name)

    def test_example_references(self):
        for source, source_field, target, target_field in REFERENCES:
            with self.subTest(source=source, field=source_field, target=target):
                known = {row[target_field] for row in self.read(target)}
                observed = {row[source_field] for row in self.read(source) if row[source_field]}
                self.assertTrue(observed)
                self.assertEqual(observed - known, set())


if __name__ == "__main__":
    unittest.main()
