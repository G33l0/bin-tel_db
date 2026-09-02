from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from database.models import BIN_FIELDS, METADATA_FIELDS, SCHEMA, Status
from utils.validation import UNKNOWN


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                directory = os.path.dirname(os.path.abspath(self.path))
                if directory:
                    os.makedirs(directory, exist_ok=True)
                self._conn = sqlite3.connect(
                    self.path, check_same_thread=False, isolation_level=None, timeout=30
                )
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA foreign_keys=ON")
                self._conn.execute("PRAGMA busy_timeout=30000")
                self._conn.executescript(SCHEMA)
            return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> "Database":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def upsert_bin(self, record: Dict[str, object]) -> None:
        now = utc_now()
        row = {name: record.get(name, UNKNOWN) for name in BIN_FIELDS}
        row["bin_length"] = int(record.get("bin_length") or len(str(row["bin"])))
        row["confidence"] = float(record.get("confidence") or 0.0)
        row["checked_at"] = record.get("checked_at") or now
        conflicts = record.get("conflicts") or []
        row["conflicts"] = (
            ", ".join(conflicts) if isinstance(conflicts, (list, tuple)) else str(conflicts)
        )

        columns = list(row.keys()) + ["created_at", "updated_at"]
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{name}=excluded.{name}" for name in row if name != "bin")
        values = list(row.values()) + [now, now]
        with self._lock:
            self.connect().execute(
                f"INSERT INTO bins ({', '.join(columns)}) VALUES ({placeholders}) "
                f"ON CONFLICT(bin) DO UPDATE SET {updates}, updated_at=excluded.updated_at",
                values,
            )

    def get_bin(self, bin_value: str) -> Optional[Dict[str, object]]:
        with self._lock:
            row = self.connect().execute("SELECT * FROM bins WHERE bin = ?", (bin_value,)).fetchone()
        return dict(row) if row else None

    def fetch_bins(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        order_by: str = "bin",
    ) -> List[Dict[str, object]]:
        column = order_by if order_by in BIN_FIELDS else "bin"
        sql = f"SELECT * FROM bins{' WHERE status = ?' if status else ''} ORDER BY {column}"
        params: List[object] = [status] if status else []
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._lock:
            return [dict(row) for row in self.connect().execute(sql, params)]

    def count_bins(self) -> int:
        with self._lock:
            return int(self.connect().execute("SELECT COUNT(*) AS n FROM bins").fetchone()["n"])

    def import_dataset(self, rows: Iterable[Dict[str, object]], dataset: str) -> Dict[str, int]:
        now = utc_now()
        columns = ["bin", "bin_length"] + METADATA_FIELDS + ["dataset", "imported_at"]
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(
            f"{name}=excluded.{name}" for name in columns if name not in ("bin", "dataset")
        )
        payload = []
        for row in rows:
            bin_value = str(row.get("bin", "")).strip()
            if not bin_value:
                continue
            values = [bin_value, len(bin_value)]
            values += [str(row.get(name, UNKNOWN) or UNKNOWN) for name in METADATA_FIELDS]
            values += [dataset, now]
            payload.append(values)
        if not payload:
            return {"inserted": 0}

        sql = (
            f"INSERT INTO dataset_bins ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(bin, dataset) DO UPDATE SET {updates}"
        )
        with self._lock:
            conn = self.connect()
            conn.execute("BEGIN")
            try:
                conn.executemany(sql, payload)
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        return {"inserted": len(payload)}

    def lookup_dataset(self, bin_value: str) -> Optional[Dict[str, object]]:
        sql = "SELECT * FROM dataset_bins WHERE bin = ? ORDER BY imported_at DESC LIMIT 1"
        candidates = [bin_value] + [bin_value[:n] for n in range(len(bin_value) - 1, 5, -1)]
        with self._lock:
            conn = self.connect()
            for candidate in candidates:
                row = conn.execute(sql, (candidate,)).fetchone()
                if row:
                    return dict(row)
        return None

    def dataset_names(self) -> List[Dict[str, object]]:
        with self._lock:
            return [
                dict(row)
                for row in self.connect().execute(
                    "SELECT dataset, COUNT(*) AS rows, MAX(imported_at) AS imported_at "
                    "FROM dataset_bins GROUP BY dataset ORDER BY imported_at DESC"
                )
            ]

    def start_run(self, kind: str, total: int, note: str = "") -> int:
        with self._lock:
            cursor = self.connect().execute(
                "INSERT INTO runs (kind, started_at, total, note) VALUES (?, ?, ?, ?)",
                (kind, utc_now(), total, note),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, counters: Dict[str, int]) -> None:
        with self._lock:
            self.connect().execute(
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

    def recent_runs(self, limit: int = 5) -> List[Dict[str, object]]:
        with self._lock:
            return [
                dict(row)
                for row in self.connect().execute(
                    "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
                )
            ]

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
        with self._lock:
            self.connect().execute(
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

    def cache_get(self, provider: str, bin_value: str, max_age_days: float = 0) -> Optional[Dict[str, object]]:
        with self._lock:
            row = self.connect().execute(
                "SELECT status, fields_json, fetched_at FROM http_cache WHERE provider = ? AND bin = ?",
                (provider, bin_value),
            ).fetchone()
        if row is None:
            return None
        if max_age_days and max_age_days > 0:
            try:
                fetched = datetime.strptime(row["fetched_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                fetched = None
            if fetched is not None:
                age_days = (datetime.now(timezone.utc) - fetched).total_seconds() / 86400
                if age_days > max_age_days:
                    return None
        try:
            fields = json.loads(row["fields_json"])
        except (TypeError, json.JSONDecodeError):
            fields = {}
        return {"status": row["status"], "fields": fields, "fetched_at": row["fetched_at"]}

    def cache_put(self, provider: str, bin_value: str, status: str, fields: Dict[str, str]) -> None:
        with self._lock:
            self.connect().execute(
                "INSERT INTO http_cache (provider, bin, status, fields_json, fetched_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(provider, bin) DO UPDATE SET "
                "status=excluded.status, fields_json=excluded.fields_json, fetched_at=excluded.fetched_at",
                (provider, bin_value, status, json.dumps(fields, sort_keys=True), utc_now()),
            )

    def cache_size(self) -> int:
        with self._lock:
            return int(self.connect().execute("SELECT COUNT(*) AS n FROM http_cache").fetchone()["n"])

    def clear_cache(self, provider: Optional[str] = None) -> int:
        with self._lock:
            conn = self.connect()
            before = conn.execute("SELECT COUNT(*) AS n FROM http_cache").fetchone()["n"]
            if provider:
                conn.execute("DELETE FROM http_cache WHERE provider = ?", (provider,))
            else:
                conn.execute("DELETE FROM http_cache")
            after = conn.execute("SELECT COUNT(*) AS n FROM http_cache").fetchone()["n"]
        return int(before - after)

    def stats(self) -> Dict[str, object]:
        with self._lock:
            conn = self.connect()
            total = int(conn.execute("SELECT COUNT(*) AS n FROM bins").fetchone()["n"])
            by_status = {
                row["status"]: row["n"]
                for row in conn.execute("SELECT status, COUNT(*) AS n FROM bins GROUP BY status")
            }
            by_network = [
                dict(row)
                for row in conn.execute(
                    "SELECT network, COUNT(*) AS n FROM bins "
                    "GROUP BY network ORDER BY n DESC LIMIT 10"
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
            cached = conn.execute("SELECT COUNT(*) AS n FROM http_cache").fetchone()["n"]
        return {
            "total": total,
            "by_status": by_status,
            "by_network": by_network,
            "by_country": by_country,
            "avg_confidence": float(avg_row["avg"] or 0.0),
            "unknown_issuer": int(unknown_issuer),
            "dataset_rows": int(dataset_rows),
            "cached_responses": int(cached),
            "datasets": self.dataset_names(),
            "runs": self.recent_runs(5),
        }
