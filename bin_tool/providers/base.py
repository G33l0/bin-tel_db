from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from database.models import METADATA_FIELDS
from utils.validation import (
    UNKNOWN,
    canonical_network,
    clean_boolean,
    clean_country_code,
    clean_currency,
    clean_value,
)

FOUND = "found"
NOT_FOUND = "not_found"
ERROR = "error"

_CLEANERS = {
    "network": canonical_network,
    "country_code": clean_country_code,
    "currency": clean_currency,
    "prepaid": clean_boolean,
    "commercial": clean_boolean,
}


def clean_fields(raw: Dict[str, Any]) -> Dict[str, str]:
    cleaned: Dict[str, str] = {}
    for name in METADATA_FIELDS:
        cleaner = _CLEANERS.get(name, clean_value)
        value = cleaner(raw.get(name))
        if value != UNKNOWN:
            cleaned[name] = value
    return cleaned


@dataclass
class ProviderResponse:
    provider: str
    bin: str
    status: str
    fields: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status == FOUND


class RateLimiter:
    def __init__(self, per_second: float) -> None:
        self.interval = 1.0 / per_second if per_second and per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next_at = 0.0

    def acquire(self) -> None:
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            self._next_at = max(now, self._next_at) + self.interval
        if wait > 0:
            time.sleep(wait)


class BaseProvider:
    type_name = "base"
    requires_network = False

    def __init__(self, config: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}
        self.context = context or {}
        self.name = str(self.config.get("name") or self.type_name)
        self.enabled = bool(self.config.get("enabled", False))
        self.description = str(self.config.get("description", ""))
        self.limiter = RateLimiter(float(self.config.get("rate_limit_per_second", 0) or 0))

    def check_ready(self) -> Optional[str]:
        return None

    def fetch(self, bin_value: str) -> ProviderResponse:
        raise NotImplementedError

    def lookup(self, bin_value: str) -> ProviderResponse:
        started = time.monotonic()
        try:
            self.limiter.acquire()
            response = self.fetch(bin_value)
        except Exception as exc:
            response = self.failed(bin_value, f"{type(exc).__name__}: {exc}")
        if not response.elapsed_ms:
            response.elapsed_ms = int((time.monotonic() - started) * 1000)
        return response

    def found(self, bin_value: str, raw: Dict[str, Any]) -> ProviderResponse:
        fields = clean_fields(raw)
        if not fields:
            return self.not_found(bin_value)
        return ProviderResponse(self.name, bin_value, FOUND, fields)

    def not_found(self, bin_value: str) -> ProviderResponse:
        return ProviderResponse(self.name, bin_value, NOT_FOUND, {})

    def failed(self, bin_value: str, error: str) -> ProviderResponse:
        return ProviderResponse(self.name, bin_value, ERROR, {}, error=error)

    def close(self) -> None:
        pass


def provider_registry() -> Dict[str, type]:
    from providers.local_provider import LocalDatasetProvider
    from providers.offline_provider import OfflineIinRangeProvider
    from providers.public_provider import BinlistProvider, HttpJsonProvider

    return {
        OfflineIinRangeProvider.type_name: OfflineIinRangeProvider,
        LocalDatasetProvider.type_name: LocalDatasetProvider,
        BinlistProvider.type_name: BinlistProvider,
        HttpJsonProvider.type_name: HttpJsonProvider,
    }


def build_provider(
    entry: Dict[str, Any], context: Optional[Dict[str, Any]] = None
) -> Optional[BaseProvider]:
    provider_class = provider_registry().get(str(entry.get("type", "")).strip())
    if provider_class is None:
        return None
    return provider_class(entry, context)


def build_providers(config: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> List[BaseProvider]:
    providers: List[BaseProvider] = []
    for entry in config.get("providers", []):
        provider = build_provider(entry, context)
        if provider is not None and provider.enabled:
            providers.append(provider)
    return providers
