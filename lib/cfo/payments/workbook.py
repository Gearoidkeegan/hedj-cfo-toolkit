"""T12: the workbook -- the spreadsheet a finance manager actually works in.

Four sheets, built through `cfo.workbook.build.build_workbook` (C5), the same
JSON-spec engine the policy tool already uses -- this module does not start a
second one, and it never writes OOXML by hand, which is the only thing that
guarantees the acceptance criterion the test suite cannot check for itself:
opening in Excel with no repair prompt.

**Nothing here may disagree with the payment file.** Every total or count
that reaches a cell is read off the `Batch`/`BatchSplit` objects this module
is handed, or off `signoff.control_block`, never recomputed from a different
source -- see `_money`, `_currency_rows` and the assertions in
`build_payments_workbook` itself, which refuse to write a workbook whose
sign-off record and whose live batches disagree.

**Money is `Decimal` all the way to the point this module hands a value to
C5.** `_money` is the single, explicit place a `Decimal` becomes the `float`
an Excel numeric cell (an IEEE double, under the OOXML hood, regardless of
what wrote it) can hold -- one rounding, on an already-exact amount, never a
`float` that has been through prior float arithmetic. That is what makes
`Decimal("0.1") + Decimal("0.2")` reach a cell as `0.3`, not
`0.30000000000000004`: the addition happens in `Decimal` (on `Batch` and
`Payment`, upstream of this module, or in the grouping helpers below), and
`_money` never receives, and never performs, anything else. `_money` raises
on a `float` input rather than coercing it, so a caller that slips a raw
float amount in is caught here, not three sheets later.

**An unreadable document is a row, not an absence.** `unreadable_documents`
-- `[(where, message), ...]`, the same shape every warning in this toolkit
uses -- becomes its own labelled block in the Exceptions sheet. This module
does no extraction itself; it only refuses to let a caller's silence about a
failed read stay silent once it reaches the workbook.

**The Exceptions sheet is one worksheet, not three.** `build_workbook`
gives one column schema to a whole sheet, so the flags, the checks that did
not run and the unreadable documents share one table, distinguished by a
`type` column and ordered into clearly labelled, blank-line-separated
blocks -- never a second tab, and never a footnote.

`cost_categories` is the one input this module needs that nothing upstream
produces yet: `cfo.payments.model.Payment` carries no `cost_category` field
(it is an invoice-level field `cfo.payments.extract` reads, that never made
it onto the payment model -- see that module's `SCALAR_FIELDS`). Without a
mapping, every payment is grouped under "Uncategorised" in the Summary
sheet's cost-category section -- an honest statement that this run has no
category data, never an invented one.

**Task 5 of the payments: staged-output milestone -- the Amendments sheet,
handed over by Task 3.** The spec's acceptance criterion 6 ("every
amendment appears in the audit trail with its reason and its author") was
only half true: `amendments.json` (`cfo.payments.amend`, Tasks 1/3/4)
records every change, but that file lives inside the run folder, where a
reviewer, an auditor or a financial controller will never look.
`_amendments_sheet` below projects that same record onto this workbook --
one row per entry, in the order it was recorded, never re-derived or
re-decided (see its own docstring): this module still never disagrees
with a different source of truth, it just adds a fifth place the one
that already exists is actually read.

**F1 (Summary sheet review, 2026-09-23).** The Payments sheet has known
about exclusions since N4: an excluded row is marked, and a "Run total
(instructed)" sits beside the reviewed "Run total". The Summary sheet's
own grouped tables did not -- built from the full reviewed set with no
`dropped`/`excluded` concept anywhere in `_currency_rows` or the grouping
helpers, so a payee, cost category or due week with every payment
excluded from it still read as an ordinary total, on the one sheet a
reader opens first. `_currency_rows`/`_grouped_rows` now take the same
`dropped_references` the Payments sheet already has, and add an
`instructed_amount` column beside `amount` -- mirroring that sheet's own
wording rather than inventing a second one, and left blank wherever the
two figures would be identical (see `_add_instructed_amount`). The two
sheets' totals are asserted, not merely hoped, to reconcile
(`_assert_reconciles_with_payments_sheet`).
"""
import os
from datetime import date
from decimal import Decimal

from cfo import runs
from cfo.console import ToolkitError
from cfo.io import locked, read_json
from cfo.market.rates import convert, ecb_rates
from cfo.payments import amend, signoff
from cfo.payments.fraud import FLAG_EXCEPTION_MESSAGE_RE as _FLAG_EXCEPTION_MESSAGE_RE
from cfo.workbook.build import build_workbook

SHEET_PAYMENTS = "Payments"
SHEET_EXCEPTIONS = "Exceptions"
SHEET_SUMMARY = "Summary"
SHEET_SIGNOFF = "Sign-off"
SHEET_AMENDMENTS = "Amendments"

UNCATEGORISED = "Uncategorised"

NOT_YET_REVIEWED_STATEMENT = (
    "Not yet reviewed. The 'Reviewed by' and 'Reviewed at' fields above are blank "
    "because sign-off has not happened for this run -- not because no one needed to "
    "review it."
)


def _money(value):
    """The `float` an Excel numeric cell needs, from an exact `Decimal` --
    the one, explicit, one-time rounding this module performs on money. A
    `float` given here is refused rather than accepted: money must already
    be `Decimal` by the time it reaches this function, never something that
    has already been through binary float arithmetic (see the module
    docstring)."""
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise ToolkitError(("amount", f"expected a Decimal amount, got {type(value).__name__} "
                            f"({value!r}) -- money must be Decimal all the way to this point"))
    return float(value)


