"""Console theme, symbols and status styling."""

from __future__ import annotations

import sys
from typing import Optional

from rich.console import Console
from rich.theme import Theme

from database.models import Status

THEME = Theme(
    {
        "ok": "bold green",
        "bad": "bold red",
        "warn": "bold yellow",
        "info": "cyan",
        "muted": "grey62",
        "time": "grey50",
        "heading": "bold cyan",
        "frame": "cyan",
        "value": "white",
        "prompt": "bold white",
    }
)

# Status -> (style, symbol key)
STATUS_STYLES = {
    Status.DISCOVERED: "ok",
    Status.IMPORTED: "ok",
    Status.UNCONFIRMED: "warn",
    Status.INVALID: "bad",
    Status.ERROR: "bad",
}

UNICODE_SYMBOLS = {"ok": "✓", "warn": "!", "bad": "✗", "dot": "•", "arrow": "→"}
ASCII_SYMBOLS = {"ok": "OK", "warn": "!", "bad": "x", "dot": "-", "arrow": "->"}

_console: Optional[Console] = None
_symbols = dict(UNICODE_SYMBOLS)


def _terminal_supports_unicode() -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "✓✗".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def configure(ascii_symbols="auto") -> Console:
    """Create the shared console and pick the symbol set."""
    global _console, _symbols
    if isinstance(ascii_symbols, str) and ascii_symbols.casefold() == "auto":
        use_ascii = not _terminal_supports_unicode()
    else:
        use_ascii = bool(ascii_symbols)
    _symbols = dict(ASCII_SYMBOLS if use_ascii else UNICODE_SYMBOLS)
    _console = Console(theme=THEME, highlight=False, soft_wrap=False)
    return _console


def console() -> Console:
    if _console is None:
        return configure()
    return _console


def symbol(name: str) -> str:
    return _symbols.get(name, "")


def status_style(status: str) -> str:
    return STATUS_STYLES.get(status, "muted")


def confidence_style(confidence: float) -> str:
    if confidence >= 0.75:
        return "ok"
    if confidence >= 0.4:
        return "warn"
    return "bad"
