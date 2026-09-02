"""Provider that answers from a reference dataset imported into SQLite."""

from __future__ import annotations

from typing import Any, Dict, Optional

from database.models import METADATA_FIELDS
from providers.base import BaseProvider, ProviderResponse


class LocalDatasetProvider(BaseProvider):
    """Looks BINs up in the ``dataset_bins`` table (menu option [3])."""

    type_name = "local_dataset"

    def __init__(self, config: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config, context)
        self.database = (self.context or {}).get("database")

    def check_ready(self) -> Optional[str]:
        if self.database is None:
            return "no database handle available"
        if not self.database.dataset_names():
            return "no reference dataset imported yet (menu option [3])"
        return None

    def fetch(self, bin_value: str) -> ProviderResponse:
        if self.database is None:
            return self.failed(bin_value, "no database handle available")
        row = self.database.lookup_dataset(bin_value)
        if not row:
            return self.not_found(bin_value)
        payload = {name: row.get(name) for name in METADATA_FIELDS}
        response = self.found(bin_value, payload)
        if response.ok and row.get("bin") != bin_value:
            # Matched on a shorter prefix; keep that visible in the audit trail.
            response.error = f"matched dataset prefix {row.get('bin')}"
        return response
