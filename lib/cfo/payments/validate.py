"""T6: invoice and batch validation -- the arithmetic and comparison layer
of the payments tool. Nothing here calls a model, and nothing here makes a
network call: every rule in this module can be decided by a calculator, so
it is decided by one and nothing more expensive.

An invoice is whatever the extraction step (T5, `cfo.payments.extract`)
hands back, plus whatever a supplier master or a person adds by hand -- a
plain mapping, read throughout with `.get()`, so a field this module does
not know about is simply ignored rather than raising `AttributeError`. The
keys it reads:

  number        the invoice number
  filename      the source file, used to name the invoice when `number`
                could not be read -- see `_where`
  supplier      the supplier's name; the key both the per-supplier
                duplicate check and the near-duplicate warning key off
  invoice_date, due_date      `datetime.date`
  net, vat, gross              `Decimal`, or a numeric string a `Decimal`
                parses losslessly -- T5 hands amounts back as strings on
                purpose, for exactly this reason (see its module
                docstring). A `float` is rejected, not coerced: see
                `_read_amount`, the same choice `cfo.payments.model` makes
                for payment amounts and for the same reason -- a `float`
                carries binary rounding error a bank, or a supplier,
                should never see.
  currency      ISO 4217, three letters
  vat_number    the supplier's VAT registration number, already prefixed
                with its own two-letter country code -- every EU VAT
                number carries one (e.g. "IE6388047V"), so there is no
                separate country field to keep in sync with it
  terms         free text, e.g. "2/10 net 30" -- read only for the early
                settlement discount; see `_early_settlement_discount`

**VIES is deliberately not here, and never will be.** Format checking
(`_check_vat_number`) is offline arithmetic against a regex and stays. A
VIES lookup would send a specific supplier's VAT number to an external
service, which discloses who the customer buys from -- exactly what this
toolkit promises never happens (see docs/specs/2026-09-21-payments-fraud-
design.md §5 and §16). It belongs in the hosted version, where the
customer has already accepted that Hedj processes their data, and it does
not belong here even as a stub someone might wire up later.

`check_invoice` and `check_batch` return every finding as a plain
`(where, message)` tuple -- the interface this task was given fixes that
shape, throughout, so severity cannot live in a third tuple field without
breaking equality against a literal 2-tuple. It lives at the front of
`message` instead: a warning's message starts with `WARNING_PREFIX`,
everything else is an error. `is_warning` reads it back, and
`split_findings` gives the (errors, warnings) pair `cfo.console.
print_problems` and `ToolkitError` both want. Nothing in this module
raises `ToolkitError` itself -- the same choice `cfo.payments.model` makes
in `validate_batch`: an empty list means clean, and a caller that wants to
fail hard on a non-empty one wraps it in one.

A check that finds nothing wrong and a check that never ran are not the
same thing, and this module never reports the second as the first.
`check_against_history` is the clearest case: a first run has no history
to check against, and says so by name (`NOT_PERFORMED_MESSAGE`) rather
than returning `[]`, which would look identical to "checked, and every
invoice was clean." The whole value of this tool is being the thing that
makes someone look; a check that silently passes because it had nothing
to compare is worse than no check at all.
"""
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

# Configuration, never a constant buried at a call site -- see
# cfo.payments.model's MAX_PAYMENTS_PER_BATCH for the same reasoning.
AMOUNT_TOLERANCE = Decimal("0.01")
MAX_DUE_DAYS = 400
NEAR_DUPLICATE_WINDOW_DAYS = 7

# A finding's severity lives at the front of its message (see the module
# docstring). Chosen over a third tuple field so `(where, message)` stays
# a plain 2-tuple, comparable by `==` to a literal in a test, everywhere.
WARNING_PREFIX = "warning: "

NOT_PERFORMED_MESSAGE = ("history check not performed: no previous run history was available "
                         "-- this is a first run, or history could not be read; it is not the "
                         "same as a clean result, and must not be reported as one")

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

