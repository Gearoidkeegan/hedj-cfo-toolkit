"""Flat CSV emitter: one row per `Payment`, in `Payment`'s own declared
field order (`dataclasses.fields`), so the column order documents itself
from the model rather than drifting from a second, hand-written list kept
here. `COLUMNS` is exported so a caller -- or a test -- can read that order
back without re-deriving it.

Amounts are `Decimal`, quantised to two places and written as plain
decimal strings -- never `float`, and never an unquantised `Decimal`
(`Decimal("0.1") + Decimal("0.2")` sums to an exact `Decimal("0.3")`, but a
bank statement line wants "0.30" -- see task-4-brief.md's example).
`beneficiary_address` is the one field on `Payment` that isn't already a
CSV-shaped scalar, so it is written as a single JSON cell; every other
field is a plain string, a date, or an amount and needs no such encoding.
"""
import csv
import io
import json
from dataclasses import fields
from decimal import Decimal, ROUND_HALF_UP

from cfo.payments.model import Payment

COLUMNS = tuple(field.name for field in fields(Payment))
_TWO_PLACES = Decimal("0.01")


def _cell(payment, name):
    value = getattr(payment, name)
    if name == "amount":
        return str(Decimal(value).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))
    if name == "value_date":
        return value.isoformat() if value else ""
    if name == "beneficiary_address":
        return json.dumps(value or {}, sort_keys=True)
    return "" if value is None else str(value)


def emit(batch) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
    writer.writeheader()
    for payment in batch.payments:
        writer.writerow({name: _cell(payment, name) for name in COLUMNS})
    return buffer.getvalue()
