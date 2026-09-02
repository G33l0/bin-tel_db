"""Offline provider backed by published IIN range allocations."""

from __future__ import annotations

from providers.base import BaseProvider, ProviderResponse
from utils.validation import network_from_iin


class OfflineIinRangeProvider(BaseProvider):
    """Derives the card network from the numbering plan. No network calls.

    Only reports what the ISO/IEC 7812 range allocations actually determine -
    the network - and leaves every issuer-specific field unset.
    """

    type_name = "offline_iin_ranges"

    def fetch(self, bin_value: str) -> ProviderResponse:
        network = network_from_iin(bin_value)
        if not network:
            return self.not_found(bin_value)
        return self.found(bin_value, {"network": network})
