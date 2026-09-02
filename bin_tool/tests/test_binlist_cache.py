import os
import tempfile
import unittest

from tests import _context
from database.database import Database
from providers.base import build_provider
from providers.public_provider import BinlistProvider


class BinlistProviderTests(unittest.TestCase):
    def test_defaults_make_it_ready_without_config(self):
        provider = BinlistProvider({"name": "binlist", "enabled": True}, {})
        self.assertIsNone(provider.check_ready())
        self.assertEqual(provider.build_url("45717360"), "https://lookup.binlist.net/45717360")
        self.assertEqual(provider.headers.get("Accept-Version"), "3")

    def test_registered_under_its_type(self):
        provider = build_provider({"name": "binlist", "type": "binlist", "enabled": True}, {})
        self.assertIsInstance(provider, BinlistProvider)

    def test_parses_binlist_payload(self):
        provider = BinlistProvider({"name": "binlist", "enabled": True}, {})
        body = (
            '{"scheme":"visa","type":"debit","brand":"Visa/Dankort","prepaid":false,'
            '"country":{"alpha2":"DK","name":"Denmark","currency":"DKK"},'
            '"bank":{"name":"Jyske Bank","url":"www.jyskebank.dk","phone":"+4589893300"}}'
        )
        response = provider._parse("45717360", body)
        self.assertTrue(response.ok)
        self.assertEqual(response.fields["network"], "Visa")
        self.assertEqual(response.fields["issuer"], "Jyske Bank")
        self.assertEqual(response.fields["country_code"], "DK")
        self.assertEqual(response.fields["currency"], "DKK")
        self.assertEqual(response.fields["prepaid"], "false")

    def test_user_config_overrides_defaults(self):
        provider = BinlistProvider(
            {"name": "binlist", "enabled": True, "rate_limit_per_second": 1.0}, {}
        )
        self.assertEqual(provider.limiter.interval, 1.0)


class ResponseCacheTests(unittest.TestCase):
    def setUp(self):
        path = os.path.join(tempfile.mkdtemp(prefix="bintel_"), "test.sqlite3")
        self.db = Database(path)
        self.db.connect()

    def tearDown(self):
        self.db.close()

    def test_put_get_roundtrip(self):
        self.db.cache_put("binlist", "411111", "found", {"network": "Visa"})
        cached = self.db.cache_get("binlist", "411111")
        self.assertEqual(cached["status"], "found")
        self.assertEqual(cached["fields"]["network"], "Visa")

    def test_miss_returns_none(self):
        self.assertIsNone(self.db.cache_get("binlist", "000000"))

    def test_clear_by_provider(self):
        self.db.cache_put("binlist", "411111", "found", {"network": "Visa"})
        self.db.cache_put("other", "411111", "found", {"network": "Visa"})
        self.assertEqual(self.db.clear_cache("binlist"), 1)
        self.assertIsNone(self.db.cache_get("binlist", "411111"))
        self.assertIsNotNone(self.db.cache_get("other", "411111"))

    def test_cached_lookup_skips_network_and_rate_limit(self):
        calls = {"n": 0}

        class Recording(BinlistProvider):
            def _send(self, url):
                calls["n"] += 1
                return 200, '{"scheme":"visa","bank":{"name":"Example"}}', None

        provider = Recording(
            {"name": "binlist", "enabled": True, "rate_limit_per_second": 0}, {"database": self.db}
        )
        first = provider.lookup("411111")
        second = provider.lookup("411111")
        self.assertTrue(first.ok and second.ok)
        self.assertEqual(second.fields["issuer"], "Example")
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
