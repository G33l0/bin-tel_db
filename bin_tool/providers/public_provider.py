from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from providers.base import ERROR, FOUND, NOT_FOUND, BaseProvider, ProviderResponse
from utils.logging_utils import get_logger

try:
    import httpx
except ImportError:
    httpx = None

LOGGER = get_logger()
USER_AGENT = "bin-tel-db/1.0 (+BIN metadata tool)"


def extract_path(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
                continue
            except (ValueError, IndexError):
                return None
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


class HttpJsonProvider(BaseProvider):
    type_name = "http_json"
    requires_network = True

    def __init__(self, config: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config, context)
        self.base_url = str(self.config.get("base_url", "")).strip().rstrip("/")
        self.url_template = str(self.config.get("url_template", "{base_url}/{bin}"))
        self.method = str(self.config.get("method", "GET")).upper()
        self.headers: Dict[str, str] = dict(self.config.get("headers", {}))
        self.field_map: Dict[str, List[str]] = {
            key: list(value) if isinstance(value, (list, tuple)) else [str(value)]
            for key, value in (self.config.get("field_map") or {}).items()
        }
        self.not_found_codes = set(int(code) for code in self.config.get("not_found_status_codes", [404]))
        settings = (self.context or {}).get("validation", {})
        self.timeout = float(self.config.get("timeout_seconds", settings.get("request_timeout_seconds", 10.0)))
        self.max_retries = int(self.config.get("max_retries", settings.get("max_retries", 2)))
        self.backoff = float(self.config.get("retry_backoff_seconds", settings.get("retry_backoff_seconds", 1.5)))
        self.cache_responses = bool(self.config.get("cache_responses", True))
        self.cache_ttl_days = float(self.config.get("cache_ttl_days", 0) or 0)
        self.database = (self.context or {}).get("database")
        self._client = None

    def check_ready(self) -> Optional[str]:
        if not self.base_url:
            return "base_url is not configured"
        if not self.base_url.startswith(("http://", "https://")):
            return "base_url must start with http:// or https://"
        if not self.field_map:
            return "field_map is empty; nothing would be extracted"
        env_name = self.config.get("api_key_env")
        if env_name and not os.environ.get(str(env_name)):
            return f"environment variable {env_name} is not set"
        return None

    def _request_headers(self) -> Dict[str, str]:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        headers.update(self.headers)
        env_name = self.config.get("api_key_env")
        key = os.environ.get(str(env_name), "") if env_name else ""
        if key:
            header_name = str(self.config.get("api_key_header", "Authorization"))
            prefix = str(self.config.get("api_key_prefix", ""))
            headers[header_name] = f"{prefix}{key}"
        return headers

    def build_url(self, bin_value: str) -> str:
        return self.url_template.format(base_url=self.base_url, bin=bin_value)

    def lookup(self, bin_value: str) -> ProviderResponse:
        if self.cache_responses and self.database is not None:
            cached = self.database.cache_get(self.name, bin_value, self.cache_ttl_days)
            if cached is not None and cached["status"] in (FOUND, NOT_FOUND):
                return ProviderResponse(self.name, bin_value, cached["status"], dict(cached["fields"]))
        response = super().lookup(bin_value)
        if (
            self.cache_responses
            and self.database is not None
            and response.status in (FOUND, NOT_FOUND)
        ):
            self.database.cache_put(self.name, bin_value, response.status, response.fields)
        return response

    def fetch(self, bin_value: str) -> ProviderResponse:
        reason = self.check_ready()
        if reason:
            return self.failed(bin_value, reason)

        url = self.build_url(bin_value)
        last_error = "request failed"
        for attempt in range(self.max_retries + 1):
            status_code, body, error = self._send(url)
            if error:
                last_error = error
            elif status_code in self.not_found_codes:
                return self.not_found(bin_value)
            elif 200 <= status_code < 300:
                return self._parse(bin_value, body)
            elif status_code in (408, 429) or status_code >= 500:
                last_error = f"HTTP {status_code}"
            else:
                return self.failed(bin_value, f"HTTP {status_code}")

            if attempt < self.max_retries:
                delay = self.backoff * (2**attempt)
                LOGGER.warning("%s: %s for %s; retrying in %.1fs", self.name, last_error, bin_value, delay)
                time.sleep(delay)
        return self.failed(bin_value, last_error)

    def _send(self, url: str):
        headers = self._request_headers()
        if httpx is not None:
            try:
                if self._client is None:
                    self._client = httpx.Client(timeout=self.timeout, follow_redirects=True)
                response = self._client.request(self.method, url, headers=headers)
                return response.status_code, response.text, None
            except Exception as exc:
                return 0, "", f"{type(exc).__name__}: {exc}"
        request = urllib.request.Request(url, headers=headers, method=self.method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read().decode("utf-8", "replace"), None
        except urllib.error.HTTPError as exc:
            return exc.code, "", None
        except Exception as exc:
            return 0, "", f"{type(exc).__name__}: {exc}"

    def _parse(self, bin_value: str, body: str) -> ProviderResponse:
        try:
            payload = json.loads(body) if body.strip() else {}
        except json.JSONDecodeError as exc:
            return self.failed(bin_value, f"invalid JSON response: {exc}")

        root = self.config.get("result_path")
        if root:
            payload = extract_path(payload, str(root))
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if not isinstance(payload, dict) or not payload:
            return self.not_found(bin_value)

        raw: Dict[str, Any] = {}
        for field_name, candidates in self.field_map.items():
            for candidate in candidates:
                value = extract_path(payload, candidate)
                if value not in (None, "", [], {}):
                    raw[field_name] = value
                    break
        return self.found(bin_value, raw)

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None


class BinlistProvider(HttpJsonProvider):
    type_name = "binlist"

    DEFAULTS = {
        "base_url": "https://lookup.binlist.net",
        "url_template": "{base_url}/{bin}",
        "method": "GET",
        "headers": {"Accept": "application/json", "Accept-Version": "3"},
        "not_found_status_codes": [404],
        "rate_limit_per_second": 0.08,
        "field_map": {
            "issuer": ["bank.name"],
            "network": ["scheme"],
            "card_type": ["type"],
            "card_level": ["brand"],
            "country": ["country.name"],
            "country_code": ["country.alpha2"],
            "currency": ["country.currency"],
            "prepaid": ["prepaid"],
            "issuer_phone": ["bank.phone"],
            "issuer_website": ["bank.url"],
        },
    }

    def __init__(self, config: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> None:
        merged: Dict[str, Any] = dict(self.DEFAULTS)
        for key, value in (config or {}).items():
            if key == "headers" and isinstance(value, dict):
                merged["headers"] = {**self.DEFAULTS["headers"], **value}
            elif key == "field_map" and isinstance(value, dict) and value:
                merged["field_map"] = value
            elif value not in (None, ""):
                merged[key] = value
        super().__init__(merged, context)