# Format only, per country, for the ten the brief names. Checksum
# validation (e.g. mod-97 for an IBAN) is a different, stronger kind of
# check and belongs to cfo.treasury.iban, not here -- a VAT number's check
# digits are computed differently per country and format-only was the
# scope agreed for this task. A country outside this table is silently
# skipped, not guessed at: see _check_vat_number.
_VAT_FORMATS = {
    # 7 digits + 1 letter, or the pre-2013 format (digit, letter/+/*, 5
    # digits, letter), or the newer 7 digits + 2 letters.
    "IE": re.compile(r"^(\d{7}[A-Z]{1,2}|\d[A-Z+*]\d{5}[A-Z])$"),
    # 9 digits standard; 12 digits for a branch; GD/HA + 3 digits for
    # government departments and health authorities.
    "GB": re.compile(r"^(\d{9}|\d{12}|GD\d{3}|HA\d{3})$"),
    "DE": re.compile(r"^\d{9}$"),
    # 2-character "key" (digits or letters) + 9-digit SIREN.
    "FR": re.compile(r"^[A-Z0-9]{2}\d{9}$"),
    # 9 digits + "B" + 2-digit sub-number.
    "NL": re.compile(r"^\d{9}B\d{2}$"),
    # 10 digits, the modern form starts 0 or 1.
    "BE": re.compile(r"^[01]\d{9}$"),
    "ES": re.compile(r"^[A-Z0-9]\d{7}[A-Z0-9]$"),
    "IT": re.compile(r"^\d{11}$"),
    "PL": re.compile(r"^\d{10}$"),
    # 12 digits, conventionally ending "01".
    "SE": re.compile(r"^\d{12}$"),
}

# "2/10 net 30", "2/10, Net 30", "2% / 10 net 30": a discount percentage,
# a slash, the day count it applies within, then "net" and the full due
# day count (which this module does not use -- due_date already carries
# it). Anything else -- "net 30" alone, a sentence, a blank field -- is
# read as "no discount stated" rather than guessed at.
_DISCOUNT_TERMS_RE = re.compile(
    r"(?i)^\s*(\d+(?:\.\d+)?)\s*%?\s*/\s*(\d+)\s*,?\s*net\s*\d+\s*$")


def _where(invoice):
    """The invoice's number, or its filename when the number could not be
    read -- so a user can find the row without counting them."""
    number = str(invoice.get("number") or "").strip()
    if number:
        return number
    filename = str(invoice.get("filename") or "").strip()
    return filename or "unknown invoice"


def _identity(invoice):
    """(supplier, number), or None when either is blank. The duplicate and
    history checks both key off this pair, and neither invents an
    identity to compare when one half of it is missing -- that would risk
    matching two invoices that only look alike because both are blank."""
    supplier = str(invoice.get("supplier") or "").strip()
    number = str(invoice.get("number") or "").strip()
    if not supplier or not number:
        return None
    return supplier, number


def _read_amount(invoice, field, where):
    """(Decimal, None) on success, (None, finding) on failure. A `float`
    is a failure, not something to coerce -- see the module docstring."""
    value = invoice.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, (where, f"{field} is required for the arithmetic check")
    if isinstance(value, float):
        return None, (where, f"{field} is a float ({value!r}); money must be a Decimal or a "
                             "numeric string, never float -- a float carries binary rounding "
                             "error a bank should never see")
    if isinstance(value, Decimal):
        return value, None
    try:
        return Decimal(str(value).strip()), None
    except InvalidOperation:
        return None, (where, f"{field} {value!r} is not a number")


