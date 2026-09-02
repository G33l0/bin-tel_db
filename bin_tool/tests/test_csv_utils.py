import json
import os
import tempfile
import unittest

from tests import _context  # noqa: F401
from utils.csv_utils import (
    export_rows,
    list_files,
    read_bin_file,
    read_dataset_file,
)


class InputReadingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="bintel_")

    def write(self, name, content):
        path = os.path.join(self.directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def test_csv_with_named_bin_column(self):
        path = self.write("bins.csv", "note,bin\nfirst,411111\nsecond,522222\n")
        self.assertEqual([e.value for e in read_bin_file(path)], ["411111", "522222"])

    def test_csv_without_header_uses_first_column(self):
        path = self.write("plain.csv", "411111,x\n522222,y\n")
        self.assertEqual([e.value for e in read_bin_file(path)], ["411111", "522222"])

    def test_txt_one_per_line_with_comments(self):
        path = self.write("bins.txt", "# comment\n411111\n\n522222\n")
        self.assertEqual([e.value for e in read_bin_file(path)], ["411111", "522222"])

    def test_txt_comma_separated(self):
        path = self.write("inline.txt", "411111, 522222\n")
        self.assertEqual([e.value for e in read_bin_file(path)], ["411111", "522222"])

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            read_bin_file(os.path.join(self.directory, "nope.csv"))

    def test_list_files_filters_by_extension(self):
        self.write("a.csv", "bin\n411111\n")
        self.write("b.md", "ignore me")
        self.assertEqual([os.path.basename(p) for p in list_files(self.directory)], ["a.csv"])


class DatasetReadingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="bintel_")

    def test_aliases_are_mapped(self):
        path = os.path.join(self.directory, "dataset.csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("iin,bank,scheme,type,countrycode\n411111,Example Bank,Visa,credit,US\n")
        rows = read_dataset_file(path)
        self.assertEqual(rows[0]["bin"], "411111")
        self.assertEqual(rows[0]["issuer"], "Example Bank")
        self.assertEqual(rows[0]["network"], "Visa")
        self.assertEqual(rows[0]["card_type"], "credit")
        self.assertEqual(rows[0]["country_code"], "US")
        self.assertEqual(rows[0]["currency"], "unknown")

    def test_missing_bin_column_raises(self):
        path = os.path.join(self.directory, "bad.csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("bank,scheme\nExample,Visa\n")
        with self.assertRaises(ValueError):
            read_dataset_file(path)


class ExportTests(unittest.TestCase):
    rows = [{"bin": "411111", "issuer": "O'Brien Bank", "network": "Visa", "status": "discovered"}]

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="bintel_")

    def test_csv_export_has_full_header(self):
        path = os.path.join(self.directory, "out.csv")
        self.assertEqual(export_rows(self.rows, path, "csv"), 1)
        with open(path, encoding="utf-8") as handle:
            header = handle.readline().strip().split(",")
        self.assertIn("confidence", header)
        self.assertEqual(header[0], "bin")

    def test_json_export_fills_unknown(self):
        path = os.path.join(self.directory, "out.json")
        export_rows(self.rows, path, "json")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data[0]["country"], "unknown")

    def test_sql_export_escapes_quotes(self):
        path = os.path.join(self.directory, "out.sql")
        export_rows(self.rows, path, "sql")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("O''Brien Bank", text)
        self.assertIn("CREATE TABLE IF NOT EXISTS bins", text)

    def test_unknown_format_raises(self):
        with self.assertRaises(ValueError):
            export_rows(self.rows, os.path.join(self.directory, "x.xml"), "xml")


if __name__ == "__main__":
    unittest.main()
