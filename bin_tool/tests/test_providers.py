import os
import tempfile
import unittest

from tests import _context  # noqa: F401
from database.database import Database
from providers.base import build_provider, build_providers
from providers.local_provider import LocalDatasetProvider
from providers.offline_provider import OfflineIinRangeProvider
from providers.public_provider import HttpJsonProvider, extract_path


class OfflineProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = OfflineIinRangeProvider({"name": "offline", "enabled": True})

    def test_reports_only_the_network(self):
        response = self.provider.lookup("411111")
        self.assertTrue(response.ok)
        self.assertEqual(response.fields, {"network": "Visa"})

    def test_unallocated_prefix_is_not_found(self):
        self.assertEqual(self.provider.lookup("999999").status, "not_found")


class LocalProviderTests(unittest.TestCase):
    def setUp(self):
        path = os.path.join(tempfile.mkdtemp(prefix="bintel_"), "test.sqlite3")
        self.db = Database(path)
        self.db.connect()
        self.provider = LocalDatasetProvider(
            {"name": "local", "enabled": True}, {"database": self.db}
        )

    def tearDown(self):
        self.db.close()

    def test_not_ready_without_a_dataset(self):
        self.assertIsNotNone(self.provider.check_ready())

    def test_returns_dataset_fields(self):
        self.db.import_dataset(
            [{"bin": "411111", "issuer": "Example Bank", "network": "Visa"}], "ds"
        )
        self.assertIsNone(self.provider.check_ready())
        response = self.provider.lookup("411111")
        self.assertEqual(response.fields["issuer"], "Example Bank")
        self.assertNotIn("currency", response.fields)  # unknown values are dropped

    def test_prefix_match_is_flagged(self):
        self.db.import_dataset([{"bin": "411111", "issuer": "Example Bank"}], "ds")
        response = self.provider.lookup("41111122")
        self.assertTrue(response.ok)
        self.assertIn("prefix", response.error or "")


class HttpProviderTests(unittest.TestCase):
    config = {
        "name": "api",
        "type": "http_json",
        "enabled": True,
        "base_url": "https://example.invalid/bins",
        "field_map": {"issuer": ["bank.name"], "network": ["scheme"], "prepaid": ["prepaid"]},
    }

    def test_extract_path_handles_nesting_and_lists(self):
        payload = {"bank": {"name": "Example"}, "items": [{"id": 7}]}
        self.assertEqual(extract_path(payload, "bank.name"), "Example")
        self.assertEqual(extract_path(payload, "items.0.id"), 7)
        self.assertIsNone(extract_path(payload, "bank.missing"))

    def test_not_ready_without_base_url(self):
        provider = HttpJsonProvider({**self.config, "base_url": ""})
        self.assertIn("base_url", provider.check_ready())

    def test_not_ready_without_api_key_env(self):
        provider = HttpJsonProvider({**self.config, "api_key_env": "BIN_TEL_MISSING_KEY"})
        os.environ.pop("BIN_TEL_MISSING_KEY", None)
        self.assertIn("BIN_TEL_MISSING_KEY", provider.check_ready())

    def test_parses_and_normalises_payload(self):
        provider = HttpJsonProvider(self.config)
        response = provider._parse(
            "411111", '{"bank": {"name": "Example Bank"}, "scheme": "visa", "prepaid": false}'
        )
        self.assertTrue(response.ok)
        self.assertEqual(response.fields["network"], "visa")
        self.assertEqual(response.fields["prepaid"], "false")

    def test_empty_payload_is_not_found(self):
        provider = HttpJsonProvider(self.config)
        self.assertEqual(provider._parse("411111", "{}").status, "not_found")

    def test_invalid_json_is_an_error(self):
        provider = HttpJsonProvider(self.config)
        response = provider._parse("411111", "<html>nope</html>")
        self.assertEqual(response.status, "error")

    def test_url_template(self):
        provider = HttpJsonProvider(self.config)
        self.assertEqual(provider.build_url("411111"), "https://example.invalid/bins/411111")

    def test_lookup_never_raises(self):
        provider = HttpJsonProvider({**self.config, "base_url": "not-a-url"})
        self.assertEqual(provider.lookup("411111").status, "error")


class RegistryTests(unittest.TestCase):
    def test_only_enabled_and_known_types_are_built(self):
        config = {
            "providers": [
                {"name": "a", "type": "offline_iin_ranges", "enabled": True},
                {"name": "b", "type": "offline_iin_ranges", "enabled": False},
                {"name": "c", "type": "does_not_exist", "enabled": True},
            ]
        }
        providers = build_providers(config, {})
        self.assertEqual([p.name for p in providers], ["a"])

    def test_build_provider_ignores_enabled_but_not_unknown_types(self):
        disabled = build_provider({"name": "b", "type": "offline_iin_ranges", "enabled": False}, {})
        self.assertIsNotNone(disabled)
        self.assertFalse(disabled.enabled)
        self.assertIsNone(build_provider({"name": "c", "type": "nope", "enabled": True}, {}))


if __name__ == "__main__":
    unittest.main()