def _check_arithmetic(invoice, where):
    """net + VAT == gross, within AMOUNT_TOLERANCE. The message carries
    all three figures and the difference -- "arithmetic does not check"
    tells whoever has to fix it nothing; the numbers do."""
    net, net_err = _read_amount(invoice, "net", where)
    vat, vat_err = _read_amount(invoice, "vat", where)
    gross, gross_err = _read_amount(invoice, "gross", where)
    errors = [err for err in (net_err, vat_err, gross_err) if err]
    if errors:
        return errors
    total = net + vat
    diff = abs(total - gross)
    if diff > AMOUNT_TOLERANCE:
        return [(where, f"net {net} + VAT {vat} = {total}, not gross {gross} "
                        f"(difference {diff})")]
    return []


def _check_vat_number(invoice, where):
    """Format only, for the country stated by the VAT number's own
    two-letter prefix. Not VIES -- see the module docstring."""
    raw = str(invoice.get("vat_number") or "").strip()
    if not raw:
        return []  # no VAT number stated -- nothing to check
    cleaned = raw.upper().replace(" ", "").replace("-", "")
    country, number = cleaned[:2], cleaned[2:]
    pattern = _VAT_FORMATS.get(country)
    if pattern is None:
        # Only the ten countries the brief names are covered; a country
        # outside that set is skipped, not guessed at, until a later task
        # extends the table.
        return []
    if not pattern.match(number):
        return [(where, f"VAT number {raw!r} does not match the {country} format")]
    return []


def _check_due_date(invoice, where):
    """Due date present, not before the invoice date, and not more than
    MAX_DUE_DAYS after it."""
    due_date = invoice.get("due_date")
    if due_date is None:
        return [(where, "due date is required")]
    if not isinstance(due_date, date):
        return [(where, f"due date {due_date!r} is not a date")]
    invoice_date = invoice.get("invoice_date")
    if invoice_date is None:
        # A due date can't be checked against an invoice date that was
        # never given -- silently skipping the comparison would look
        # identical to "checked and fine", which this module does not do.
        return [(where, "invoice date is required to check the due date against it")]
    if not isinstance(invoice_date, date):
        return [(where, f"invoice date {invoice_date!r} is not a date")]
    span = (due_date - invoice_date).days
    if span < 0:
        return [(where, f"due date {due_date.isoformat()} is before the invoice date "
                        f"{invoice_date.isoformat()}")]
    if span > MAX_DUE_DAYS:
        return [(where, f"due date {due_date.isoformat()} is {span} days after the invoice "
                        f"date {invoice_date.isoformat()}, more than the {MAX_DUE_DAYS}-day "
                        "limit")]
    return []


def _check_currency(invoice, where):
    """Currency is a three-letter ISO 4217 code."""
    currency = invoice.get("currency")
    if not currency:
        return [(where, "currency is required")]
    if not _CURRENCY_RE.match(str(currency)):
        return [(where, f"currency {currency!r} is not an ISO 4217 three-letter code")]
    return []


def _early_settlement_discount(invoice):
    """(percent, days), parsed from `terms`, or None when there isn't
    one -- "Net 30" alone states no discount, and that is not a defect,
    just nothing to surface."""
    match = _DISCOUNT_TERMS_RE.match(str(invoice.get("terms") or ""))
    if not match:
        return None
    return Decimal(match.group(1)), int(match.group(2))


def _check_early_settlement_discount(invoice, where):
    """An early-settlement discount, when terms state one, surfaced with
    the date by which it applies -- this is money the customer is
    entitled to and routinely loses. Advisory, not a problem, so it takes
    the same WARNING_PREFIX as the near-duplicate check: worth a person's
    attention, never a reason to block the invoice."""
    parsed = _early_settlement_discount(invoice)
    if parsed is None:
        return []
    percent, days = parsed
    invoice_date = invoice.get("invoice_date")
    if not isinstance(invoice_date, date):
        return []  # nothing to add the discount window to
    by_date = invoice_date + timedelta(days=days)
    return [(where, WARNING_PREFIX + f"an early-settlement discount of {percent}% applies if "
                    f"paid by {by_date.isoformat()} (terms: {invoice.get('terms')!r})")]