def _compared(flag):
    """(a, b) out of a `Flag`'s `compared` dict, whichever pair of keys it
    used -- see `cfo.payments.fraud.Flag`'s own docstring: always
    `{"old", "new"}` or `{"a", "b"}`, always both."""
    compared = flag.compared or {}
    if "old" in compared or "new" in compared:
        return compared.get("old", ""), compared.get("new", "")
    return compared.get("a", ""), compared.get("b", "")


# A flag-derived exception's message, as `cfo.payments.cli.cmd_review` writes
# it today: "[check_id] <flag.summary>" -- see `_flag_dispositions` below.
# Imported from `cfo.payments.fraud`, the one place this format's regex is
# defined, rather than compiled a second time here (N5's "Also" note,
# second review: this copy was identical to `cfo.payments.render_fraud`'s
# own but, unlike that one, had no contract test pinning it -- and it is
# the copy a reviewer actually opens).


def _read_signoff_state(run_dir):
    """`signoff.json` read directly -- H6: a workbook must be able to show
    a reviewer's decision without waiting on, or depending on, anything a
    caller (`cfo.payments.cli`) does with it. Goes straight to the file
    `cfo.payments.signoff` itself writes (`signoff.STATE_FILENAME`, under
    the same `locked()` a concurrent writer holds, so this never reads a
    half-written file) rather than through that module's own read
    functions, every one of which raises when no review has been started
    yet -- and a workbook (the NOT-REVIEWED draft) must still build in that
    case, simply showing no disposition for anything. Returns `None` for a
    run with no signoff.json at all."""
    path = os.path.join(run_dir, signoff.STATE_FILENAME)
    with locked(path):
        return read_json(path)


def _flag_dispositions(run_dir):
    """`{(check, where, summary): {"accepted", "reason", "by", "at"}}` --
    the reviewer's own decision, from `signoff.json`, for every fraud flag
    this run's exceptions cover; a flag with no matching exception, or one
    with no disposition recorded yet, is simply absent (never a fabricated
    "Accepted").

    Each of `signoff.json`'s own `exceptions` entries is matched back to
    the flag it was raised for by its `where` and the `[check] summary` a
    flag-derived exception's own `message` carries (`cmd_review` writes it
    as `f"[{flag.check}] {flag.summary}"`; see `_FLAG_EXCEPTION_MESSAGE_RE`)
    -- an exception that does not carry the bracket prefix at all (an
    unreadable document, a failed invoice, ...) is not a flag and is simply
    skipped here.

    **N5 (second review):** keyed on `(check, where, summary)`, not
    `(check, where)` alone -- `_check_lookalike_sender_domain` can raise up
    to three flags for one invoice sharing the same `(check, where)` pair
    (its module docstring: one comparison against the domain on file, one
    against the domain printed on the invoice, one against another invoice
    in the batch), and a `(check, where)`-only key silently collapsed all
    three to whichever was processed last -- a finding the reviewer
    rejected could render as accepted. `summary` is exactly what
    distinguishes them (each comparison names which one found it), and it
    is already right there in the exception's own message, after the
    prefix this same regex already strips -- no new field to carry, no
    second place for the writer and the reader to drift apart.

    When an exception has been dispositioned more than once (a changed
    mind -- see `signoff.disposition`'s own docstring: a second call is a
    new entry, not a correction of the first, and both survive in order),
    this uses the LATEST one, because that is what the reviewer actually
    decided in the end."""
    state = _read_signoff_state(run_dir)
    if not state:
        return {}
    latest_by_id = {}
    for entry in state.get("dispositions", []):
        latest_by_id[str(entry["exception_id"])] = entry
    dispositions = {}
    for exc in state.get("exceptions", []):
        message = str(exc.get("message", ""))
        match = _FLAG_EXCEPTION_MESSAGE_RE.match(message)
        if not match:
            continue
        summary = message[match.end():]
        entry = latest_by_id.get(str(exc.get("id")))
        if entry is not None:
            dispositions[(match.group("check"), exc.get("where"), summary)] = entry
    return dispositions


def _rate_cache_dir(run_dir):
    """Where `_RateCache` caches, and falls back to, ECB reference rates --
    `cfo.runs.cache_dir_of`'s own tree, shared by every run under this runs
    root (never this run alone: ECB rates are the same for everyone on this
    machine on a given day, so a run tomorrow, or a different company's run
    today, should get to reuse a rate an earlier run already fetched), under
    its own 'market-rates' subdirectory so this module's date-named cache
    files can never collide with `cfo.tasks.cache`'s own hash-keyed ones
    living in the same `.cache` tree (B4)."""
    return os.path.join(runs.cache_dir_of(run_dir), "market-rates")


class _RateCache:
    """Fetches `ecb_rates()` at most once -- including a failed attempt,
    which is cached too, so a network or SSL failure does not retry once per
    conversion this run needs (B4: an SSL verification failure, the kind a
    corporate TLS-inspecting proxy produces, is exactly the case that made
    every retry pointless -- it fails the same way every time). A
    single-currency run, or a caller that already supplied `rates`, never
    makes a network call at all.

    `cache_dir`, when given, is passed straight through to `ecb_rates` --
    this is what makes its own "fall back to the most recently cached date"
    branch reachable at all (B4: before this, `ecb_rates()` was always
    called with no `cache_dir`, so that branch, and the cache it depends on,
    could never be reached, and "no cached rates are available" was the
    only possible failure)."""

    def __init__(self, rates=None, cache_dir=None):
        self._rates = rates
        self._cache_dir = cache_dir
        self._error = None

    def get(self):
        if self._rates is None and self._error is None:
            try:
                self._rates = ecb_rates(cache_dir=self._cache_dir)
            except ToolkitError as exc:
                self._error = exc
        if self._error is not None:
            raise self._error
        return self._rates


