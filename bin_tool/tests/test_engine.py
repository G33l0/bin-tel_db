import unittest

from tests import _context  # noqa: F401
from database.models import Status
from engine import coverage_confidence, reconcile
from providers.base import ProviderResponse


def response(provider, bin_value="411111", status="found", **fields):
    return ProviderResponse(provider, bin_value, status, dict(fields))


class ReconcileTests(unittest.TestCase):
    def test_agreeing_providers_produce_a_discovered_record(self):
        outcome = reconcile(
            "411111",
            [
                response("a", issuer="Example Bank", network="Visa", country_code="US"),
                response("b", issuer="example bank", network="Visa", card_type="credit"),
            ],
        )
        self.assertEqual(outcome.record["status"], Status.DISCOVERED)
        self.assertEqual(outcome.record["issuer"], "Example Bank")
        self.assertEqual(outcome.conflicts, [])
        self.assertGreater(outcome.record["confidence"], 0.35)

    def test_conflicting_field_is_not_stored(self):
        outcome = reconcile(
            "522222",
            [
                response("a", "522222", issuer="Bank A", network="Mastercard"),
                response("b", "522222", issuer="Bank B", network="Mastercard"),
            ],
        )
        self.assertEqual(outcome.record["status"], Status.UNCONFIRMED)
        self.assertEqual(outcome.record["issuer"], "unknown")
        self.assertEqual(outcome.record["network"], "Mastercard")
        self.assertEqual(len(outcome.conflicts), 1)
        self.assertIn("Bank A", outcome.conflicts[0])

    def test_missing_required_field_stays_unconfirmed(self):
        outcome = reconcile("343434", [response("a", "343434", network="American Express")])
        self.assertEqual(outcome.record["status"], Status.UNCONFIRMED)

    def test_low_confidence_stays_unconfirmed(self):
        outcome = reconcile(
            "343434",
            [response("a", "343434", network="American Express", issuer="Example")],
            min_confidence=0.9,
        )
        self.assertEqual(outcome.record["status"], Status.UNCONFIRMED)

    def test_not_found_everywhere_is_invalid(self):
        outcome = reconcile("999999", [response("a", "999999", status="not_found")])
        self.assertEqual(outcome.record["status"], Status.INVALID)
        self.assertEqual(outcome.record["confidence"], 0.0)

    def test_all_errors_is_error(self):
        failed = ProviderResponse("a", "411111", "error", {}, error="timeout")
        self.assertEqual(reconcile("411111", [failed]).record["status"], Status.ERROR)

    def test_unreachable_provider_is_not_reported_as_invalid(self):
        failed = ProviderResponse("api", "777777", "error", {}, error="connection refused")
        missing = ProviderResponse("offline", "777777", "not_found", {})
        self.assertEqual(reconcile("777777", [missing, failed]).record["status"], Status.ERROR)

    def test_unreported_fields_are_unknown_not_guessed(self):
        outcome = reconcile("411111", [response("a", network="Visa")])
        for name in ("issuer", "country", "currency", "issuer_phone"):
            self.assertEqual(outcome.record[name], "unknown")

    def test_two_providers_score_higher_than_one(self):
        fields = dict(issuer="Example Bank", network="Visa")
        single = reconcile("411111", [response("a", **fields)]).record["confidence"]
        double = reconcile("411111", [response("a", **fields), response("b", **fields)]).record[
            "confidence"
        ]
        self.assertGreater(double, single)


class CoverageTests(unittest.TestCase):
    def test_more_fields_scores_higher(self):
        self.assertGreater(
            coverage_confidence({"network": "Visa", "issuer": "X", "country_code": "US"}),
            coverage_confidence({"network": "Visa"}),
        )


if __name__ == "__main__":
    unittest.main()
