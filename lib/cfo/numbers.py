"""Shared, European-safe number parsing.

Rejects a comma that could be either a thousands separator or a decimal
comma (`"1,5"`), rejects non-finite numbers, and understands the £/€/$
symbols and gbp/eur/usd words plus k/m/mn/b/bn suffixes. Used by the
company-profile schema and the workbook builder, so both interpret the same
input the same way.
"""
import math
import re

_NUMBER = re.compile(r"^(-?\d+(?:\.\d+)?)(k|mn|m|bn|b)?$", re.I)
_MULTIPLIERS = {"": 1, "k": 1e3, "m": 1e6, "mn": 1e6, "b": 1e9, "bn": 1e9}


def parse_number(value):
    if isinstance(value, bool):
        raise ValueError(f"'{value}' is not a number")
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError(f"'{value}' is not a finite number")
        return value
    raw = re.sub(r"[€£$\s]|eur|gbp|usd", "", str(value), flags=re.I)
    if re.search(r",(?!\d{3}(?!\d))", raw):
        raise ValueError(f"'{value}' is ambiguous: write it like 1234.5 or 1,234.5")
    text = raw.replace(",", "")
    match = _NUMBER.match(text)
    if not match:
        raise ValueError(f"'{value}' is not a number")
    number = float(match.group(1)) * _MULTIPLIERS[(match.group(2) or "").lower()]
    return int(number) if number.is_integer() else number