def _payments_columns():
    return [
        {"key": "row_type", "label": "Row", "type": "text", "width": 16, "required": True},
        {"key": "batch", "label": "Batch", "type": "text", "width": 14},
        {"key": "currency", "label": "Currency", "type": "text", "width": 10},
        {"key": "value_date", "label": "Value date", "type": "date"},
        {"key": "beneficiary", "label": "Beneficiary", "type": "text", "width": 28},
        {"key": "reference", "label": "Reference", "type": "text", "width": 18},
        {"key": "iban_or_account", "label": "IBAN / account", "type": "text", "width": 26},
        {"key": "amount", "label": "Amount", "type": "number", "format": "#,##0.00", "width": 16},
    ]


EXCLUDED_ROW_TYPE = "Excluded" + chr(0x2014) + "exception rejected"


def _payments_sheet(batches, dropped_references=()):
    """`batches` is always the run's full, UNFILTERED set -- every count and
    total here must match the signed-off record (see this module's own
    docstring), so a rejected payment's row is never simply left out.
    Instead, when `dropped_references` names it (N4, second review), the
    row is marked `EXCLUDED_ROW_TYPE` rather than "Payment", and an
    "Instructed" total is added beside the existing "Batch subtotal"/"Run
    total" rows (kept exactly as before, unmarked and unrenamed, when
    `dropped_references` is empty -- the ordinary, nothing-rejected case,
    so an existing reader of this sheet sees no change at all).

    Before this, a run with four rejected payments listed all of them as
    ordinary "Payment" rows with no marker at all, and a "Run total" above
    what the bank was actually told to send -- a finance manager
    reconciling the bank's own confirmation found a gap this sheet gave no
    explanation for, in a file that otherwise looks like the audit record
    of what was paid."""
    dropped_references = set(dropped_references or ())
    columns = _payments_columns()
    rows = []
    for batch in batches:
        for payment in batch.payments:
            excluded = payment.reference in dropped_references
            rows.append({
                "row_type": EXCLUDED_ROW_TYPE if excluded else "Payment",
                "batch": batch.external_reference,
                "currency": payment.currency, "value_date": payment.value_date,
                "beneficiary": payment.beneficiary_name, "reference": payment.reference,
                "iban_or_account": payment.iban or payment.account_number,
                # Read off the payment itself, the same field the batch's own
                # control_total sums -- never a value invented for display.
                # Left in place (never zeroed or blanked) for an excluded
                # row too: a finance manager reconciling a gap needs the
                # exact amount that was excluded, not just that one was.
                "amount": _money(payment.amount),
            })
        rows.append({
            "row_type": "Batch subtotal", "batch": batch.external_reference,
            "currency": batch.sell_currency,
            # Batch.control_total, read directly -- never re-summed from the
            # payment rows just written above (rule: never a different source).
            # This is the REVIEWED figure -- every payment this batch held
            # before any rejection -- on purpose; see the "instructed"
            # subtotal below for what was actually sent.
            "amount": _money(batch.control_total),
        })
        if dropped_references:
            instructed = sum((p.amount for p in batch.payments
                              if p.reference not in dropped_references), Decimal("0"))
            rows.append({
                "row_type": "Batch subtotal (instructed)", "batch": batch.external_reference,
                "currency": batch.sell_currency, "amount": _money(instructed),
            })
    if batches:
        currencies = {batch.sell_currency for batch in batches}
        currency_label = next(iter(currencies)) if len(currencies) == 1 else "Mixed"
        run_total = sum((batch.control_total for batch in batches), Decimal("0"))
        rows.append({
            "row_type": "Run total", "batch": "",
            "currency": currency_label,
            # The REVIEWED total, unchanged from before N4 -- every payment
            # this run held before any rejection. Kept as "Run total" (not
            # renamed) so an existing reader, or an earlier saved copy of
            # this workbook, is not asked to relearn what this row means.
            "amount": _money(run_total),
        })
        if dropped_references:
            all_payments = [p for batch in batches for p in batch.payments]
            instructed_total = sum((p.amount for p in all_payments
                                    if p.reference not in dropped_references), Decimal("0"))
            rows.append({
                "row_type": "Run total (instructed)", "batch": "",
                "currency": currency_label,
                # N4: what the bank was actually told to send -- the figure
                # a finance manager reconciling the bank's own confirmation
                # needs beside the reviewed one above, with the difference
                # between the two explained by the excluded rows themselves.
                "amount": _money(instructed_total),
            })
    return {"name": SHEET_PAYMENTS, "freeze": "A2", "columns": columns, "rows": rows}


def _exceptions_columns():
    return [
        {"key": "type", "label": "Type", "type": "text", "width": 18, "required": True},
        {"key": "check", "label": "Check", "type": "text", "width": 24},
        {"key": "severity", "label": "Severity", "type": "text", "width": 12},
        {"key": "where", "label": "Where", "type": "text", "width": 16},
        {"key": "summary", "label": "Summary", "type": "text", "width": 60},
        {"key": "compared_a", "label": "Compared (a)", "type": "text", "width": 24},
        {"key": "compared_b", "label": "Compared (b)", "type": "text", "width": 24},
        {"key": "source", "label": "Source", "type": "text", "width": 30},
        {"key": "accepted_rejected", "label": "Accepted / rejected", "type": "enum",
         "options": ["Accepted", "Rejected"], "width": 18},
        {"key": "reviewer_reason", "label": "Reviewer's reason", "type": "text", "width": 40},
    ]


