import unittest

from tests import _context
from utils.validation import (
    clean_boolean,
    clean_country_code,
    clean_currency,
    normalize_bin,
    normalize_comparable,
    network_from_iin,
)


class NormalizeBinTests(unittest.TestCase):
    def test_accepts_six_and_eight_digits(self):
        self.assertEqual(normalize_bin("411111").value, "411111")
        self.assertEqual(normalize_bin("4111-1111").value, "41111111")

    def test_strips_separators_and_whitespace(self):
        self.assertEqual(normalize_bin("  4111 11 ").value, "411111")

    def test_rejects_full_card_numbers(self):
        result = normalize_bin("4111111111111111")
        self.assertFalse(result.ok)
        self.assertIn("full card number", result.reason)

    def test_rejects_unsupported_lengths(self):
        self.assertFalse(normalize_bin("4111").ok)
        self.assertFalse(normalize_bin("4111111").ok)

    def test_rejects_empty_and_non_numeric(self):
        self.assertFalse(normalize_bin("").ok)
        self.assertFalse(normalize_bin("abcdef").ok)

    def test_custom_allowed_lengths(self):
        self.assertTrue(normalize_bin("4111111", allowed_lengths=[7]).ok)


class NetworkTests(unittest.TestCase):
    def test_known_ranges(self):
        cases = {
            "411111": "Visa",
            "512345": "Mastercard",
            "222100": "Mastercard",
            "343434": "American Express",
            "352800": "JCB",
            "360000": "Diners Club",
            "601100": "Discover",
            "650000": "Discover",
            "620000": "UnionPay",
            "500000": "Maestro",
        }
        for bin_value, expected in cases.items():
            self.assertEqual(network_from_iin(bin_value), expected, bin_value)

    def test_unallocated_and_ambiguous_return_none(self):
        self.assertIsNone(network_from_iin("999999"))
        self.assertIsNone(network_from_iin("622200"))


class CleanerTests(unittest.TestCase):
    def test_comparable_ignores_case_and_punctuation(self):
        self.assertEqual(
            normalize_comparable("Example  Bank, N.A."), normalize_comparable("example bank na")
        )

    def test_unknown_markers_compare_as_empty(self):
        self.assertEqual(normalize_comparable("unknown"), "")
        self.assertEqual(normalize_comparable(None), "")

    def test_country_currency_and_boolean(self):
        self.assertEqual(clean_country_code("us"), "US")
        self.assertEqual(clean_country_code("USA"), "unknown")
        self.assertEqual(clean_currency("usd"), "USD")
        self.assertEqual(clean_boolean(True), "true")
        self.assertEqual(clean_boolean("No"), "false")
        self.assertEqual(clean_boolean("maybe"), "unknown")


if __name__ == "__main__":
    unittest.main()
