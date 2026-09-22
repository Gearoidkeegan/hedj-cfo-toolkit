"""T9: batch assembly and splitting.

`build_batches` is the seam between "a list of approved invoices" and the
one or more `Batch` objects `cfo.payments.model.validate_batch` will accept.
It owns exactly two things: putting the payments in one deterministic
order, and cutting that order into batches of at most `max_payments` --
never refusing an oversized run, because sending someone to a spreadsheet to
split 250 invoices by hand is exactly where the transposition errors come
from (see the module docstring in `cfo.payments.model`). Everything else --
whether a payment's IBAN checks out, whether its amount is a positive
`Decimal`, whether a batch is itself within `max_payments` -- is
`validate_batch`'s job, called here once per produced batch as a guarantee,
never re-implemented.

Each item of `invoices` is duck-typed like `cfo.payments.model.Payment`:
this module reads `.value_date`, `.beneficiary_name` and `.reference` for
the sort key ("due date, then supplier name, then invoice number" in the
brief's words -- a payment's own `reference` is where an invoice number
belongs; it is what `cfo.payments.emit.pain001` writes into `RmtInf/Ustrd`)
and `.currency` for the mixed-currency grouping below. It does not build a
`Payment` out of some other, rawer invoice shape -- by the time a caller has
one to hand this module, extraction (T5), validation (T6) and supplier
enrichment (T7) have already turned it into one.

## Two judgement calls the brief left open

**The shape of the split.** `build_batches` returns a `BatchSplit` -- a
`list` of `Batch`, so every caller that treats the return value as
`list[Batch]` (iterate it, index it, take `len()` of it) keeps working --
with three read-only properties layered on top: `batch_count`,
`payment_count` and `control_total` (the sum of every batch's own
`control_total`). A caller that wants "2 batches, 199 and 51 payments,
X in all" reads `split.batch_count` and `[(b.count, b.control_total) for b
in split]`, without a second return value to keep in step with the first,
and without a fourth field bolted onto `Batch` itself.

**A foreign-currency invoice.** `sell_currency` names the batch run's
primary currency, but nothing forces every invoice into it, and this module
never converts one into it -- see the brief: conversion is a rate decision,
and a rate decision belongs in the summary, where the rate and its date can
be shown, never silently inside a payment file. So a payment whose
`.currency` differs from `sell_currency` is never dropped and never
coerced: it is grouped into a batch (or batches, still capped at
`max_payments`, still sorted the same way) of its own, carrying its own
`sell_currency`, ordered after every `sell_currency` batch -- one foreign
currency at a time, alphabetically, so the order never depends on where in
`invoices` a foreign payment happened to sit. A caller sees exactly what
happened: an extra, correctly-labelled batch, never a batch whose declared
currency does not match what is actually in it, and never a run that stops
dead over one invoice priced in the wrong currency.
"""
from datetime import date
from decimal import Decimal

from cfo.console import ToolkitError
from cfo.payments.model import MAX_PAYMENTS_PER_BATCH, Batch, validate_batch


class BatchSplit(list):
    """The `Batch` objects `build_batches` produced, in the order their
    external references were assigned -- a plain `list[Batch]` to any
    caller that iterates, indexes or takes `len()` of it -- plus the shape
    of the split attached as read-only properties, so the workbook and the
    sign-off step can report "2 batches, 199 and 51 payments, N EUR in all"
    without a second return value to keep in step with the first list."""

    @property
    def batch_count(self):
        return len(self)

    @property
    def payment_count(self):
        return sum(item.count for item in self)

    @property
    def control_total(self):
        return sum((item.control_total for item in self), Decimal("0"))


def _sort_key(payment):
    """(due date, supplier name, invoice number), per the brief -- a total
    order that never raises, even for a payment missing the field a later
    validation step would reject it for. `None` sorts before every real
    date rather than raising `TypeError` when compared against one, so a
    payment with no `value_date` still gets a stable position instead of
    crashing the sort; reporting that it is missing is `validate_batch`'s
    job, not this one's."""
    value_date = payment.value_date
    return (
        value_date is None,
        value_date or date.min,
        payment.beneficiary_name or "",
        payment.reference or "",
    )


def _group_by_currency(payments):
    """`{currency: [payment, ...]}`. Insertion order does not matter here --
    `_currency_order` below is what makes the grouping deterministic."""
    groups = {}
    for payment in payments:
        groups.setdefault(payment.currency, []).append(payment)
    return groups


def _currency_order(groups, sell_currency):
    """`sell_currency` first, when any payment actually carries it, then
    every other currency present, alphabetically -- deterministic
    regardless of the order `invoices` arrived in, and regardless of how
    many distinct foreign currencies show up. A currency with no payments
    in `groups` is never given an empty batch of its own."""
    others = sorted(currency for currency in groups if currency != sell_currency)
    order = [sell_currency] if sell_currency in groups else []
    order.extend(others)
    return order


def build_batches(invoices, *, debtor_account, execution_date, sell_currency,
                  reference_prefix, max_payments=MAX_PAYMENTS_PER_BATCH):
    """One or more `Batch` objects built from `invoices`, returned as a
    `BatchSplit` (see its docstring). Every batch is already within
    `max_payments` -- filled to the cap before the next one starts, so
    "batch 2 of 2" reads as the remainder, not half the run -- and every
    batch's payments are sorted by (due date, supplier name, invoice
    number), so the same input always produces the same batches, external
    references included, regardless of the order `invoices` arrived in.

    A payment whose `.currency` differs from `sell_currency` is grouped
    into its own batch(es), ordered after every `sell_currency` batch, one
    foreign currency at a time, alphabetically -- see the module
    docstring. External references are assigned in that same order, so
    they stay unique and ordered across the whole return value, never
    restarting per currency.

    Every batch this returns is checked against `validate_batch` before
    being handed back; a batch that check would reject raises
    `ToolkitError` naming every problem -- this module's own guarantee
    ("hand it batches it will accept") enforced here, not assumed by
    whoever calls it next. An empty `invoices` returns an empty
    `BatchSplit` without raising: there is nothing to batch, and that is
    not an error.
    """
    invoices = list(invoices)
    split = BatchSplit()
    if not invoices:
        return split

    groups = _group_by_currency(invoices)
    sequence = 1
    for currency in _currency_order(groups, sell_currency):
        payments = sorted(groups[currency], key=_sort_key)
        for start in range(0, len(payments), max_payments):
            chunk = payments[start:start + max_payments]
            produced = Batch(
                debtor_account=debtor_account,
                execution_date=execution_date,
                sell_currency=currency,
                external_reference=f"{reference_prefix}-{sequence:03d}",
                payments=chunk,
            )
            problems = validate_batch(produced, max_payments=max_payments)
            if problems:
                raise ToolkitError(problems)
            split.append(produced)
            sequence += 1
    return split
