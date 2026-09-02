import os
import tempfile
import threading
import unittest

from tests import _context
from database.database import Database
from database.models import Status


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(prefix="bintel_"), "test.sqlite3")
        self.db = Database(self.path)
        self.db.connect()

    def tearDown(self):
        self.db.close()

    def test_upsert_is_idempotent_and_updates(self):
        self.db.upsert_bin({"bin": "411111", "network": "Visa", "status": Status.UNCONFIRMED})
        self.db.upsert_bin(
            {"bin": "411111", "network": "Visa", "issuer": "Example", "status": Status.DISCOVERED}
        )
        self.assertEqual(self.db.count_bins(), 1)
        record = self.db.get_bin("411111")
        self.assertEqual(record["status"], Status.DISCOVERED)
        self.assertEqual(record["issuer"], "Example")

    def test_defaults_are_unknown(self):
        self.db.upsert_bin({"bin": "411111", "status": Status.UNCONFIRMED})
        self.assertEqual(self.db.get_bin("411111")["country"], "unknown")
        self.assertEqual(self.db.get_bin("411111")["bin_length"], 6)

    def test_conflicts_are_stored_as_text(self):
        self.db.upsert_bin(
            {"bin": "411111", "status": Status.UNCONFIRMED, "conflicts": ["issuer (a=X, b=Y)"]}
        )
        self.assertIn("issuer", self.db.get_bin("411111")["conflicts"])

    def test_dataset_import_and_prefix_lookup(self):
        self.db.import_dataset([{"bin": "411111", "issuer": "Example", "network": "Visa"}], "ds")
        self.assertEqual(self.db.lookup_dataset("411111")["issuer"], "Example")
        self.assertEqual(self.db.lookup_dataset("41111199")["bin"], "411111")
        self.assertIsNone(self.db.lookup_dataset("999999"))

    def test_dataset_reimport_updates_in_place(self):
        self.db.import_dataset([{"bin": "411111", "issuer": "Old"}], "ds")
        self.db.import_dataset([{"bin": "411111", "issuer": "New"}], "ds")
        self.assertEqual(self.db.lookup_dataset("411111")["issuer"], "New")
        self.assertEqual(self.db.stats()["dataset_rows"], 1)

    def test_fetch_bins_filters_by_status(self):
        self.db.upsert_bin({"bin": "411111", "status": Status.DISCOVERED})
        self.db.upsert_bin({"bin": "522222", "status": Status.UNCONFIRMED})
        self.assertEqual(len(self.db.fetch_bins(Status.DISCOVERED)), 1)
        self.assertEqual(len(self.db.fetch_bins()), 2)

    def test_run_lifecycle_and_provider_results(self):
        run_id = self.db.start_run("validate", 2, "test.csv")
        self.db.record_provider_result(run_id, "411111", "offline", "found", {"network": "Visa"})
        self.db.finish_run(run_id, {"discovered": 1, "unconfirmed": 1, "invalid": 0, "errors": 0})
        run = self.db.recent_runs(1)[0]
        self.assertEqual(run["total"], 2)
        self.assertEqual(run["discovered"], 1)
        self.assertIsNotNone(run["finished_at"])

    def test_concurrent_writes(self):
        def write(start):
            for offset in range(50):
                value = str(400000 + start * 50 + offset)
                self.db.upsert_bin({"bin": value, "status": Status.DISCOVERED})

        threads = [threading.Thread(target=write, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.db.count_bins(), 200)

    def test_stats_shape(self):
        self.db.upsert_bin(
            {"bin": "411111", "status": Status.DISCOVERED, "network": "Visa", "confidence": 0.8}
        )
        stats = self.db.stats()
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["by_status"][Status.DISCOVERED], 1)
        self.assertAlmostEqual(stats["avg_confidence"], 0.8)


if __name__ == "__main__":
    unittest.main()