def _divider_row(summary):
    """A labelled section break within the one Exceptions worksheet -- never
    a blank `type` (that column is required, and a blank would only earn a
    spurious "is empty" warning on every run), and never a second tab."""
    return {"type": "Section", "summary": summary}


def _exceptions_sheet(flags, performed, unreadable_documents, flag_dispositions=None):
    flag_dispositions = flag_dispositions or {}
    rows = []
    if not flags:
        rows.append({"type": "Note", "summary": "No fraud flags were raised for this run."})
    for flag in flags:
        compared_a, compared_b = _compared(flag)
        row = {
            "type": "Flag", "check": flag.check, "severity": flag.severity, "where": flag.where,
            "summary": flag.summary, "compared_a": compared_a, "compared_b": compared_b,
            "source": flag.source,
        }
        # H6: the two columns below already existed and were left `None` on
        # every row -- a reviewer's rejection recorded in signoff.json never
        # reached this sheet. Populated only when a disposition is actually
        # on record (see `_flag_dispositions`); otherwise left blank, same as
        # before, which is the honest answer for a run not yet reviewed.
        disposition = flag_dispositions.get((flag.check, flag.where, flag.summary))
        if disposition is not None:
            row["accepted_rejected"] = "Accepted" if disposition.get("accepted") else "Rejected"
            row["reviewer_reason"] = disposition.get("reason", "")
        rows.append(row)

    if unreadable_documents:
        rows.append(_divider_row("UNREADABLE DOCUMENTS -- these could not be read at all, and "
                                 "produced no payment line"))
        for where, message in unreadable_documents:
            rows.append({"type": "Unreadable document", "where": str(where),
                        "summary": str(message), "source": "extraction"})

    not_performed = [(check_id, entry) for check_id, entry in (performed or {}).items()
                     if not entry.get("performed", True)]
    rows.append(_divider_row("CHECKS NOT PERFORMED FOR THIS RUN -- see each reason before "
                             "treating a clean report as a clean batch"))
    if not_performed:
        for check_id, entry in not_performed:
            rows.append({"type": "Not performed", "check": check_id,
                        "summary": entry.get("reason", "")})
    else:
        rows.append({"type": "Note", "summary": "Every fraud check that this run could run, ran."})

    return {"name": SHEET_EXCEPTIONS, "freeze": "A2", "columns": _exceptions_columns(), "rows": rows}


def _summary_columns():
    return [
        {"key": "section", "label": "Section", "type": "text", "width": 18, "required": True},
        {"key": "group", "label": "Group", "type": "text", "width": 28, "required": True},
        {"key": "currency", "label": "Currency", "type": "text", "width": 10},
        {"key": "count", "label": "Payments", "type": "number", "format": "0", "width": 12},
        {"key": "amount", "label": "Amount", "type": "number", "format": "#,##0.00", "width": 16},
        # F1 (Summary sheet review, 2026-09-23): the instructed figure
        # beside the reviewed one above -- mirrors the Payments sheet's
        # own "Run total" / "Run total (instructed)" wording (see
        # `_payments_sheet`) rather than a new label. Left blank (never
        # repeated) when this group has nothing excluded from it -- see
        # `_add_instructed_amount` below.
        {"key": "instructed_amount", "label": "Amount (instructed)", "type": "number",
         "format": "#,##0.00", "width": 16},
        {"key": "converted_amount", "label": "Converted amount", "type": "number",
         "format": "#,##0.00", "width": 18},
        {"key": "converted_currency", "label": "Converted currency", "type": "text", "width": 14},
        {"key": "rate", "label": "Rate", "type": "number", "format": "0.0000", "width": 12},
        {"key": "rate_date", "label": "Rate date", "type": "date", "width": 14},
        # E1: what this rate actually is, in the same row as the rate and
        # its date -- ECB reference, mid-market, indicative -- and, when
        # `rate_table["stale"]` says so, that it is not current and the
        # date it is actually from. Populated only when a conversion
        # succeeded (see `_rate_basis_note`); left blank, like `rate`
        # itself, for a row that was not converted at all.
        {"key": "rate_basis", "label": "Rate basis", "type": "text", "width": 70},
        # B4: populated only when a conversion this row needed could not be
        # done -- no ECB rate reachable at all, or none for this specific
        # currency -- so the row says plainly it was not converted, and why,
        # rather than the workbook failing to build over one missing rate.
        {"key": "conversion_note", "label": "Conversion note", "type": "text", "width": 50},
    ]


_RATE_BASIS = "ECB euro reference rate: mid-market and indicative; your bank's own rate will differ"


def _rate_basis_note(rate_table):
    """One plain sentence for the Summary row a converted figure already
    sits in -- never a footnote -- naming what that number actually is:
    the ECB's own euro reference rate, a mid-market figure published once
    a business day, indicative rather than dealable, and a plain
    statement that the customer's own bank will quote something
    different (its spread sits inside that quote rather than showing as
    a separate fee) -- so a reader is never left thinking this is what
    left, or will leave, the bank account.

    When `rate_table` is `ecb_rates`'s own cache-fallback result
    (`stale: True` -- see that module's docstring: a real path, reached
    whenever the ECB is unreachable, which a corporate TLS-inspecting
    network hits routinely), the same sentence also names the actual
    date the rate is from and says plainly it is not current -- before
    this function existed, `stale` was read nowhere in this file (grep
    for it turned up nothing), so a rate of unknown age rendered
    identically to today's."""
    if rate_table.get("stale"):
        return (f"{_RATE_BASIS}; and this one is not current -- the latest reachable rate is "
                f"dated {rate_table.get('date')}, not today's.")
    return f"{_RATE_BASIS}."


