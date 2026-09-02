"""Validation engine: queries providers, reconciles answers, scores confidence."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from database.models import CORE_FIELDS, METADATA_FIELDS, Status
from providers.base import BaseProvider, ProviderResponse
from utils.logging_utils import get_logger
from utils.validation import UNKNOWN, normalize_comparable

LOGGER = get_logger()

FIELD_WEIGHTS = {name: (2.0 if name in CORE_FIELDS else 1.0) for name in METADATA_FIELDS}

# Event names emitted to the UI.
EV_PROCESSING = "processing"
EV_FIELD = "field"
EV_CONFLICT = "conflict"
EV_NOTE = "note"
EV_RESULT = "result"


@dataclass
class Event:
    kind: str
    bin: str
    text: str = ""
    status: str = ""


@dataclass
class BinOutcome:
    """Everything the engine learned about one BIN."""

    bin: str
    record: Dict[str, object]
    conflicts: List[str] = field(default_factory=list)
    responses: List[ProviderResponse] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def status(self) -> str:
        return str(self.record.get("status", Status.UNCONFIRMED))


@dataclass
class RunCounters:
    total: int = 0
    processed: int = 0
    discovered: int = 0
    unconfirmed: int = 0
    invalid: int = 0
    errors: int = 0
    elapsed_total: float = 0.0

    @property
    def average_seconds(self) -> float:
        return self.elapsed_total / self.processed if self.processed else 0.0

    def record(self, outcome: BinOutcome) -> None:
        self.processed += 1
        self.elapsed_total += outcome.elapsed
        status = outcome.status
        if status == Status.DISCOVERED:
            self.discovered += 1
        elif status == Status.INVALID:
            self.invalid += 1
        elif status == Status.ERROR:
            self.errors += 1
        else:
            self.unconfirmed += 1

    def as_dict(self) -> Dict[str, int]:
        return {
            "total": self.total,
            "processed": self.processed,
            "discovered": self.discovered,
            "unconfirmed": self.unconfirmed,
            "invalid": self.invalid,
            "errors": self.errors,
        }


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


DEFAULT_REQUIRED_FIELDS = ("network", "issuer")


def reconcile(
    bin_value: str,
    responses: Sequence[ProviderResponse],
    min_providers: int = 1,
    required_fields: Sequence[str] = DEFAULT_REQUIRED_FIELDS,
    min_confidence: float = 0.0,
) -> BinOutcome:
    """Merge provider answers into one record.

    A field is only written when the providers that reported it agree. Any
    disagreement leaves the field as ``unknown`` and is recorded as a conflict,
    so the database never contains an invented or arbitrarily-picked value.

    A record is only marked ``discovered`` when every field in
    ``required_fields`` was resolved and the confidence score clears
    ``min_confidence``; everything else stays ``unconfirmed`` for review.
    """
    record: Dict[str, object] = {
        "bin": bin_value,
        "bin_length": len(bin_value),
        "checked_at": utc_stamp(),
    }
    for name in METADATA_FIELDS:
        record[name] = UNKNOWN

    responding = [r for r in responses if r.ok]
    not_found = [r for r in responses if r.status == "not_found"]
    failed = [r for r in responses if r.status == "error"]

    conflicts: List[str] = []
    agreed = 0
    considered = 0

    for name in METADATA_FIELDS:
        values: Dict[str, str] = {}
        for response in responding:
            value = response.fields.get(name)
            if value and value != UNKNOWN:
                values[response.provider] = value
        if not values:
            continue
        considered += 1
        distinct = {normalize_comparable(v) for v in values.values()}
        distinct.discard("")
        if len(distinct) <= 1:
            record[name] = next(iter(values.values()))
            agreed += 1
        else:
            reporters = ", ".join(f"{p}={v}" for p, v in sorted(values.items()))
            conflicts.append(f"{name} ({reporters})")

    # ------------------------------------------------------------- confidence
    total_weight = sum(FIELD_WEIGHTS.values())
    known_weight = sum(
        FIELD_WEIGHTS[name] for name in METADATA_FIELDS if record[name] != UNKNOWN
    )
    coverage = known_weight / total_weight if total_weight else 0.0
    agreement = agreed / considered if considered else 0.0
    provider_factor = 1.0 if len(responding) >= 2 else 0.85
    confidence = round(min(1.0, (0.6 * coverage + 0.4 * agreement) * provider_factor), 3)

    # ----------------------------------------------------------------- status
    if not responses:
        status = Status.ERROR
    elif not responding and failed:
        # A provider that could not be reached is not evidence that the BIN
        # does not exist, so the record is flagged for a re-check.
        status = Status.ERROR
    elif not responding and not_found:
        status = Status.INVALID
    elif conflicts:
        status = Status.UNCONFIRMED
    elif len(responding) < max(1, int(min_providers)):
        status = Status.UNCONFIRMED
    elif any(record.get(name, UNKNOWN) == UNKNOWN for name in required_fields):
        status = Status.UNCONFIRMED
    elif confidence < float(min_confidence):
        status = Status.UNCONFIRMED
    else:
        status = Status.DISCOVERED

    record["status"] = status
    record["confidence"] = confidence if status != Status.INVALID else 0.0
    record["source"] = ", ".join(sorted(r.provider for r in responding)) or (
        ", ".join(sorted(r.provider for r in responses)) or UNKNOWN
    )
    record["conflicts"] = conflicts
    return BinOutcome(bin_value, record, conflicts, list(responses))


class ValidationEngine:
    """Runs a batch of BINs through the configured providers."""

    def __init__(
        self,
        config: Dict[str, object],
        database,
        providers: Sequence[BaseProvider],
        emit: Optional[Callable[[Event], None]] = None,
    ) -> None:
        self.config = config
        self.database = database
        self.providers = list(providers)
        self.emit = emit or (lambda event: None)
        settings = dict(config.get("validation", {}))  # type: ignore[arg-type]
        self.concurrency = max(1, int(settings.get("concurrency", 4)))
        self.min_providers = int(settings.get("min_providers_for_confirmation", 1))
        self.required_fields = tuple(
            settings.get("required_fields_for_discovery", DEFAULT_REQUIRED_FIELDS)
        )
        self.min_confidence = float(settings.get("min_confidence_for_discovery", 0.0))
        self.store_provider_results = bool(settings.get("store_provider_results", True))
        self._emit_lock = threading.Lock()
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ single
    def validate_one(self, bin_value: str, run_id: Optional[int] = None) -> BinOutcome:
        started = time.monotonic()
        responses = [provider.lookup(bin_value) for provider in self.providers]
        outcome = reconcile(
            bin_value,
            responses,
            self.min_providers,
            self.required_fields,
            self.min_confidence,
        )
        outcome.elapsed = time.monotonic() - started

        if self.database is not None:
            if self.store_provider_results:
                for response in responses:
                    self.database.record_provider_result(
                        run_id,
                        bin_value,
                        response.provider,
                        response.status,
                        response.fields,
                        response.error,
                        response.elapsed_ms,
                    )
            self.database.upsert_bin(outcome.record)
        return outcome

    # ------------------------------------------------------------------- batch
    def run(self, bins: Iterable[str], run_id: Optional[int] = None) -> RunCounters:
        queue = list(dict.fromkeys(bins))
        counters = RunCounters(total=len(queue))
        if not queue:
            return counters

        def worker(bin_value: str) -> BinOutcome:
            if self._stop.is_set():
                raise RuntimeError("run cancelled")
            return self.validate_one(bin_value, run_id)

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            for bin_value, outcome in zip(queue, pool.map(worker, queue)):
                counters.record(outcome)
                self._emit_outcome(outcome, counters)
                LOGGER.info(
                    "%s -> %s (confidence %.3f, source %s)",
                    bin_value,
                    outcome.record.get("status"),
                    float(outcome.record.get("confidence", 0.0)),
                    outcome.record.get("source"),
                )
        return counters

    def _emit_outcome(self, outcome: BinOutcome, counters: RunCounters) -> None:
        """Emit one contiguous block of events per BIN."""
        with self._emit_lock:
            self.emit(Event(EV_PROCESSING, outcome.bin, f"Processing {outcome.bin}"))
            record = outcome.record
            for name in METADATA_FIELDS:
                value = record.get(name, UNKNOWN)
                if value == UNKNOWN:
                    continue
                label = "" if name == "network" else f"{name.replace('_', ' ').title()}: "
                self.emit(Event(EV_FIELD, outcome.bin, f"{label}{value}"))
            for conflict in outcome.conflicts:
                self.emit(Event(EV_CONFLICT, outcome.bin, f"Conflicting {conflict}"))
            for response in outcome.responses:
                if response.status == "error" and response.error:
                    self.emit(
                        Event(EV_NOTE, outcome.bin, f"{response.provider}: {response.error}")
                    )
            self.emit(Event(EV_RESULT, outcome.bin, "", record.get("status", "")))

    def close(self) -> None:
        for provider in self.providers:
            provider.close()


def coverage_confidence(record: Dict[str, object]) -> float:
    """Confidence for a record whose fields come from a single trusted source.

    Scores only how complete the record is - it makes no claim about accuracy
    beyond what the source supplied.
    """
    total_weight = sum(FIELD_WEIGHTS.values())
    known = sum(
        FIELD_WEIGHTS[name]
        for name in METADATA_FIELDS
        if str(record.get(name, UNKNOWN)) not in ("", UNKNOWN)
    )
    return round(known / total_weight, 3) if total_weight else 0.0
