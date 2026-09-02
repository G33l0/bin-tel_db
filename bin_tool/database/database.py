"""SQLite persistence layer."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from database.models import BIN_FIELDS, METADATA_FIELDS, SCHEMA, Status
from utils.validation import UNKNOWN


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    """Thin, thread-safe wrapper around the SQLite file."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------ setup
    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            directory = os.path.dirname(os.path.abspath(self.path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self.init_schema()
        return self._conn

    def init_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Database":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------- bins
    def upsert_bin(self, record: Dict[str, object]) -> None:
        conn = self.connect()
        now = utc_now()
        row = {name: record.get(name, UNKNOWN) for name in BIN_FIELDS}
        row["bin_length"] = int(record.get("bin_length") or len(str(row["bin"])))
        row["confidence"] = float(record.get("confidence") or 0.0)
        row["checked_at"] = record.get("checked_at") or now
        conflicts = record.get("conflicts") or []
        row["conflicts"] = ", ".join(conflicts) if isinstance(conflicts, (list, tuple)) else str(conflicts)

        columns = list(row.keys()) + ["created_at", "updated_at"]
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{name}=excluded.{name}" for name in row if name != "bin")
        values = list(row.values()) + [now, now]
        conn.execute(
            f"INSERT INTO bins ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(bin) DO UPDATE SET {updates}, updated_at=excluded.updated_at",
            values,
        )
        conn.commit()

    def get_bin(self, bin_value: str) -> Optional[Dict[str, object]]:
        conn = self.connect()
        row = conn.execute("SELECT * FROM bins WHERE bin = ?", (bin_value,)).fetchone()
        return dict(row) if row else None

    def fetch_bins(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        order_by: str = "bin",
    ) -> List[Dict[str, object]]:
        conn = self.connect()
        column = order_by if order_by in BIN_FIELDS else "bin"
        sql = f"SELECT * FROM bins{' WHERE status = ?' if status else ''} ORDER BY {column}"
        params: List[object] = [status] if status else []
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [dict(row) for row in conn.execute(sql, params)]

    def count_bins(self) -> int:
        conn = self.connect()
        return int(conn.execute("SELECT COUNT(*) AS n FROM bins").fetchone()["n"])

    # ---------------------------------------------------------------- dataset
    def import_dataset(self, rows: Iterable[Dict[str, object]], dataset: str) -> Dict[str, int]:
        """Bulk-load an official / licensed reference dataset."""
        conn = self.connect()
        now = utc_now()
        columns = ["bin", "bin_length"] + METADATA_FIELDS + ["dataset", "imported_at"]
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(
            f"{name}=excluded.{name}" for name in columns if name not in ("bin", "dataset")
        )
        counts = {"inserted": 0}
        payload = []
        for row in rows:
            bin_value = str(row.get("bin", "")).strip()
            if not bin_value:
                continue
            values = [bin_value, len(bin_value)]
            values += [str(row.get(name, UNKNOWN) or UNKNOWN) for name in METADATA_FIELDS]
            values += [dataset, now]
            payload.append(values)
        if payload:
            conn.executemany(
                f"INSERT INTO dataset_bins ({', '.join(columns)}) VALUES ({placeholders}) "
                f"ON CONFLICT(bin, dataset) DO UPDATE SET {updates}",
                payload,
            )
            conn.commit()
            counts["inserted"] = len(payload)
        return counts

    def lookup_dataset(self, bin_value: str) -> Optional[Dict[str, object]]:
        """Exact match first, then the longest matching shorter prefix."""
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM dataset_bins WHERE bin = ? ORDER BY imported_at DESC LIMIT 1",
            (bin_value,),
        ).fetchone()
        if row:
            return dict(row)
        prefixes = [bin_value[:n] for n in range(len(bin_value) - 1, 5, -1)]
        for prefix in prefixes:
            row = conn.execute(
                "SELECT * FROM dataset_bins WHERE bin = ? ORDER BY imported_at DESC LIMIT 1",
                (prefix,),
            ).fetchone()
            if row:
                return dict(row)
        return None

    def dataset_names(self) -> List[Dict[str, object]]:
        conn = self.connect()
        rows = conn.execute(
            "SELECT dataset, COUNT(*) AS rows, MAX(imported_at) AS imported_at "
            "FROM dataset_bins GROUP BY dataset ORDER BY imported_at DESC"
        )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------- runs
    def start_run(self, kind: str, total: int, note: str = "") -> int:
        conn = self.connect()
        cursor = conn.execute(
            "INSERT INTO runs (kind, started_at, total, note) VALUES (?, ?, ?, ?)",
            (kind, utc_now(), total, note),
        )
        conn.commit()
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, counters: Dict[str, int]) -> None:
        conn = self.connect()
        conn.execute(
            "UPDATE runs SET finished_at = ?, discovered = ?, unconfirmed = ?, "
            "invalid = ?, errors = ? WHERE id = ?",
            (
                utc_now(),
                int(counters.get("discovered", 0)),
                int(counters.get("unconfirmed", 0)),
                int(counters.get("invalid", 0)),
                int(counters.get("errors", 0)),
                run_id,
            ),
        )
        conn.commit()

    def recent_runs(self, limit: int = 5) -> List[Dict[str, object]]:
        conn = self.connect()
        rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in rows]

    def record_provider_result(
        self,
        run_id: Optional[int],
        bin_value: str,
        provider: str,
        status: str,
        fields: Dict[str, str],
        error: Optional[str] = None,
        elapsed_ms: int = 0,
    ) -> None:
        conn = self.connect()
        conn.execute(
            "INSERT INTO provider_results (run_id, bin, provider, status, fields_json, "
            "error, elapsed_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                bin_value,
                provider,
                status,
                json.dumps(fields, sort_keys=True),
                error,
                int(elapsed_ms),
                utc_now(),
            ),
        )
        conn.commit()

    # ------------------------------------------------------------------ stats
    def stats(self) -> Dict[str, object]:
        conn = self.connect()
        total = self.count_bins()
        by_status = {
            row["status"]: row["n"]
            for row in conn.execute("SELECT status, COUNT(*) AS n FROM bins GROUP BY status")
        }
        by_network = [
            dict(row)
            for row in conn.execute(
                "SELECT network, COUNT(*) AS n FROM bins GROUP BY network ORDER BY n DESC LIMIT 10"
            )
        ]
        by_country = [
            dict(row)
            for row in conn.execute(
                "SELECT country_code, COUNT(*) AS n FROM bins "
                "GROUP BY country_code ORDER BY n DESC LIMIT 10"
            )
        ]
        avg_row = conn.execute(
            "SELECT AVG(confidence) AS avg FROM bins WHERE status = ?", (Status.DISCOVERED,)
        ).fetchone()
        unknown_issuer = conn.execute(
            "SELECT COUNT(*) AS n FROM bins WHERE issuer = ?", (UNKNOWN,)
        ).fetchone()["n"]
        dataset_rows = conn.execute("SELECT COUNT(*) AS n FROM dataset_bins").fetchone()["n"]
        return {
            "total": total,
            "by_status": by_status,
            "by_network": by_network,
            "by_country": by_country,
            "avg_confidence": float(avg_row["avg"] or 0.0),
            "unknown_issuer": int(unknown_issuer),
            "dataset_rows": int(dataset_rows),
            "datasets": self.dataset_names(),
            "runs": self.recent_runs(5),
        }