def check_invoice(invoice):
    """[(where, message)] -- every per-invoice rule this module enforces.
    An empty list means the invoice is clean."""
    where = _where(invoice)
    findings = []
    findings += _check_arithmetic(invoice, where)
    findings += _check_vat_number(invoice, where)
    findings += _check_due_date(invoice, where)
    findings += _check_currency(invoice, where)
    findings += _check_early_settlement_discount(invoice, where)
    return findings


def _check_duplicate_numbers(invoices):
    """No duplicate invoice number per supplier (an error -- unlike the
    near-duplicate check below, this is not "often legitimate"), **and** no
    invoice number reused by two DIFFERENT suppliers in this batch either
    (N1, second review -- .superpowers/sdd/2026-09-21-payments-fraud/
    second-review-findings.md): invoice numbers are not unique across
    suppliers, and this was "clean" here purely because the check above is
    scoped to `_identity` (supplier, number) -- two suppliers sharing a
    number never shared an identity, so the pair was invisible to it. The
    exception model `cfo.payments.cli` builds on top of this batch keys a
    great deal on an invoice's own number; reported once, at the batch
    level (`where="batch"`, the same convention `_check_control_total`
    already uses below for a finding that is not about any one invoice
    row -- never a specific invoice's own `_where()`, because the whole
    point of this finding is that string cannot tell the two apart)."""
    seen = {}
    by_number = {}
    findings = []
    for invoice in invoices:
        number = str(invoice.get("number") or "").strip()
        supplier = str(invoice.get("supplier") or "").strip()
        if number:
            by_number.setdefault(number, []).append((supplier, _where(invoice)))

        identity = _identity(invoice)
        if identity is None:
            continue
        where = _where(invoice)
        if identity in seen:
            supplier, number = identity
            findings.append((where, f"duplicate invoice number {number!r} for supplier "
                                    f"{supplier!r}, already seen as {seen[identity]!r}"))
        else:
            seen[identity] = where

    for number, entries in by_number.items():
        suppliers_seen = sorted({supplier for supplier, _where in entries if supplier})
        if len(suppliers_seen) > 1:
            wheres = ", ".join(sorted({where for _supplier, where in entries}))
            findings.append(("batch", f"invoice number {number!r} is used by more than one "
                                      f"supplier in this batch ({', '.join(suppliers_seen)}: "
                                      f"{wheres}) -- invoice numbers are not unique across "
                                      "suppliers, and the payment run this batch becomes is "
                                      "built per invoice, never assume this number identifies "
                                      "one payment"))
    return findings


def _check_near_duplicates(invoices):
    """Same supplier, same amount, invoice dates within
    NEAR_DUPLICATE_WINDOW_DAYS. A warning, not an error: often legitimate
    (a weekly retainer), and blocking it would train people to click past
    the tool -- see the module docstring's linked brief.

    A pair that already shares an invoice number is skipped here: it was
    reported once, as the stronger finding, by _check_duplicate_numbers,
    and reporting the same pair twice teaches nothing new."""
    findings = []
    for i in range(len(invoices)):
        for j in range(i + 1, len(invoices)):
            first, second = invoices[i], invoices[j]
            supplier_first = str(first.get("supplier") or "").strip()
            supplier_second = str(second.get("supplier") or "").strip()
            if not supplier_first or supplier_first != supplier_second:
                continue
            number_first = str(first.get("number") or "").strip()
            number_second = str(second.get("number") or "").strip()
            if number_first and number_first == number_second:
                continue
            where_first, where_second = _where(first), _where(second)
            gross_first, err_first = _read_amount(first, "gross", where_first)
            gross_second, err_second = _read_amount(second, "gross", where_second)
            if err_first or err_second or gross_first != gross_second:
                continue
            date_first, date_second = first.get("invoice_date"), second.get("invoice_date")
            if not (isinstance(date_first, date) and isinstance(date_second, date)):
                continue
            span = abs((date_first - date_second).days)
            if span > NEAR_DUPLICATE_WINDOW_DAYS:
                continue
            findings.append((where_second, WARNING_PREFIX +
                             f"possible near-duplicate of {where_first!r}: supplier "
                             f"{supplier_first!r}, both {gross_first}, invoice dates "
                             f"{date_first.isoformat()} and {date_second.isoformat()} "
                             f"({span} day(s) apart)"))
    return findings


