from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

UNKNOWN = "unknown"

MIN_BIN_DIGITS = 6
MAX_BIN_DIGITS = 8
ALLOWED_BIN_LENGTHS: Sequence[int] = (6, 8)

_NON_DIGITS = re.compile(r"\D+")

MII_INDUSTRIES = {
    "0": "ISO/TC 68 and other industry assignments",
    "1": "Airlines",
    "2": "Airlines, financial and other future industry assignments",
    "3": "Travel and entertainment",
    "4": "Banking and financial",
    "5": "Banking and financial",
    "6": "Merchandising and banking/financial",
    "7": "Petroleum and other future industry assignments",
    "8": "Healthcare, telecommunications and other future assignments",
    "9": "National assignment",
}


@dataclass(frozen=True)
class BinInput:
    raw: str
    value: Optional[str]
    ok: bool
    reason: str = ""

    @property
    def length(self) -> int:
        return len(self.value) if self.value else 0


def looks_like_pan(digits: str) -> bool:
    return len(digits) >= 12


def normalize_bin(
    raw: str,
    allowed_lengths: Iterable[int] = ALLOWED_BIN_LENGTHS,
    max_digits: int = MAX_BIN_DIGITS,
) -> BinInput:
    allowed = tuple(sorted(set(int(n) for n in allowed_lengths)))
    raw_text = "" if raw is None else str(raw).strip()

    if not raw_text:
        return BinInput(raw_text, None, False, "empty value")

    digits = _NON_DIGITS.sub("", raw_text)
    if not digits:
        return BinInput(raw_text, None, False, "no digits found")

    if looks_like_pan(digits):
        return BinInput(
            raw_text,
            None,
            False,
            f"{len(digits)} digits looks like a full card number; only BIN/IIN values are accepted",
        )

    if len(digits) > max_digits:
        return BinInput(
            raw_text,
            None,
            False,
            f"{len(digits)} digits exceeds the {max_digits}-digit BIN/IIN limit",
        )

    if len(digits) not in allowed:
        wanted = " or ".join(str(n) for n in allowed)
        return BinInput(raw_text, None, False, f"length {len(digits)} is not {wanted} digits")

    return BinInput(raw_text, digits, True, "")


def mii_industry(bin_value: str) -> Optional[str]:
    if not bin_value:
        return None
    return MII_INDUSTRIES.get(bin_value[0])


def network_from_iin(bin_value: str) -> Optional[str]:
    digits = _NON_DIGITS.sub("", bin_value or "")
    if len(digits) < 4:
        return None

    two = int(digits[:2])
    three = int(digits[:3])
    four = int(digits[:4])
    six = int(digits[:6]) if len(digits) >= 6 else None

    if digits[0] == "4":
        return "Visa"
    if 51 <= two <= 55 or 2221 <= four <= 2720:
        return "Mastercard"
    if two in (34, 37):
        return "American Express"
    if 3528 <= four <= 3589:
        return "JCB"
    if two in (36, 38, 39) or 300 <= three <= 305 or four == 3095:
        return "Diners Club"
    if six is not None and 622126 <= six <= 622925:
        return None
    if digits.startswith("6011") or two == 65 or 644 <= three <= 649:
        return "Discover"
    if two == 62 or two == 81:
        return "UnionPay"
    if two in (50, 63, 67) or 56 <= two <= 58:
        return "Maestro"
    return None


def normalize_comparable(value: Optional[str]) -> str:
    if value is None:
        return ""
    text = str(value).strip().casefold()
    if text in ("", UNKNOWN, "none", "null", "n/a", "na", "-"):
        return ""
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^www\.", "", text).rstrip("/")
    text = re.sub(r"[\-/_&+]+", " ", text)
    text = re.sub(r"[^\w\s]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_value(value) -> str:
    if value is None:
        return UNKNOWN
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    if not text or text.casefold() in (UNKNOWN, "none", "null", "n/a", "na", "-"):
        return UNKNOWN
    return re.sub(r"\s+", " ", text)


def clean_country_code(value) -> str:
    text = clean_value(value)
    if text == UNKNOWN:
        return UNKNOWN
    code = re.sub(r"[^A-Za-z]", "", text).upper()
    return code if len(code) == 2 else UNKNOWN


def clean_currency(value) -> str:
    text = clean_value(value)
    if text == UNKNOWN:
        return UNKNOWN
    code = re.sub(r"[^A-Za-z]", "", text).upper()
    return code if len(code) == 3 else UNKNOWN


def clean_boolean(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = clean_value(value).casefold()
    if text in ("true", "yes", "y", "1"):
        return "true"
    if text in ("false", "no", "n", "0"):
        return "false"
    return UNKNOWN
