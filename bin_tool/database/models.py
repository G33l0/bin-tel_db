"""Record shapes and SQLite schema for the BIN-TEL database."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from utils.validation import UNKNOWN

# Metadata fields resolved from providers / imported datasets.
METADATA_FIELDS: List[str] = [
    "issuer",
    "network",
    "card_type",
    "card_level",
    "country",
    "country_code",
    "currency",
    "prepaid",
    "commercial",
    "issuer_phone",
    "issuer_website",
]

# Full column order used for exports and for the ``bins`` table.
BIN_FIELDS: List[str] = (
    ["bin", "bin_length"]
    + METADATA_FIELDS
    + ["status", "confidence", "source", "checked_at"]
)

# Fields that carry the most weight when scoring confidence.
CORE_FIELDS = ("network", "issuer", "country_code", "card_type")

BOOLEAN_FIELDS = ("prepaid", "commercial")


class Status:
    DISCOVERED = "discovered"
    UNCONFIRMED = "unconfirmed"
    INVALID = "invalid"
    ERROR = "error"
    IMPORTED = "imported"


STATUS_LABELS = {
    Status.DISCOVERED: "DISCOVERED",
    Status.UNCONFIRMED: "SKIPPED - requires verification",
    Status.INVALID: "INVALID",
    Status.ERROR: "ERROR",
    Status.IMPORTED: "IMPORTED",
}


@dataclass
class BinRecord:
    """One row of the ``bins`` table."""

    bin: str
    bin_length: int = 0
    issuer: str = UNKNOWN
    network: str = UNKNOWN
    card_type: str = UNKNOWN
    card_level: str = UNKNOWN
    country: str = UNKNOWN
    country_code: str = UNKNOWN
    currency: str = UNKNOWN
    prepaid: str = UNKNOWN
    commercial: str = UNKNOWN
    issuer_phone: str = UNKNOWN
    issuer_website: str = UNKNOWN
    status: str = Status.UNCONFIRMED
    confidence: float = 0.0
    source: str = UNKNOWN
    checked_at: str = ""
    conflicts: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.bin_length:
            self.bin_length = len(self.bin)

    def to_row(self) -> Dict[str, object]:
        return {name: getattr(self, name) for name in BIN_FIELDS}

    @classmethod
    def from_row(cls, row: Dict[str, object]) -> "BinRecord":
        data = {name: row[name] for name in BIN_FIELDS if name in row}
        return cls(**data)  # type: ignore[arg-type]


@dataclass
class ProviderOutcome:
    """Raw per-provider answer stored for auditing."""

    provider: str
    bin: str
    status: str
    fields: Dict[str, str]
    error: Optional[str] = None
    elapsed_ms: int = 0


SCHEMA = """
CREATE TABLE IF NOT EXISTS bins (
    bin             TEXT PRIMARY KEY,
    bin_length      INTEGER NOT NULL,
    issuer          TEXT NOT NULL DEFAULT 'unknown',
    network         TEXT NOT NULL DEFAULT 'unknown',
    card_type       TEXT NOT NULL DEFAULT 'unknown',
    card_level      TEXT NOT NULL DEFAULT 'unknown',
    country         TEXT NOT NULL DEFAULT 'unknown',
    country_code    TEXT NOT NULL DEFAULT 'unknown',
    currency        TEXT NOT NULL DEFAULT 'unknown',
    prepaid         TEXT NOT NULL DEFAULT 'unknown',
    commercial      TEXT NOT NULL DEFAULT 'unknown',
    issuer_phone    TEXT NOT NULL DEFAULT 'unknown',
    issuer_website  TEXT NOT NULL DEFAULT 'unknown',
    status          TEXT NOT NULL,
    confidence      REAL NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'unknown',
    checked_at      TEXT NOT NULL,
    conflicts       TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bins_status  ON bins(status);
CREATE INDEX IF NOT EXISTS idx_bins_network ON bins(network);
CREATE INDEX IF NOT EXISTS idx_bins_country ON bins(country_code);

CREATE TABLE IF NOT EXISTS dataset_bins (
    bin             TEXT NOT NULL,
    bin_length      INTEGER NOT NULL,
    issuer          TEXT NOT NULL DEFAULT 'unknown',
    network         TEXT NOT NULL DEFAULT 'unknown',
    card_type       TEXT NOT NULL DEFAULT 'unknown',
    card_level      TEXT NOT NULL DEFAULT 'unknown',
    country         TEXT NOT NULL DEFAULT 'unknown',
    country_code    TEXT NOT NULL DEFAULT 'unknown',
    currency        TEXT NOT NULL DEFAULT 'unknown',
    prepaid         TEXT NOT NULL DEFAULT 'unknown',
    commercial      TEXT NOT NULL DEFAULT 'unknown',
    issuer_phone    TEXT NOT NULL DEFAULT 'unknown',
    issuer_website  TEXT NOT NULL DEFAULT 'unknown',
    dataset         TEXT NOT NULL,
    imported_at     TEXT NOT NULL,
    PRIMARY KEY (bin, dataset)
);

CREATE INDEX IF NOT EXISTS idx_dataset_bin ON dataset_bins(bin);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    total           INTEGER NOT NULL DEFAULT 0,
    discovered      INTEGER NOT NULL DEFAULT 0,
    unconfirmed     INTEGER NOT NULL DEFAULT 0,
    invalid         INTEGER NOT NULL DEFAULT 0,
    errors          INTEGER NOT NULL DEFAULT 0,
    note            TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS provider_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER,
    bin             TEXT NOT NULL,
    provider        TEXT NOT NULL,
    status          TEXT NOT NULL,
    fields_json     TEXT NOT NULL DEFAULT '{}',
    error           TEXT,
    elapsed_ms      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_provider_results_bin ON provider_results(bin);
"""
