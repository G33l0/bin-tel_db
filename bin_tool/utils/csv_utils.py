from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

from database.models import BIN_FIELDS, METADATA_FIELDS
from utils.validation import UNKNOWN

BIN_COLUMN_ALIASES = (
    "bin", "iin", "bin_iin", "bin/iin", "binnumber", "bin_number",
    "cardbin", "card_bin", "prefix", "iin_prefix", "number",
)

DATASET_COLUMN_ALIASES: Dict[str, Sequence[str]] = {
    "bin": BIN_COLUMN_ALIASES,
    "issuer": ("issuer", "bank", "bank_name", "issuer_name", "issuingbank", "issuing_bank"),
    "network": ("network", "scheme", "brand", "card_brand", "cardscheme"),
    "card_type": ("card_type", "type", "cardtype"),
    "card_level": ("card_level", "level", "tier", "category", "product"),
    "country": ("country", "country_name", "countryname"),
    "country_code": ("country_code", "countrycode", "alpha2", "iso_country", "iso2"),
    "currency": ("currency", "currency_code", "currencycode"),
    "prepaid": ("prepaid", "is_prepaid"),
    "commercial": ("commercial", "is_commercial", "business", "corporate"),
    "issuer_phone": ("issuer_phone", "phone", "bank_phone", "contact_phone"),
    "issuer_website": ("issuer_website", "website", "url", "bank_url", "bank_website"),
}


@dataclass
class RawEntry:
    value: str
    line: int
    source: str


def _normalise_header(name: str) -> str:
    return "".join(ch for ch in (name or "").strip().casefold() if ch.isalnum() or ch in "_/")


def _sniff_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.get_dialect("excel")


def read_bin_file(path: str) -> List[RawEntry]:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    name = os.path.basename(path)
    extension = os.path.splitext(path)[1].casefold()
    entries: List[RawEntry] = []

    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        if extension in (".csv", ".tsv"):
            sample = handle.read(8192)
            handle.seek(0)
            reader = csv.reader(handle, _sniff_dialect(sample))
            rows = list(reader)
            if not rows:
                return entries
            header = [_normalise_header(cell) for cell in rows[0]]
            index = 0
            start = 0
            matched = [i for i, cell in enumerate(header) if cell in BIN_COLUMN_ALIASES]
            if matched:
                index = matched[0]
                start = 1
            elif header and header[0] and not header[0].isdigit():
                start = 1
            for line_number, row in enumerate(rows[start:], start=start + 1):
                if not row or index >= len(row):
                    continue
                cell = row[index].strip()
                if cell:
                    entries.append(RawEntry(cell, line_number, name))
        else:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text or text.startswith("#"):
                    continue
                for chunk in text.replace(";", ",").replace("\t", ",").split(","):
                    chunk = chunk.strip()
                    if chunk:
                        entries.append(RawEntry(chunk, line_number, name))
    return entries


def read_dataset_file(path: str) -> List[Dict[str, str]]:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        reader = csv.reader(handle, _sniff_dialect(sample))
        rows = list(reader)

    if not rows:
        return []

    header = [_normalise_header(cell) for cell in rows[0]]
    mapping: Dict[str, int] = {}
    for field_name, aliases in DATASET_COLUMN_ALIASES.items():
        for index, column in enumerate(header):
            if column in aliases and field_name not in mapping:
                mapping[field_name] = index
    if "bin" not in mapping:
        raise ValueError(
            "dataset has no BIN column; expected one of: " + ", ".join(BIN_COLUMN_ALIASES)
        )

    records: List[Dict[str, str]] = []
    for row in rows[1:]:
        if not row:
            continue
        raw_bin = row[mapping["bin"]].strip() if mapping["bin"] < len(row) else ""
        if not raw_bin:
            continue
        record: Dict[str, str] = {"bin": "".join(ch for ch in raw_bin if ch.isdigit())}
        if not record["bin"]:
            continue
        for field_name in METADATA_FIELDS:
            index = mapping.get(field_name)
            value = row[index].strip() if index is not None and index < len(row) else ""
            record[field_name] = value or UNKNOWN
        records.append(record)
    return records


def _rows_for_export(rows: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    return [{name: row.get(name, UNKNOWN) for name in BIN_FIELDS} for row in rows]


def write_csv(rows: Iterable[Dict[str, object]], path: str) -> int:
    prepared = _rows_for_export(rows)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=BIN_FIELDS)
        writer.writeheader()
        writer.writerows(prepared)
    return len(prepared)


def write_json(rows: Iterable[Dict[str, object]], path: str) -> int:
    prepared = _rows_for_export(rows)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(prepared, handle, indent=2, sort_keys=False)
    return len(prepared)


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def write_sql(rows: Iterable[Dict[str, object]], path: str, table: str = "bins") -> int:
    prepared = _rows_for_export(rows)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    columns = ", ".join(BIN_FIELDS)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"-- BIN-TEL export: {len(prepared)} rows\n")
        handle.write(
            f"CREATE TABLE IF NOT EXISTS {table} (\n"
            "    bin            TEXT PRIMARY KEY,\n"
            "    bin_length     INTEGER NOT NULL,\n"
            + "".join(f"    {name:<14} TEXT,\n" for name in METADATA_FIELDS)
            + "    status         TEXT NOT NULL,\n"
            "    confidence     REAL,\n"
            "    source         TEXT,\n"
            "    checked_at     TEXT\n);\n\n"
        )
        for row in prepared:
            values = ", ".join(_sql_literal(row[name]) for name in BIN_FIELDS)
            handle.write(f"INSERT INTO {table} ({columns}) VALUES ({values});\n")
    return len(prepared)


EXPORTERS = {"csv": write_csv, "json": write_json, "sql": write_sql}


def export_rows(rows: Iterable[Dict[str, object]], path: str, fmt: str) -> int:
    exporter = EXPORTERS.get(fmt.casefold())
    if exporter is None:
        raise ValueError(f"unsupported export format: {fmt}")
    return exporter(list(rows), path)


def list_files(directory: str, extensions: Sequence[str] = (".csv", ".txt", ".tsv")) -> List[str]:
    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if os.path.splitext(name)[1].casefold() in extensions
    )