def _record_conversion_failure(currency, home_currency, exc, warnings):
    """Plain, finance-manager-facing text for the cell -- 'not converted',
    and why -- not a stack trace, built from `exc.problems`'s own first
    message (`ToolkitError`'s shape; see `cfo.console.ToolkitError`).
    Also appends a deduplicated `("market.rates", note)` entry to
    `warnings` (deduplicated so a currency missing from every one of the
    "By payee" / "By cost category" / "By due week" groupings does not
    repeat the same line once per group)."""
    reason = exc.problems[0][1] if getattr(exc, "problems", None) else str(exc)
    note = (f"Not converted to {home_currency}: no exchange rate is available for {currency} "
            f"-- {reason}")
    if warnings is not None and not any(existing == note for _where, existing in warnings):
        warnings.append(("market.rates", note))
    return note


def _conversion_fields(native_total, currency, home_currency, rate_cache, warnings):
    """`{}` when `currency` is `home_currency` -- nothing was converted, so
    nothing is claimed. Otherwise the converted amount, the currency it was
    converted into, the rate used, the date it came from, and (E1) one
    plain sentence naming what kind of rate it is -- always all five
    together, in the same row, per the brief: a converted figure with no
    rate, or no statement of what the rate is, is a number nobody can
    check or trust at face value.

    **B4:** neither `rate_cache.get()` (no rate reachable at all -- offline,
    or an SSL verification failure, which a corporate TLS-inspecting proxy
    produces for a user who is not offline at all) nor `convert()` (a rate
    table that simply has no entry for this particular currency) may raise
    out of this function. Either failure degrades this row to a single
    `conversion_note` field that says plainly the figure was not converted,
    and why -- never the whole workbook lost over one missing rate. That
    row carries no `rate_basis` either: a figure that was never converted
    has no rate to describe."""
    if not home_currency or currency == home_currency:
        return {}
    try:
        rate_table = rate_cache.get()
        converted = convert(native_total, currency, home_currency, rate_table)
        unit_rate = convert(Decimal("1"), currency, home_currency, rate_table)
    except ToolkitError as exc:
        return {"conversion_note": _record_conversion_failure(currency, home_currency, exc,
                                                              warnings)}
    return {"converted_amount": _money(converted), "converted_currency": home_currency,
           "rate": _money(unit_rate), "rate_date": rate_table["date"],
           "rate_basis": _rate_basis_note(rate_table)}


def _instructed_total(payments, dropped_references):
    """The same `payments`, summed with any `dropped_references` excluded
    -- the "instructed" half of the pair `_payments_sheet` already shows
    for the whole run (Payment.amount, never a different source, just the
    same filter that sheet's own "Run total (instructed)" applies)."""
    return sum((p.amount for p in payments if p.reference not in dropped_references),
              Decimal("0"))


def _add_instructed_amount(row, native_total, payments, dropped_references):
    """Sets `row["instructed_amount"]` only when it would actually differ
    from `row["amount"]` -- F1 (Summary sheet review, 2026-09-23): "where
    a group's two figures are equal, one is enough; a reader should not
    have to scan a column of identical pairs to find the one that
    differs." Left unset (blank cell) for the ordinary, nothing-excluded
    case, and for a group nothing was excluded from even when other groups
    in the same run had exclusions -- so the control (no exclusions at
    all) and an unaffected group inside an otherwise-affected run both
    read exactly as before this fix."""
    if not dropped_references:
        return
    instructed_total = _instructed_total(payments, dropped_references)
    if instructed_total != native_total:
        row["instructed_amount"] = _money(instructed_total)


def _currency_rows(batches, home_currency, rate_cache, warnings, dropped_references=()):
    totals, counts, instructed_totals = {}, {}, {}
    for batch in batches:
        # Batch.control_total and Batch.count, read directly and summed --
        # never a re-derivation from the individual payments (rule: never a
        # different source than the batch the payment file was built from).
        totals[batch.sell_currency] = totals.get(batch.sell_currency, Decimal("0")) + batch.control_total
        counts[batch.sell_currency] = counts.get(batch.sell_currency, 0) + batch.count
        # F1: the instructed half, from the batch's own payments -- the
        # same per-payment filter `_payments_sheet` already applies, just
        # grouped by currency instead of by batch.
        if dropped_references:
            instructed_totals[batch.sell_currency] = (
                instructed_totals.get(batch.sell_currency, Decimal("0")) +
                _instructed_total(batch.payments, dropped_references))
    rows = []
    for currency in sorted(totals):
        row = {"section": "By currency", "group": currency, "currency": currency,
              "count": counts[currency], "amount": _money(totals[currency])}
        if dropped_references:
            instructed_total = instructed_totals.get(currency, Decimal("0"))
            if instructed_total != totals[currency]:
                row["instructed_amount"] = _money(instructed_total)
        row.update(_conversion_fields(totals[currency], currency, home_currency, rate_cache,
                                      warnings))
        rows.append(row)
    return rows, totals, instructed_totals


def _grouped_rows(section, payments, key_fn, home_currency, rate_cache, warnings,
                  dropped_references=()):
    groups = {}
    for payment in payments:
        key = key_fn(payment)
        bucket = groups.setdefault(key, [])
        bucket.append(payment)
    rows = []
    for (label, currency) in sorted(groups):
        group_payments = groups[(label, currency)]
        # Payment.amount, the same field Batch.control_total itself sums --
        # not a different source, just a different grouping of it.
        native_total = sum((p.amount for p in group_payments), Decimal("0"))
        row = {"section": section, "group": label, "currency": currency,
              "count": len(group_payments), "amount": _money(native_total)}
        _add_instructed_amount(row, native_total, group_payments, dropped_references)
        row.update(_conversion_fields(native_total, currency, home_currency, rate_cache, warnings))
        rows.append(row)
    return rows