def _check_control_total(invoices, control_total):
    """The control total equals the sum of the lines, in Decimal.

    Skipped entirely when `control_total` is None (the default): unlike
    `check_against_history`, most callers of `check_batch` will never have
    an independently stated total (from a bank file or a supplier
    statement, say) to reconcile against, and turning that into a
    mandatory "not performed" finding on every ordinary run would be noise
    for a check nobody asked for -- not the same situation as a first run
    genuinely missing the history file this tool itself writes."""
    if control_total is None:
        return []
    stated, err = _read_amount({"control_total": control_total}, "control_total", "batch")
    if err:
        return [err]
    total = Decimal("0")
    for invoice in invoices:
        gross, gross_err = _read_amount(invoice, "gross", _where(invoice))
        if gross_err is None:
            total += gross
        # An invoice whose own gross could not be read is already reported
        # by check_invoice; it is left out of the sum rather than raising
        # a second complaint about the same figure here.
    diff = abs(stated - total)
    if diff > AMOUNT_TOLERANCE:
        return [("batch", f"control total {stated} does not equal the sum of the lines "
                          f"{total} (difference {diff})")]
    return []


def check_batch(invoices, *, control_total=None):
    """[(where, message)] -- every batch-wide rule this module enforces
    across `invoices`. An empty list means the batch is clean.

    `control_total`, when given, is the batch's independently stated
    total -- `Decimal`, or a numeric string, never `float` -- checked
    against the sum of the invoices' own `gross` amounts. See
    `_check_control_total` for why leaving it as `None` skips the check
    rather than reporting it as "not performed"."""
    findings = []
    findings += _check_duplicate_numbers(invoices)
    findings += _check_near_duplicates(invoices)
    findings += _check_control_total(invoices, control_total)
    return findings


def check_against_history(invoices, history):
    """[(where, message)] -- invoice numbers already paid for the same
    supplier in a previous run, per `history`.

    `history` is the run folder's own record: an iterable of {"supplier",
    "number"} pairs (or full invoice-shaped dicts -- only those two keys
    are read) drawn from previous runs' paid invoices. Pass `None`, not
    `[]`, when there is no previous run to read: `None` reports the check
    as **not performed**, by name, in NOT_PERFORMED_MESSAGE; `[]` reports
    a check that ran and found a genuinely empty history. The two must
    never look the same -- see the module docstring."""
    if history is None:
        return [("history", NOT_PERFORMED_MESSAGE)]
    paid = set()
    for record in history:
        identity = _identity(record)
        if identity is not None:
            paid.add(identity)
    findings = []
    for invoice in invoices:
        identity = _identity(invoice)
        if identity is None:
            continue
        if identity in paid:
            supplier, number = identity
            findings.append((_where(invoice), f"invoice {number!r} for supplier {supplier!r} "
                                              "was already paid in a previous run"))
    return findings


def is_warning(message):
    """True when a finding's message marks it as advisory rather than
    blocking -- see the module docstring for why severity lives here and
    not in a third tuple field."""
    return message.startswith(WARNING_PREFIX)


def split_findings(findings):
    """(errors, warnings) -- the shape `cfo.console.print_problems` wants,
    and the shape a caller wraps as `ToolkitError(problems=errors,
    warnings=warnings)` when it wants to fail hard on the errors alone
    while still surfacing the warnings."""
    errors = [item for item in findings if not is_warning(item[1])]
    warnings = [item for item in findings if is_warning(item[1])]
    return errors, warnings
