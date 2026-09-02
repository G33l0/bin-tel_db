from __future__ import annotations

import copy
import json
import os
import sys
from typing import Any, Dict

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    "database": {"path": "data/bin_tel.sqlite3"},
    "paths": {
        "input": "data/input",
        "results": "data/results",
        "imports": "data/imports",
        "logs": "data/logs",
    },
    "input": {"allowed_bin_lengths": [6, 8], "max_input_digits": 8, "deduplicate": True},
    "validation": {
        "concurrency": 4,
        "request_timeout_seconds": 10.0,
        "max_retries": 2,
        "retry_backoff_seconds": 1.5,
        "min_providers_for_confirmation": 1,
        "required_fields_for_discovery": ["network", "issuer"],
        "min_confidence_for_discovery": 0.35,
        "skip_already_discovered": True,
        "store_provider_results": True,
    },
    "export": {"default_format": "csv"},
    "ui": {"ascii_symbols": "auto", "log_lines": 12},
    "logging": {"level": "INFO", "file": "bin_tel.log"},
    "providers": [
        {
            "name": "offline_iin_ranges",
            "type": "offline_iin_ranges",
            "enabled": True,
            "description": "Card network from published IIN ranges. Offline.",
        },
        {
            "name": "local_dataset",
            "type": "local_dataset",
            "enabled": True,
            "description": "Reference dataset imported with menu option 3.",
        },
        {
            "name": "metadata_api",
            "type": "http_json",
            "enabled": False,
            "description": "Licensed BIN metadata API. Set base_url, then enable.",
            "base_url": "",
            "url_template": "{base_url}/{bin}",
            "method": "GET",
            "headers": {"Accept": "application/json"},
            "api_key_env": "BIN_TEL_API_KEY",
            "api_key_header": "Authorization",
            "api_key_prefix": "Bearer ",
            "rate_limit_per_second": 1.0,
            "not_found_status_codes": [404],
            "field_map": {
                "issuer": ["bank.name", "issuer", "bank"],
                "network": ["scheme", "network", "brand"],
                "card_type": ["type", "card_type"],
                "card_level": ["brand", "level", "tier"],
                "country": ["country.name", "country"],
                "country_code": ["country.alpha2", "country_code"],
                "currency": ["country.currency", "currency"],
                "prepaid": ["prepaid"],
                "commercial": ["commercial", "business"],
                "issuer_phone": ["bank.phone", "phone"],
                "issuer_website": ["bank.url", "website"],
            },
        },
    ],
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_config(path: str = CONFIG_PATH) -> Dict[str, Any]:
    if not os.path.isfile(path):
        save_config(DEFAULT_CONFIG, path)
        return copy.deepcopy(DEFAULT_CONFIG)
    with open(path, "r", encoding="utf-8") as handle:
        user_config = json.load(handle)
    return _deep_merge(DEFAULT_CONFIG, user_config)


def save_config(config: Dict[str, Any], path: str = CONFIG_PATH) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")


def resolve_path(relative: str) -> str:
    if os.path.isabs(relative):
        return relative
    return os.path.normpath(os.path.join(APP_DIR, relative))


def ensure_directories(config: Dict[str, Any]) -> None:
    for key in ("input", "results", "imports", "logs"):
        os.makedirs(resolve_path(config["paths"][key]), exist_ok=True)
    database_dir = os.path.dirname(resolve_path(config["database"]["path"]))
    if database_dir:
        os.makedirs(database_dir, exist_ok=True)