def _due_week(payment):
    if not isinstance(payment.value_date, date):
        return "No value date"
    iso_year, iso_week, _weekday = payment.value_date.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _assert_matches_batches(batches, all_payments):
    """The per-currency totals this sheet is about to show, computed two
    ways from the same underlying data -- once by summing each Batch's own
    control_total, once by summing the individual payments -- must agree.
    Both read Payment.amount in the end (Batch.control_total is defined as
    its own sum of it), so this is not a second, independent source; it is
    the assertion the brief asks for that nothing here has drifted apart."""
    from_batches = {}
    for batch in batches:
        from_batches[batch.sell_currency] = (
            from_batches.get(batch.sell_currency, Decimal("0")) + batch.control_total)
    from_payments = {}
    for payment in all_payments:
        from_payments[payment.currency] = (
            from_payments.get(payment.currency, Decimal("0")) + payment.amount)
    if from_batches != from_payments:
        raise ToolkitError(("summary", "the per-currency totals computed from the batches do "
                            "not match the sum of the individual payments -- refusing to write "
                            "a workbook that could disagree with the payment file"))


def _assert_reconciles_with_payments_sheet(batches, dropped_references, currency_totals,
                                           currency_instructed_totals):
    """F1 (Summary sheet review, 2026-09-23), the acceptance criterion's own
    words: 'the totals across the grouped tables reconcile with the
    Payments sheet's own two totals. Assert this, because two places
    computing the same number is how they come to disagree.'

    `currency_totals`/`currency_instructed_totals` are `_currency_rows`'s
    own per-currency figures -- summed across every currency here, exactly
    the way `_payments_sheet`'s "Run total"/"Run total (instructed)" rows
    already mix currencies together (a pre-existing choice of that sheet's,
    not one this assertion tries to fix). Both totals below are computed a
    THIRD way, straight off `batches`, the same object `_payments_sheet`
    itself sums -- not a second read of the same numbers `_currency_rows`
    already produced, which would only prove this function can copy its
    own arithmetic."""
    reviewed_total = sum((batch.control_total for batch in batches), Decimal("0"))
    if sum(currency_totals.values(), Decimal("0")) != reviewed_total:
        raise ToolkitError(("summary", "the Summary sheet's own reviewed total does not match "
                            "the Payments sheet's Run total -- refusing to write a workbook "
                            "whose two sheets could disagree about the same number"))
    if not dropped_references:
        return
    all_payments = [payment for batch in batches for payment in batch.payments]
    instructed_total = _instructed_total(all_payments, dropped_references)
    if sum(currency_instructed_totals.values(), Decimal("0")) != instructed_total:
        raise ToolkitError(("summary", "the Summary sheet's own instructed total does not match "
                            "the Payments sheet's Run total (instructed) -- refusing to write a "
                            "workbook whose two sheets could disagree about the same number"))


def _summary_sheet(batches, home_currency, rates, cost_categories, run_dir, warnings,
                   dropped_references=()):
    # F1 (Summary sheet review, 2026-09-23): normalised once, exactly as
    # `_payments_sheet` normalises its own copy of the same set, so every
    # helper below can treat an absent set and an empty one alike.
    dropped_references = set(dropped_references or ())
    cache_dir = _rate_cache_dir(run_dir) if run_dir else None
    rate_cache = _RateCache(rates, cache_dir)
    all_payments = [payment for batch in batches for payment in batch.payments]
    _assert_matches_batches(batches, all_payments)

    currency_rows, totals, instructed_totals = _currency_rows(batches, home_currency, rate_cache,
                                                               warnings, dropped_references)
    _assert_reconciles_with_payments_sheet(batches, dropped_references, totals, instructed_totals)
    payee_rows = _grouped_rows("By payee", all_payments,
                              lambda p: (p.beneficiary_name or "(no beneficiary)", p.currency),
                              home_currency, rate_cache, warnings, dropped_references)
    category_rows = _grouped_rows(
        "By cost category", all_payments,
        lambda p: ((cost_categories or {}).get(p.reference) or UNCATEGORISED, p.currency),
        home_currency, rate_cache, warnings, dropped_references)
    week_rows = _grouped_rows("By due week", all_payments, lambda p: (_due_week(p), p.currency),
                              home_currency, rate_cache, warnings, dropped_references)

    rows = currency_rows + payee_rows + category_rows + week_rows
    return {"name": SHEET_SUMMARY, "freeze": "A2", "columns": _summary_columns(), "rows": rows}


def _signoff_columns():
    return [
        {"key": "field", "label": "Field", "type": "text", "width": 22, "required": True},
        {"key": "value", "label": "Value", "type": "text", "width": 60},
        {"key": "amount", "label": "Amount", "type": "number", "format": "#,##0.00", "width": 16},
        {"key": "sha256", "label": "SHA-256", "type": "text", "width": 68},
    ]


