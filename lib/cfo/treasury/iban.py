"""IBAN and BIC validation (ISO 13616 / ISO 9362).

The IBAN check is ISO 7064 mod-97-10: move the four-character country code
and check digits to the end, read every letter as a base-36 digit (A=10 ...
Z=35, which is exactly what `int(ch, 36)` gives a single character), and the
result mod 97 must be 1. That check alone accepts strings of the wrong length
for their country -- it is a checksum on the digits, not on how many of them
there are -- so a length table is carried separately and applied first.

The table is deliberately not exhaustive. A country outside it is unknown,
not invalid: `valid_iban` falls back to the mod-97 check alone for it, and
says so below. Extending the table only ever tightens what is accepted for a
country that was previously waved through on checksum alone; it can never
newly reject an IBAN this module used to accept.
"""
import re

# ISO 3166-1 alpha-2 -> IBAN length. Covers the SEPA countries a treasury
# payment run is most likely to see; anything else falls through to the
# mod-97-only check in `valid_iban`.
_LENGTHS = {
    "AT": 20, "BE": 16, "CH": 21, "CY": 28, "DE": 22, "DK": 18, "EE": 20,
    "ES": 24, "FI": 18, "FR": 27, "GB": 22, "GR": 27, "IE": 22, "IT": 27,
    "LU": 20, "MT": 31, "NL": 18, "NO": 15, "PL": 28, "PT": 25, "SE": 24,
}

# Bank code (4 letters) + country code (2 letters) + location code (2
# alphanumerics) + an optional branch code (3 alphanumerics). Only the bank
# and country positions are letters-only; ISO 9362 allows digits in the
# location and branch codes.
_BIC_RE = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")


def normalise_iban(value: str) -> str:
    """Strip all whitespace and upper-case: the form both the mod-97 check
    and the length table expect, and how an IBAN is usually typed or copied
    from a document with spaces every four characters."""
    return "".join(str(value or "").split()).upper()


def _mod97_ok(value: str) -> bool:
    rearranged = value[4:] + value[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def valid_iban(value: str) -> bool:
    """ISO 7064 mod-97-10, plus a length check for the countries in
    `_LENGTHS`. A country not in that table is unknown, not invalid: only
    the mod-97 check applies, so this can accept a structurally-plausible
    IBAN whose length was never actually checked."""
    v = normalise_iban(value)
    if len(v) < 5 or not v[:2].isalpha() or not v[2:4].isdigit() or not v[4:].isalnum():
        return False
    expected = _LENGTHS.get(v[:2])
    if expected is not None and len(v) != expected:
        return False
    return _mod97_ok(v)


def iban_country(value: str) -> str | None:
    """The two-letter country code, or None when `value` is too short or
    does not start with letters to have one -- this reads the prefix only
    and does not imply the IBAN as a whole is valid."""
    v = normalise_iban(value)
    if len(v) < 2 or not v[:2].isalpha():
        return None
    return v[:2]


def valid_bic(value: str) -> bool:
    """8 or 11 characters: bank code and country letters, then an
    alphanumeric location code and optional branch code."""
    v = "".join(str(value or "").split()).upper()
    return bool(_BIC_RE.match(v))