def _signoff_sheet(control):
    reviewed_by = control["reviewed_by"]
    reviewed_at = control["reviewed_at"]
    review_status = (f"Reviewed by {reviewed_by} at {reviewed_at}." if reviewed_by
                     else NOT_YET_REVIEWED_STATEMENT)
    rows = [
        {"field": "Run ID", "value": control["run_id"]},
        {"field": "Tool version", "value": control["tool_version"]},
        # Defect F1 (Summary sheet review, 2026-09-23), small item:
        # `signoff.prepared_by_label` annotates the raw account name
        # (`getpass.getuser()`, an operational detail -- see that
        # module's own docstring) so it never reads as a person, sitting
        # right above "Reviewed by" below.
        {"field": "Prepared by", "value": signoff.prepared_by_label(control["prepared_by"])},
        {"field": "Prepared at", "value": control["prepared_at"]},
        # Left blank (not a placeholder string) exactly when control_block
        # says so -- the words explaining why live in "Review status" below,
        # never inside these two fields themselves.
        {"field": "Reviewed by", "value": reviewed_by},
        {"field": "Reviewed at", "value": reviewed_at},
        {"field": "Review status", "value": review_status},
        {"field": "What sign-off means", "value": signoff.ATTESTATION_STATEMENT},
        {"field": "Batch count", "value": str(control["batch_count"])},
        {"field": "Payment count", "value": str(control["payment_count"])},
        {"field": "Control total", "amount": _money(control["control_total"])},
    ]
    for entry in control["source_files"]:
        rows.append({"field": "Source file", "value": entry["name"], "sha256": entry["sha256"]})
    return {"name": SHEET_SIGNOFF, "freeze": "A2", "columns": _signoff_columns(), "rows": rows}


# The `change` values `cfo.payments.amend`/`cfo.payments.cli` actually
# record (`amend.EXCLUDE`/`amend.RESTORE`/`amend.VALUE_DATE`, and the plain
# literal `"remittance"` `cmd_amend` itself writes -- there is no constant
# for that one; see `cli.cmd_amend`), given a plain-words label for the
# "what changed" column. An unrecognised `change` (a future amendment kind
# this module has not been taught about) falls back to itself in
# `_amendments_sheet` rather than disappearing -- see there.
_CHANGE_LABELS = {
    amend.EXCLUDE: "Excluded",
    amend.RESTORE: "Restored",
    amend.VALUE_DATE: "Value date changed",
    "remittance": "Remittance changed",
}


def _amendments_columns():
    return [
        {"key": "reference", "label": "Reference", "type": "text", "width": 18, "required": True},
        {"key": "change", "label": "Change", "type": "text", "width": 22, "required": True},
        {"key": "resolution", "label": "Resolution", "type": "text", "width": 16},
        {"key": "from", "label": "From", "type": "text", "width": 26},
        {"key": "to", "label": "To", "type": "text", "width": 26},
        {"key": "reason", "label": "Reason", "type": "text", "width": 40},
        {"key": "by", "label": "By", "type": "text", "width": 20},
        {"key": "at", "label": "When", "type": "text", "width": 24},
    ]


def _amendments_sheet(run_dir):
    """One row per amendment ever recorded for this run
    (`amend.amendments(run_dir)`), in the exact order `amend.record`
    appended them -- the audit trail's own order, never re-sorted and
    never re-decided. This is the fix for the gap Task 3 flagged and
    declined to close unilaterally (`workbook.py` was not in its file
    list): `amendments.json` alone answers "who changed this payment, and
    why?" only for a developer who knows to open a JSON file inside the
    run folder -- this is the same answer, in the one place a reviewer,
    an auditor or a financial controller will actually look.

    Each row is a straight projection of the stored entry's own
    `reference`/`change`/`from`/`to`/`resolution`/`reason`/`by`/`at` keys
    -- nothing here recomputes what changed or infers a reason; `change`
    gets a plain-words label (`_CHANGE_LABELS`) purely for readability,
    falling back to the raw value for a kind this module does not
    recognise, so nothing is ever silently dropped. `resolution` is blank
    for every amendment except a value-date one that actually crossed a
    batch boundary and was resolved -- naming exactly which of the three
    choices (`keep-in-batch`/`own-batch`/`hold-back`) the reviewer chose,
    because that is exactly the thing a later reader will want to
    understand. A `hold-back` resolution is recorded with `change` set to
    the same `exclude` a plain `--exclude` uses (Task 4's own choice, so
    it needs no second exclusion code path) -- the `Resolution` column is
    what tells the two apart here: blank for a plain exclusion, named for
    one that came from a value-date decision.

    Empty -- zero rows, never a broken reference -- for a run with no
    amendments recorded at all: the ordinary case, and the honest answer
    ("nothing to audit yet"), not "amendments are unsupported here"."""
    rows = []
    for entry in amend.amendments(run_dir):
        change = entry.get("change")
        rows.append({
            "reference": entry.get("reference", ""),
            "change": _CHANGE_LABELS.get(change, change),
            "resolution": entry.get("resolution"),
            "from": entry.get("from"),
            "to": entry.get("to"),
            "reason": entry.get("reason", ""),
            "by": entry.get("by", ""),
            "at": entry.get("at", ""),
        })
    return {"name": SHEET_AMENDMENTS, "freeze": "A2", "columns": _amendments_columns(),
           "rows": rows}


def _assert_control_matches_batches(control, batches):
    """`signoff.control_block`'s own recorded batch_count/payment_count/
    control_total, checked against the live Batch objects this call was
    actually given -- the same "never disagree with the payment file" rule
    the rest of this module holds to, applied to the one sheet built from a
    second, independently-stored record rather than straight from
    `batches`."""
    live_batch_count = len(batches)
    live_payment_count = sum(batch.count for batch in batches)
    live_control_total = sum((batch.control_total for batch in batches), Decimal("0"))
    recorded = (control["batch_count"], control["payment_count"], control["control_total"])
    live = (live_batch_count, live_payment_count, live_control_total)
    if recorded != live:
        raise ToolkitError(("sign-off", f"the run's recorded control block {recorded} does not "
                            f"match the batches given to the workbook {live} -- refusing to "
                            "write a workbook that could disagree with the payment file"))


def build_payments_workbook(out_path, run_dir, batches, flags, performed, *,
                            unreadable_documents=(), home_currency=None, rates=None,
                            cost_categories=None, brand=None, dropped_references=()):
    """Builds the five-sheet payments workbook at `out_path` through C5's
    `build_workbook` -- Payments, Exceptions, Summary, Sign-off and
    Amendments.

    `batches` is whatever `cfo.payments.batch.build_batches` returned (a
    `BatchSplit`, or any plain iterable of `Batch`) -- read directly for
    every total and count this module writes, never recomputed from the
    individual payments except where the brief asks for a different
    grouping of the very same field (see `_grouped_rows`). `flags` is the
    list of `Flag` from `cfo.payments.fraud.check_batch`; `performed` is
    `cfo.payments.fraud.checks_performed`'s own result.
    `signoff.control_block(run_dir)` is called directly here, and checked
    against `batches` before anything is written (`_assert_control_matches_
    batches`) -- a run whose sign-off record disagrees with the batches it
    was given raises rather than writing a workbook that could disagree
    with the payment file.

    `unreadable_documents` -- `[(where, message), ...]` -- is this module's
    own input for the documents an extractor could not read at all; nothing
    upstream that this module already consumes carries that list, so a
    caller supplies it directly (see the module docstring). `home_currency`
    defaults to the first batch's own `sell_currency` when not given, and
    `rates` (a `cfo.market.rates.ecb_rates()`-shaped dict) is fetched fresh,
    at most once, only if a conversion actually needs it and none was
    supplied -- through `run_dir`'s own persistent rate cache (see
    `_rate_cache_dir`), and never allowed to raise: a currency this run
    cannot get a rate for degrades to a `conversion_note` explaining why
    rather than losing the whole workbook (B4). `cost_categories` --
    `{payment.reference: category}` -- is optional; a payment with no entry
    is grouped under "Uncategorised", not dropped and not guessed at.

    Every Exceptions-sheet row for a fraud flag also carries the
    reviewer's own decision, read straight from `run_dir`'s `signoff.json`
    (`_flag_dispositions`) -- a rejected exception shows "Rejected" here,
    not a blank cell a reader could mistake for "approved" (H6).

    `dropped_references` -- the `payment.reference` values
    `cfo.payments.cli._filter_rejected_payments` actually removed, if any
    -- marks the corresponding rows in the Payments sheet as excluded and
    adds an "instructed" total beside the existing reviewed one, rather
    than leaving a rejected payment reading as an ordinary, unremarkable
    line (N4, second review). Optional, and empty by default: `batches`
    itself is still always the run's full, unfiltered set (see
    `_payments_sheet`'s own docstring) -- this parameter only changes how
    those same rows are labelled, never which ones are written.

    **F1 (Summary sheet review, 2026-09-23):** the same `dropped_references`
    also reaches the Summary sheet's own By currency/By payee/By cost
    category/By due week tables (`_summary_sheet`), each gaining an
    `instructed_amount` column beside the existing `amount` -- before this,
    those tables were built from the full reviewed set with no idea an
    exclusion had happened at all, so a payee with every invoice withheld
    still read as an ordinary total, on the sheet a reader opens first.
    Left blank for a group nothing was excluded from (never a repeated,
    identical pair), and asserted, not merely hoped, to reconcile with the
    Payments sheet's own "Run total"/"Run total (instructed)"
    (`_assert_reconciles_with_payments_sheet`) -- the same "never a second
    source that could drift" rule the rest of this module already holds
    to, applied to the one pair of sheets that both total the same run.

    The **Amendments** sheet (Task 5, handed over by Task 3 -- see the
    module docstring) needs nothing new from a caller: it reads
    `run_dir`'s own `amendments.json` directly (`_amendments_sheet`), the
    same file `dropped_references`/`amend.instructed_batches` are
    already built from. Empty, not absent and not broken, for a run with
    no amendments recorded yet.

    **E1:** every converted row on the Summary sheet also carries a
    `rate_basis` sentence beside the rate and its date (`_rate_basis_note`)
    -- the ECB's euro reference rate, mid-market, indicative, so the
    customer's own bank will quote something different -- and, when
    `rate_table["stale"]` says the rate actually came from `ecb_rates`'s
    cache fallback, the same sentence names the date it is from and says
    plainly that it is not current. Nothing here changes which currencies
    convert or which degrade to `conversion_note`; it only says, in the
    row a reader is already looking at, what kind of number a converted
    figure is.
    """
    batches = list(batches)
    control = signoff.control_block(run_dir)
    _assert_control_matches_batches(control, batches)

    home_currency = home_currency or (batches[0].sell_currency if batches else None)
    flag_dispositions = _flag_dispositions(run_dir)
    warnings = []

    sheets = [
        _payments_sheet(batches, dropped_references),
        _exceptions_sheet(list(flags or []), performed or {}, list(unreadable_documents or []),
                          flag_dispositions),
        _summary_sheet(batches, home_currency, rates, cost_categories, run_dir, warnings,
                      dropped_references),
        _signoff_sheet(control),
        _amendments_sheet(run_dir),
    ]
    result = build_workbook({"sheets": sheets}, out_path, brand=brand)
    result["_warnings"] = warnings + list(result.get("_warnings", []))
    return result
