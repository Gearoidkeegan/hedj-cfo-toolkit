"""T8: the twelve fraud checks -- all scripts, none of them a model call.
(Eleven in the brief's own table; `round_sum_or_odd_timing` was split into
`round_sum` and `odd_timing` on review -- see the note below on why one id
could not honestly answer "performed" for both halves.)

This is the task where a wrong answer is invisible: a missed flag looks
exactly like a clean batch, and nobody finds out until the money has gone.
Nothing here decides that a flagged payment is safe -- it reports, a person
decides, and T10's model only groups and writes up what these scripts
already found (see the module docstrings of `cfo.payments.suppliers` and
`cfo.payments.approvals`, both of which this module calls rather than
reimplements: `suppliers.bank_change` for the bank-detail comparison,
`suppliers.match` for near-exact name resolution, `approvals.available` and
`approvals.limits` for the two limit-based checks).

**A check with no data reports itself as not performed, by name, and says
why in words a finance manager would use.** `checks_performed` returns
`{check_id: {"performed": bool, "reason": str}}` -- `reason` is empty only
when `performed` is `True`. This is deliberately *not* gated on a resource's
mere presence where that would still be a false assurance: a master that
exists but carries no `domain` on any record leaves
`lookalike_sender_domain` exactly as blind as no master at all, so that
check's `performed` reads the batch's own data (`master` domains *and*
invoice `sender_email` values), not just `master is not None`.
`lookalike_supplier_name` and `changed_bank_details` both need an actual
record to compare against -- a name for one, a bank detail for the other --
so both are gated on `bool(master)`: a master that is `{}` -- the shape
`cfo.payments.suppliers.load_master` returns for a missing file, not an
error -- has nothing in it for either check to compare against, and
reporting either one "performed" there would be exactly the false assurance
this whole function exists to avoid. (A review finding, H3, caught an
earlier version of this module gating `lookalike_supplier_name` on `master
is not None` alone, on the theory that "no match" is itself a real answer
even from a master with zero records -- but with nothing to compare a name
against, that "no match" carries no more information than never checking
at all, so it is now held to the same `bool(master)` rule as
`changed_bank_details`, its sibling one line above it that always got this
right.)
`just_under_a_limit` and `split_to_stay_under` are gated on
`approvals.available(matrix)`, which already answers from real content, not
mere presence (`{}` is unavailable). `check_batch` reads these same answers
before even attempting a gated check: `performed=False` means the check
function is never called, not called and quietly finding nothing. `history`
is accepted for interface symmetry with `check_batch`'s siblings
(`cfo.payments.validate.check_against_history` takes the same three-way
shape) but nothing here reads it -- sequential numbering, near-duplicates and
new-payee status are all judged from this batch's own contents, which is
enough evidence for a scripted heuristic. Cross-run pattern reasoning belongs
to T10's model, not to a script that cannot explain why it weighted one run
against another.

**Every flag carries both compared values and a source**, never a bare
suspicion -- see `Flag`. A person who cannot see both sides of a comparison
cannot act on it and will learn to ignore it.

**One invoice field this module reads has no producer yet.** `sent_at` (a
`datetime`) is not written by `cfo.payments.extract` (T5) today. Its
absence from a *particular* invoice is read like any other optional field
that invoice happens to lack -- a skip, not an error. Its absence from
*every* invoice in the batch means `odd_timing` genuinely has nothing to
work with at all, and `checks_performed` says so by name rather than
claiming a look that never happened -- see `_check_odd_timing` and
`_invoices_have_timing_signal`.

`odd_timing` used to carry a second half that read `invoice_date` alone and
flagged any invoice *dated* on a weekend. A review finding, H1, had it
removed: a supplier's own invoicing run for the 1st of the month lands on a
weekend roughly 2 times in 7, so that half was a structural false positive
on routine, correctly-dated invoices at a rate of roughly 29% -- for ever,
on every real batch -- burying the genuine `sent_at` signal, and every
other check's flags, in noise a reviewer learns to ignore (three of the
sample pack's own twelve clean invoices were flagged by it). `sent_at`'s
out-of-hours reading survives because it is evidence about when a human or
a compromised mailbox actually acted, not an artefact of which day of the
week an invoice's own date happened to fall on -- see
`.superpowers/sdd/2026-09-21-payments-fraud/final-review-findings.md`.

**`lookalike_sender_domain` makes three independent comparisons, not one**
(T16 -- see `.superpowers/sdd/2026-09-21-payments-fraud/task-16-brief.md`),
because learning a supplier's domain purely from history gets the logic
backwards exactly when it matters: if the first mail ever received from a
supplier is the fraud, that domain becomes "the record", and every genuine
invoice afterwards flags against it while the fraud passes clean. Each
comparison reads `invoice["sender_email"]` (the `.eml` "From" address) and
needs no history at all except the first:

1. **Against the domain on file** -- `master[supplier_id]["domain"]`,
   written only by `cfo.payments.suppliers.remember`, and only for a
   supplier it already recognised (see that module's own docstring for why
   never for a brand-new record). Needs history to exist at all.
2. **Against the domain printed on the invoice itself** -- the optional
   `contact_email` scalar field (`cfo.payments.extract.SCALAR_FIELDS`), an
   ordinary model-read field like `supplier_name` or `net`. This is the
   load-bearing comparison: a real supplier's own letterhead disagreeing
   with the mail that just arrived is suspicious on the very first sighting,
   with nothing on file at all -- the one hole comparison 1 cannot close.
3. **Against another invoice in this same batch claiming the same
   supplier** -- two invoices for "Ilkeston Freight Ltd" arriving from two
   different domains is worth a look regardless of what is on file or
   printed on either one.

All three share one near-miss test, `_is_domain_lookalike` (built on
`_is_lookalike`, the same edit-distance-and-homoglyph logic
`lookalike_supplier_name` already uses -- never a second implementation
that could drift from it), with one guard neither of those two needs: two
domains that share the same label before their first "." (`.com` and
`.co.uk` of the same name, say) are treated as ordinary multi-market
registrations, never a lookalike, however close their full strings compare
under raw edit distance -- see `_is_domain_lookalike`'s own docstring.

`checks_performed` reports this check performed once **any** of the three
is actually possible: an invoice with a sender plus *either* a domain on
file, a printed domain somewhere in the batch, or a second invoice sharing
a claimed supplier name and also carrying a sender. Only when none of the
three is available does it report not performed -- naming, in each case,
which specific input was missing.

**No check here may raise on odd invoice data.** A missing date, an
unparseable amount, a blank supplier: each is a skip, never an exception --
`check_batch` itself drops any batch entry that is not even a `dict` before
looking at it. This is a script that finds; it does not decide, and it does
not stop the batch to complain about one bad row when eleven others might
carry a real flag.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from cfo.payments import approvals, suppliers
from cfo.safety.injection import scan_instruction_like
from cfo.treasury.iban import iban_country

# The brief's own table lists eleven; `round_sum_or_odd_timing` is split into
# `round_sum` and `odd_timing` here (see the module docstring), giving twelve.
# `check_batch` concatenates each check's flags in this same order, so a
# caller sees a stable, predictable arrangement run to run rather than one
# that shuffles with dict-iteration or hashing quirks.
CHECK_IDS = (
    "changed_bank_details", "new_payee_large_amount", "lookalike_supplier_name",
    "lookalike_sender_domain", "iban_country_mismatch", "sequential_invoice_numbers",
    "just_under_a_limit", "split_to_stay_under", "round_sum", "odd_timing",
    "near_duplicate_invoice", "hidden_instructions",
)
SEVERITIES = ("critical", "high", "medium", "low")

# --- Thresholds, named and argued here so a reviewer can contest a number
# without reading the function that uses it. ---

# new_payee_large_amount: the large-amount percentile. A first invoice from
# a genuinely new supplier is routine (a one-off purchase); flagging every
# one would be noise. The top decile of this batch's own amounts, in the
# same currency, is where a legitimately large first order and a fraudulent
# one both cluster -- worth a look, not proof of either.
NEW_PAYEE_PERCENTILE = 90
# Fewer than this many same-currency amounts in the batch and a percentile
# is two or three numbers pretending to be a distribution -- skip rather
# than manufacture a threshold from noise.
NEW_PAYEE_MIN_SAMPLE = 5

# lookalike_supplier_name / lookalike_sender_domain: share one similarity
# test (edit distance, and homoglyphs, per the brief's own wording).
# Levenshtein distance of 2 catches a single transposed or substituted
# character while still requiring real similarity -- "Acme Ltd" and "Acme
# Holdings Ltd" (a genuinely different, unrelated company that merely reads
# alike -- see cfo.payments.suppliers's own docstring) are nowhere near this
# close, and nor should they be: this check must not conflate "shares a
# word" with "engineered to be confused for".
LOOKALIKE_MAX_EDIT_DISTANCE = 2
# Below this many characters, edit distance stops meaning anything --
# "BP" and "PB" would always be "close" by the same rule that catches a real
# lookalike.
LOOKALIKE_MIN_NAME_LENGTH = 4

# sequential_invoice_numbers: the sequential-numbering gap. Two consecutive
# numbers from one supplier in one batch can be coincidence; three is a
# pattern worth naming. A gap of up to 2 between neighbours still reads as
# "the same run" of a fabricated series -- a real supplier's own numbering
# also serves other customers and rarely lands three invoices to us this
# close together by chance.
SEQUENTIAL_MIN_RUN = 3
SEQUENTIAL_MAX_GAP = 2

# just_under_a_limit: the margin below a limit. 10% is close enough to a
# configured threshold that landing there by chance is unlikely; a payment
# at half of a limit is unremarkable and must not be swept in by too wide a
# margin.
JUST_UNDER_LIMIT_MARGIN = Decimal("0.10")

# split_to_stay_under: several invoices means at least two -- the limit
# itself is the threshold that matters here, not an extra margin on top of
# it (unlike just_under_a_limit, which has no natural "total" to compare
# against instead).
SPLIT_MIN_INVOICE_COUNT = 2

# round_sum: the round-sum definition. A gross amount landing exactly on a
# multiple of 1,000 is unusual for a real costed invoice -- freight,
# materials and tax rarely sum this cleanly on their own.
#
# odd_timing: the out-of-hours window. 07:00-19:00 is an ordinary business
# day for sending a genuine commercial invoice; outside it is not proof of
# anything, just something to note.
ROUND_AMOUNT_UNIT = Decimal("1000")
OUT_OF_HOURS_START_HOUR = 7
OUT_OF_HOURS_END_HOUR = 19

# near_duplicate_invoice: the near-duplicate date window. A week is close
# enough that "two invoices, same supplier, same amount" is worth a look,
# but wide enough that it does not collide with an ordinary monthly
# recurring invoice landing on a similar date next cycle.
NEAR_DUPLICATE_WINDOW_DAYS = 7

_VAT_COUNTRY_RE = re.compile(r"^[A-Z]{2}")
_TRAILING_DIGITS_RE = re.compile(r"(\d+)\D*$")

# Multi-character sequences first (longest match first matters: "Acrne"
# only folds to "Acme" if "rn"->"m" runs before any single-character rule
# could touch it), then the classic digit/letter substitutions. Only the
# handful an invoice-fraud attempt is actually likely to use, not a general
# Unicode-confusables table -- see _is_lookalike for why going further would
# start catching names that just happen to share a word.
_HOMOGLYPH_SEQUENCES = (("rn", "m"), ("vv", "w"), ("cl", "d"), ("ii", "u"))
_HOMOGLYPH_CHARS = str.maketrans({"0": "o", "1": "l", "5": "s", "8": "b"})


@dataclass
class Flag:
    check: str          # the check's id, e.g. "changed_bank_details"
    severity: str       # "critical" | "high" | "medium" | "low"
    where: str           # the invoice: its number, or filename if unread
    summary: str        # one sentence a finance manager can act on
    compared: dict = field(default_factory=dict)   # {"old", "new"} or {"a", "b"} -- always both
    source: str = ""     # where in the document (or which reference data) this rests on
    # N1 (second review, .superpowers/sdd/2026-09-21-payments-fraud/
    # second-review-findings.md): the invoice that actually raised this flag,
    # by its unique content_doc_id -- `where` (the invoice's own number, or
    # its filename) is what a person reads, but it is NOT a unique key: two
    # invoices from different suppliers can, and in the wild do, share a
    # number. `cfo.payments.cli.cmd_review` used to map a rejected exception
    # back to the payment it should drop by `where` alone, which silently
    # collapsed two invoices sharing one into whichever was processed last --
    # rejecting a flag on one invoice dropped a different supplier's payment
    # instead and paid the flagged one. `content_doc_id` is the one thing
    # `cfo.payments.cli._invoice_units` already assigns that IS unique per
    # invoice, so it -- not `where` -- is what cmd_review now uses to find
    # the payment a flag's own invoice became. Blank only for a Flag built
    # without one (a test fixture, mostly); every Flag `check_batch` raises
    # here carries the invoice's own content_doc_id.
    content_doc_id: str = ""


# The exact text `cfo.payments.cli.cmd_review` writes for a flag's own
# exception -- "[check_id] summary" -- and the regex `cfo.payments.
# render_fraud` and `cfo.payments.workbook` both use to parse it back apart
# and match a reviewer's disposition to the finding it belongs to. Defined
# here, once, and imported by all three rather than copied: a second,
# independent copy of either the format or the regex is exactly how N5's
# "Also" note happened -- render_fraud's copy was pinned by a contract test,
# workbook's identical copy was not, and workbook's is the one a reviewer
# actually opens.
FLAG_EXCEPTION_MESSAGE_RE = re.compile(r"^\[(?P<check>[^\]]+)\]\s*")


def flag_exception_message(flag):
    """The "[check_id] summary" text for `flag`'s own exception -- the one
    place this format is built, so a caller never has to restate it (and
    risk restating it slightly differently) at the point it writes an
    exception for a flag."""
    return f"[{flag.check}] {flag.summary}"


def _performed(reason=""):
    return {"performed": not reason, "reason": reason}


def _master_has_domain(master):
    """True once at least one record in `master` carries a non-blank
    `domain` -- a master that exists but where every record is silent on
    domain is exactly as blind to a spoofed sender as no master at all, and
    must not be reported as though the check had something to compare."""
    return any(str(record.get("domain") or "").strip()
              for record in (master or {}).values() if isinstance(record, dict))


def _has_sender_email(invoice):
    return isinstance(invoice, dict) and "@" in str(invoice.get("sender_email") or "")


def _invoices_have_sender(invoices):
    return any(_has_sender_email(invoice) for invoice in (invoices or []))


def _invoices_have_printed_domain(invoices):
    """True once at least one invoice in the batch states a domain of its
    own to compare a sender against -- comparison 2's own precondition,
    needing no master and no history at all."""
    return any(_printed_domain(invoice) for invoice in (invoices or [])
              if isinstance(invoice, dict))


def _batch_has_comparable_pair(invoices):
    """True once at least two invoices in this batch claim the same
    supplier by name and each carries a sender email -- comparison 3's own
    precondition. Whether their domains actually differ is that
    comparison's own finding, not this gate's concern; this only asks
    whether the comparison could be *made* at all."""
    counts = {}
    for invoice in (invoices or []):
        if not isinstance(invoice, dict):
            continue
        name = _claimed_supplier(invoice)
        if name and _sender_domain(invoice):
            counts[name] = counts.get(name, 0) + 1
    return any(count >= 2 for count in counts.values())


def _invoices_have_timing_signal(invoices):
    """True once at least one invoice carries a checkable `sent_at` --
    `odd_timing`'s only remaining signal after a review finding, H1, had
    the weekend-`invoice_date` half removed (see the module docstring and
    `_check_odd_timing`)."""
    for invoice in (invoices or []):
        if not isinstance(invoice, dict):
            continue
        if isinstance(invoice.get("sent_at"), datetime):
            return True
    return False


def _invoices_have_country_signal(invoices, master):
    """True once at least one invoice in the batch that carries an IBAN
    also has an expected country to compare it against -- from the
    supplier master, the invoice's own stated country, or its VAT number's
    own prefix (see `_expected_country`, which this calls for exactly the
    same three sources). Without this, `iban_country_mismatch` reported
    itself performed unconditionally, blind on precisely the new-payee
    invoices it exists for: no master record yet, no `country` field
    (never extracted as its own field), and often no VAT number either --
    see H4 in `.superpowers/sdd/2026-09-21-payments-fraud/final-review-findings.md`."""
    for invoice in (invoices or []):
        if not isinstance(invoice, dict):
            continue
        if not str(invoice.get("iban") or "").strip():
            continue
        expected, _source = _expected_country(invoice, master)
        if expected:
            return True
    return False


def checks_performed(invoices=None, *, master=None, matrix=None, history=None):
    """`{check_id: {"performed": bool, "reason": str}}` for all twelve.
    `reason` is empty exactly when `performed` is `True`, and otherwise
    names what was missing in words a finance manager would use -- this is
    what a gap report prints verbatim, so it reads as an explanation, not a
    diagnostic.

    Gating is read from real content, never a resource's mere presence
    where presence alone would still be a false assurance -- see the module
    docstring for why `lookalike_sender_domain`, `odd_timing` and
    `iban_country_mismatch` look at `invoices` (and, for the domain check,
    `master`'s own record contents) rather than only asking whether
    `master`/`matrix` were supplied at all. `history` is accepted, unused --
    see the module docstring."""
    del history
    # changed_bank_details and lookalike_supplier_name both need an actual
    # record in the master to compare against -- a bank detail for one, a
    # name for the other. An empty master ({} -- see
    # cfo.payments.suppliers.load_master's own docstring for when this
    # happens) is present but has nothing in it for either check to compare
    # against, so it is exactly as blind as no master at all (H3 -- see the
    # module docstring).
    master_available = bool(master)
    matrix_available = approvals.available(matrix)

    # Performed once *any* of the three comparisons is possible -- see the
    # module docstring. A sender is the one thing every comparison needs;
    # past that, a domain on file, a domain printed on some invoice, or a
    # second invoice from the same supplier is enough for any one of them.
    if not _invoices_have_sender(invoices):
        domain_reason = ("no invoice in this batch stated a sender email address, so a spoofed "
                         "sender could not be compared against anything")
    elif not (_master_has_domain(master) or _invoices_have_printed_domain(invoices)
              or _batch_has_comparable_pair(invoices)):
        domain_reason = ("no supplier email domains were recorded, no invoice stated the domain "
                         "printed on it, and no second invoice from the same supplier was "
                         "available to compare against, so a spoofed sender could not be checked")
    else:
        domain_reason = ""

    timing_reason = ("" if _invoices_have_timing_signal(invoices) else
                     "no invoice in this batch stated a time it was sent, so unusual timing "
                     "could not be checked")

    country_reason = ("" if _invoices_have_country_signal(invoices, master) else
                      "no invoice in this batch carrying an IBAN had a determinable country -- "
                      "from the supplier master, the invoice's own stated country or its VAT "
                      "number's own prefix -- so an IBAN country mismatch could not be checked")

    return {
        "changed_bank_details": _performed(
            "" if master_available else
            "no supplier master with any records was supplied, so a changed bank detail could "
            "not be compared against anything on file"),
        "new_payee_large_amount": _performed(),
        "lookalike_supplier_name": _performed(
            "" if master_available else
            "no supplier master with any records was supplied, so a look-alike supplier name "
            "could not be compared against real ones"),
        "lookalike_sender_domain": _performed(domain_reason),
        "iban_country_mismatch": _performed(country_reason),
        "sequential_invoice_numbers": _performed(),
        "just_under_a_limit": _performed(
            "" if matrix_available else
            "no approval matrix was supplied, so there were no limits to compare payment "
            "amounts against"),
        "split_to_stay_under": _performed(
            "" if matrix_available else
            "no approval matrix was supplied, so there were no limits to check split payments "
            "against"),
        "round_sum": _performed(),
        "odd_timing": _performed(timing_reason),
        "near_duplicate_invoice": _performed(),
        "hidden_instructions": _performed(),
    }


def _where(invoice):
    """The invoice's number, or its filename when the number could not be
    read -- the same rule `cfo.payments.validate._where` applies, kept
    consistent rather than invented a second way to name a row."""
    if not isinstance(invoice, dict):
        return "unknown invoice"
    number = str(invoice.get("number") or "").strip()
    if number:
        return number
    filename = str(invoice.get("filename") or "").strip()
    return filename or "unknown invoice"


def _read_amount(value):
    """`Decimal`, or `None` on anything that is not one -- never a raise.
    A `float` is deliberately not coerced, the same choice
    `cfo.payments.validate._read_amount` makes and for the same reason: a
    float carries binary rounding error, and a fraud check that is wrong
    about an amount by a rounding error is worse than one that skips it."""
    if value is None or isinstance(value, (bool, float)):
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _percentile(sorted_values, pct):
    """The `pct`-th percentile of already-sorted `Decimal` values, by
    nearest rank -- simple, deterministic, and never invents a value
    between two invoices that were never actually invoiced (the way linear
    interpolation would). Integer arithmetic throughout, so this never
    touches a float on the way to a money comparison."""
    n = len(sorted_values)
    if n == 0:
        return None
    rank = -(-(pct * n) // 100)  # ceiling division, no float involved
    index = min(max(rank - 1, 0), n - 1)
    return sorted_values[index]


def _normalise_for_comparison(text):
    """Casefolded, with anything that is not a letter or digit removed --
    contiguous tokens, unlike `cfo.payments.suppliers._normalise`, which
    keeps a space and strips a legal-suffix token from the end. That
    module's near-exact matching and this module's fuzzy lookalike test
    solve different problems and were never meant to share one
    normalisation."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").casefold())


def _fold_homoglyphs(text):
    for sequence, replacement in _HOMOGLYPH_SEQUENCES:
        text = text.replace(sequence, replacement)
    return text.translate(_HOMOGLYPH_CHARS)


def _levenshtein(a, b):
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1,
                             previous[j - 1] + (ca != cb))
        previous = current
    return previous[-1]


def _is_lookalike(a, b):
    """True when `a` and `b` are different but close enough to be a
    plausible visual lookalike -- exactly equal is a match, not a
    lookalike, and is never reported here (see the two callers, which both
    check for a real match first). Two names or domains that are simply
    unrelated, or too short to say anything about, are never close enough
    either -- see LOOKALIKE_MIN_NAME_LENGTH."""
    norm_a, norm_b = _normalise_for_comparison(a), _normalise_for_comparison(b)
    if not norm_a or not norm_b or norm_a == norm_b:
        return False
    if min(len(norm_a), len(norm_b)) < LOOKALIKE_MIN_NAME_LENGTH:
        return False
    if _fold_homoglyphs(norm_a) == _fold_homoglyphs(norm_b):
        return True
    return _levenshtein(norm_a, norm_b) <= LOOKALIKE_MAX_EDIT_DISTANCE


def _domain_of(value):
    """The lower-cased domain half of an email address in `value`, or `""`
    when `value` carries no "@" at all -- the one thing all three of
    `lookalike_sender_domain`'s comparisons actually compare, pulled out
    once so none of them derives it slightly differently."""
    text = str(value or "").strip()
    if "@" not in text:
        return ""
    return text.rsplit("@", 1)[-1].strip().lower()


def _sender_domain(invoice):
    return _domain_of(invoice.get("sender_email")) if isinstance(invoice, dict) else ""


def _printed_domain(invoice):
    """The domain half of `contact_email` -- an email address printed on
    the invoice itself, read by the model like any other
    `cfo.payments.extract.SCALAR_FIELDS` entry, never the address the
    invoice arrived from. See the module docstring's comparison 2."""
    return _domain_of(invoice.get("contact_email")) if isinstance(invoice, dict) else ""


def _claimed_supplier(invoice):
    return str(invoice.get("supplier") or "").strip().casefold() if isinstance(invoice, dict) \
        else ""


def _domain_label(domain):
    """The registrable name before a domain's first "." -- "ilkeston-freight"
    from both "ilkeston-freight.com" and "ilkeston-freight.co.uk". Comparing
    on this alone is what lets a supplier's own legitimate multi-market
    domains agree with each other in `_is_domain_lookalike` below."""
    return str(domain or "").strip().lower().partition(".")[0]


def _is_domain_lookalike(a, b):
    """True when domains `a` and `b` are different but close enough to be a
    plausible spoof of one another. Reuses `_is_lookalike` -- the same
    edit-distance-and-homoglyph test `lookalike_supplier_name` already
    applies, never a second implementation that could drift from it -- with
    one guard neither `lookalike_supplier_name` nor `_is_lookalike` itself
    needs: two domains that share the same label before their first "."
    (".com" and ".co.uk" of the same name, say) are ordinary multi-market
    registrations, not a lookalike, however close their full strings
    compare under raw edit distance alone. Without this guard, "ilkeston-
    freight.com" and "ilkeston-freight.co.uk" would register as a lookalike
    of each other purely because "com" and "co.uk", stripped of punctuation,
    happen to sit within edit distance 2 -- exactly the false positive the
    brief's own near-miss rule warns against."""
    a, b = str(a or "").strip().lower(), str(b or "").strip().lower()
    if not a or not b or a == b:
        return False
    if _domain_label(a) == _domain_label(b):
        return False
    # Two domains differing ONLY in punctuation are a spoof, not a variant.
    # `_normalise_for_comparison` strips punctuation before comparing, so
    # "ilkestonfreight.co.uk" and "ilkeston-freight.co.uk" both reduce to
    # "ilkestonfreightcouk"; `_is_lookalike` then sees two identical strings
    # and answers, correctly for its own purpose, that identical is a match
    # rather than a lookalike. The effect here was that dropping a hyphen --
    # among the cheapest and most convincing spoofs available, and trivially
    # registrable -- was invisible to this check.
    #
    # Found by a control probe against the real pack rather than by a test:
    # every test used a letter substitution, and none used a punctuation
    # change.
    #
    # This sits AFTER the same-label guard deliberately. ".com" against
    # ".co.uk" of one name normalises to two different strings, so the
    # legitimate multi-market case never reaches this line.
    if _normalise_for_comparison(a) == _normalise_for_comparison(b):
        return True
    return _is_lookalike(a, b)


def _resolve_supplier_id(invoice, master):
    """`invoice["supplier_id"]` when it carries one, otherwise
    `suppliers.match(invoice["supplier"], master)` -- the same resolution
    order `suppliers.bank_change` uses internally, exposed here through the
    public `match` rather than that module's own private helper, because
    this module calls `suppliers`, it does not reach into it."""
    supplier_id = str(invoice.get("supplier_id") or "").strip()
    if supplier_id:
        return supplier_id
    resolved, _reason = suppliers.match(invoice.get("supplier"), master)
    return resolved


def _check_changed_bank_details(invoices, master):
    """this invoice against the supplier master -- `suppliers.bank_change`
    does the comparison; this only turns its finding into a `Flag`.
    Critical always: see the module's task brief on why a changed bank
    detail is critical even when it is probably legitimate."""
    flags = []
    for invoice in invoices:
        finding = suppliers.bank_change(invoice, master)
        if finding is None:
            continue
        last_seen = finding.get("last_seen")
        last_seen_text = last_seen.isoformat() if isinstance(last_seen, date) else "unknown"
        flags.append(Flag(
            check="changed_bank_details", severity="critical", where=_where(invoice),
            summary=(f"the {finding['field']} on this invoice does not match the supplier "
                     f"master's record for {finding['supplier_id']} (last seen "
                     f"{last_seen_text})"),
            compared={"old": finding["old"], "new": finding["new"]},
            source=f"supplier master record for {finding['supplier_id']}",
            content_doc_id=str(invoice.get("content_doc_id") or "")))
    return flags


def _is_known_supplier(invoice, master):
    if not isinstance(invoice, dict):
        return False
    supplier_id = str(invoice.get("supplier_id") or "").strip()
    if supplier_id:
        return supplier_id in (master or {})
    resolved, _reason = suppliers.match(invoice.get("supplier"), master or {})
    return resolved is not None


def _check_new_payee_large_amount(invoices, master):
    """a first-time supplier against the batch's own value distribution --
    grouped by currency, since comparing a GBP invoice against a EUR
    percentile would compare two different things. Works with `master`
    absent: everyone is then unresolvable and so, by this check's own
    definition, a first-time supplier -- the honest reading of "we have no
    record of anyone", not a reason to skip."""
    by_currency = {}
    for invoice in invoices:
        amount = _read_amount(invoice.get("gross"))
        currency = str(invoice.get("currency") or "").strip().upper()
        if amount is not None and currency:
            by_currency.setdefault(currency, []).append(amount)

    thresholds = {}
    for currency, amounts in by_currency.items():
        if len(amounts) < NEW_PAYEE_MIN_SAMPLE:
            continue
        thresholds[currency] = _percentile(sorted(amounts), NEW_PAYEE_PERCENTILE)

    flags = []
    for invoice in invoices:
        currency = str(invoice.get("currency") or "").strip().upper()
        threshold = thresholds.get(currency)
        if threshold is None:
            continue
        amount = _read_amount(invoice.get("gross"))
        if amount is None or amount <= threshold:
            continue
        if _is_known_supplier(invoice, master):
            continue
        flags.append(Flag(
            check="new_payee_large_amount", severity="high", where=_where(invoice),
            summary=(f"the first invoice seen from {invoice.get('supplier') or 'this supplier'} "
                     f"is {amount} {currency}, above this batch's own "
                     f"{NEW_PAYEE_PERCENTILE}th percentile for {currency}"),
            compared={"a": f"{amount} {currency}",
                     "b": f"{threshold} {currency} ({NEW_PAYEE_PERCENTILE}th percentile of "
                          f"{len(by_currency[currency])} invoice(s) in this batch)"},
            source=f"this batch's own {currency} value distribution",
            content_doc_id=str(invoice.get("content_doc_id") or "")))
    return flags


def _check_lookalike_supplier_name(invoices, master):
    """edit distance and homoglyphs against master names -- only for a
    supplier name that `suppliers.match` could not resolve at all: a real
    match, exact or near-exact, is the ordinary case and is never a
    lookalike."""
    master = master or {}
    flags = []
    for invoice in invoices:
        name = str(invoice.get("supplier") or "").strip()
        if not name:
            continue
        resolved, _reason = suppliers.match(name, master)
        if resolved is not None:
            continue
        best = None
        for record in master.values():
            candidates = [record.get("name")] + list(record.get("aliases") or [])
            for candidate in candidates:
                candidate = str(candidate or "").strip()
                if candidate and _is_lookalike(name, candidate):
                    best = candidate
                    break
            if best:
                break
        if best:
            flags.append(Flag(
                check="lookalike_supplier_name", severity="high", where=_where(invoice),
                summary=(f"the supplier name {name!r} on this invoice closely resembles "
                         f"{best!r} in the supplier master, but does not match it"),
                compared={"a": name, "b": best},
                source="supplier master names and aliases",
                content_doc_id=str(invoice.get("content_doc_id") or "")))
    return flags


def _check_lookalike_sender_domain(invoices, master):
    """the sender's domain on an `.eml` invoice against up to three things
    that need no shared history to compare against -- see the module
    docstring's three-comparison design. Each comparison is independent (an
    invoice can fire more than one) and each names, in its own `summary`,
    which one found it -- "differs from the one printed on this invoice"
    and "differs from the one on file" are different facts to a reader, and
    they act on them differently."""
    master = master or {}
    invoices = [invoice for invoice in invoices if isinstance(invoice, dict)]
    flags = []

    # Comparison 3's own grouping: every invoice, by its own claimed
    # supplier name -- never a master lookup, since this comparison must
    # work with no master at all.
    by_claimed_supplier = {}
    for invoice in invoices:
        name = _claimed_supplier(invoice)
        if name:
            by_claimed_supplier.setdefault(name, []).append(invoice)

    for invoice in invoices:
        sender_domain = _sender_domain(invoice)
        if not sender_domain:
            continue

        # 1. Against the domain on file for the supplier this invoice
        # resolves to -- needs history (suppliers.remember must have
        # written it first).
        supplier_id = _resolve_supplier_id(invoice, master)
        known_domain = ""
        if supplier_id and supplier_id in master:
            known_domain = str(master[supplier_id].get("domain") or "").strip().lower()
        if known_domain and _is_domain_lookalike(sender_domain, known_domain):
            flags.append(Flag(
                check="lookalike_sender_domain", severity="high", where=_where(invoice),
                summary=(f"the sending domain {sender_domain!r} closely resembles but does not "
                         f"match {known_domain!r}, the domain on file for this supplier"),
                compared={"a": sender_domain, "b": known_domain},
                source=f"sender_email on {_where(invoice)}",
                content_doc_id=str(invoice.get("content_doc_id") or "")))

        # 2. Against the domain printed on the invoice itself -- needs no
        # history at all; the load-bearing comparison, see the module
        # docstring.
        printed_domain = _printed_domain(invoice)
        if printed_domain and _is_domain_lookalike(sender_domain, printed_domain):
            flags.append(Flag(
                check="lookalike_sender_domain", severity="high", where=_where(invoice),
                summary=(f"the sender's domain {sender_domain!r} differs from "
                         f"{printed_domain!r}, the domain printed on this invoice itself"),
                compared={"a": sender_domain, "b": printed_domain},
                source=f"contact_email printed on {_where(invoice)}",
                content_doc_id=str(invoice.get("content_doc_id") or "")))

    # 3. Against another invoice in this same batch claiming the same
    # supplier -- needs no history either, only this batch's own contents.
    # One flag per differing pair (not one per invoice, and not one per
    # side of the pair), the same convention `_check_near_duplicate_invoice`
    # uses for its own pairwise comparison.
    for group in by_claimed_supplier.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                first, second = group[i], group[j]
                domain_a, domain_b = _sender_domain(first), _sender_domain(second)
                if not domain_a or not domain_b:
                    continue
                if not _is_domain_lookalike(domain_a, domain_b):
                    continue
                flags.append(Flag(
                    check="lookalike_sender_domain", severity="high", where=_where(second),
                    summary=(f"{_where(second)} arrived from {domain_b!r}, which closely "
                             f"resembles but does not match {domain_a!r}, used by "
                             f"{_where(first)} -- another invoice in this batch claiming the "
                             "same supplier"),
                    compared={"a": f"{_where(first)}: {domain_a}",
                             "b": f"{_where(second)}: {domain_b}"},
                    source=f"other invoices in this batch claiming {second.get('supplier')!r}",
                    content_doc_id=str(second.get("content_doc_id") or "")))
    return flags


def _expected_country(invoice, master):
    """(`country`, `source description`) for whatever this invoice, or the
    master record it resolves to, states about the supplier's country --
    preferring the master when it resolves, then the invoice's own stated
    country, then the two-letter prefix every EU VAT number carries (see
    `cfo.payments.validate`'s own docstring on why that prefix is always
    the country and never a separate field). `(None, None)` when none of
    the three says anything -- not master-dependent as a whole: a country
    is very often available from the invoice alone, which is why
    `iban_country_mismatch` is not in `checks_performed`'s master-gated set
    of five. It is still gated, on real content rather than presence, by
    `_invoices_have_country_signal`: an invoice carrying an IBAN but
    resolvable through none of the three (a brand-new payee, typically --
    see H4) has genuinely nothing for this function to find, and
    `checks_performed` must say so rather than claim a look that found
    nothing to look at."""
    if master:
        supplier_id = _resolve_supplier_id(invoice, master)
        if supplier_id and supplier_id in master:
            country = str(master[supplier_id].get("country") or "").strip().upper()
            if country:
                return country, f"the supplier master record for {supplier_id}"
    country = str(invoice.get("country") or "").strip().upper()
    if country:
        return country, "the invoice's own stated country"
    vat_number = str(invoice.get("vat_number") or "").strip().upper()
    match = _VAT_COUNTRY_RE.match(vat_number)
    if match:
        return match.group(0), "the country prefix of the invoice's own VAT number"
    return None, None


def _check_iban_country_mismatch(invoices, master):
    """the IBAN's country against the supplier's country -- see
    _expected_country for where "the supplier's country" comes from."""
    flags = []
    for invoice in invoices:
        iban = str(invoice.get("iban") or "").strip()
        if not iban:
            continue
        iban_ctry = iban_country(iban)
        if not iban_ctry:
            continue
        expected, source_desc = _expected_country(invoice, master)
        if not expected or expected == iban_ctry:
            continue
        flags.append(Flag(
            check="iban_country_mismatch", severity="high", where=_where(invoice),
            summary=(f"the IBAN's country ({iban_ctry}) does not match the supplier's "
                     f"country ({expected}), from {source_desc}"),
            compared={"a": f"IBAN country {iban_ctry}", "b": f"supplier country {expected}"},
            source=source_desc, content_doc_id=str(invoice.get("content_doc_id") or "")))
    return flags


def _numeric_suffix(number):
    match = _TRAILING_DIGITS_RE.search(str(number or ""))
    return int(match.group(1)) if match else None


def _check_sequential_invoice_numbers(invoices):
    """numbering running tight across one supplier -- a run of
    SEQUENTIAL_MIN_RUN or more invoice numbers from the same supplier in
    this batch, each within SEQUENTIAL_MAX_GAP of the last."""
    by_supplier = {}
    for invoice in invoices:
        supplier = str(invoice.get("supplier") or "").strip()
        suffix = _numeric_suffix(invoice.get("number"))
        if supplier and suffix is not None:
            by_supplier.setdefault(supplier, []).append((suffix, invoice))

    flags = []
    for supplier in sorted(by_supplier):
        entries = sorted(by_supplier[supplier], key=lambda entry: entry[0])
        run = [entries[0]]
        for suffix, invoice in entries[1:]:
            if suffix - run[-1][0] <= SEQUENTIAL_MAX_GAP:
                run.append((suffix, invoice))
                continue
            if len(run) >= SEQUENTIAL_MIN_RUN:
                flags.append(_sequential_flag(supplier, run))
            run = [(suffix, invoice)]
        if len(run) >= SEQUENTIAL_MIN_RUN:
            flags.append(_sequential_flag(supplier, run))
    return flags


def _sequential_flag(supplier, run):
    numbers = [str(invoice.get("number")) for _suffix, invoice in run]
    return Flag(
        check="sequential_invoice_numbers", severity="medium", where=_where(run[-1][1]),
        summary=(f"{len(run)} invoices from {supplier!r} in this batch carry numbers that "
                 f"run tightly together: {', '.join(numbers)}"),
        compared={"a": numbers[0], "b": numbers[-1]},
        source=f"invoice numbers for {supplier!r} in this batch",
        content_doc_id=str(run[-1][1].get("content_doc_id") or ""))


def _check_just_under_a_limit(invoices, matrix):
    """amounts within JUST_UNDER_LIMIT_MARGIN below any approval
    threshold in the matrix, in the invoice's own currency."""
    all_limits = approvals.limits(matrix)
    flags = []
    for invoice in invoices:
        amount = _read_amount(invoice.get("gross"))
        currency = str(invoice.get("currency") or "").strip().upper()
        if amount is None or not currency:
            continue
        for role, limit_currency, limit in all_limits:
            if limit_currency != currency or limit is None or limit <= 0:
                continue
            margin = limit * JUST_UNDER_LIMIT_MARGIN
            if limit - margin <= amount < limit:
                flags.append(Flag(
                    check="just_under_a_limit", severity="medium", where=_where(invoice),
                    summary=(f"{amount} {currency} sits within {int(JUST_UNDER_LIMIT_MARGIN * 100)}% "
                             f"below the {role} approval limit of {limit} {currency}, without "
                             "reaching it"),
                    compared={"a": f"{amount} {currency}",
                             "b": f"{limit} {currency} ({role} limit)"},
                    source=f"approval matrix: {role} limit in {currency}",
                    content_doc_id=str(invoice.get("content_doc_id") or "")))
                break  # one flag per invoice; every nearby role's limit would be noise
    return flags


def _check_split_to_stay_under(invoices, matrix):
    """several invoices, one supplier, one day, summing over a limit --
    while none alone would have reached it."""
    groups = {}
    for invoice in invoices:
        supplier = str(invoice.get("supplier") or "").strip()
        invoice_date = invoice.get("invoice_date")
        currency = str(invoice.get("currency") or "").strip().upper()
        amount = _read_amount(invoice.get("gross"))
        if not supplier or not isinstance(invoice_date, date) or amount is None or not currency:
            continue
        groups.setdefault((supplier, invoice_date, currency), []).append((amount, invoice))

    limits_by_currency = {}
    for role, currency, limit in approvals.limits(matrix):
        limits_by_currency.setdefault(currency, []).append((role, limit))

    flags = []
    for (supplier, invoice_date, currency), entries in sorted(
            groups.items(), key=lambda kv: (kv[0][0], kv[0][1].isoformat(), kv[0][2])):
        if len(entries) < SPLIT_MIN_INVOICE_COUNT:
            continue
        total = sum((amount for amount, _invoice in entries), Decimal("0"))
        for role, limit in limits_by_currency.get(currency, []):
            if limit is None or limit <= 0:
                continue
            if total > limit and all(amount <= limit for amount, _invoice in entries):
                labels = ", ".join(_where(invoice) for _amount, invoice in entries)
                flags.append(Flag(
                    check="split_to_stay_under", severity="high",
                    where=_where(entries[-1][1]),
                    summary=(f"{len(entries)} invoices from {supplier!r} on "
                             f"{invoice_date.isoformat()} total {total} {currency}, over the "
                             f"{role} limit of {limit} {currency}, though none alone reaches it"),
                    compared={"a": f"{total} {currency} across {labels}",
                             "b": f"{limit} {currency} ({role} limit)"},
                    source=f"approval matrix: {role} limit in {currency}",
                    content_doc_id=str(entries[-1][1].get("content_doc_id") or "")))
                break  # one flag per group; every limit it clears would be noise
    return flags


def _check_round_sum(invoices):
    """round amounts -- always performed, because a gross amount is a core
    field every invoice carries (see `checks_performed`)."""
    flags = []
    for invoice in invoices:
        amount = _read_amount(invoice.get("gross"))
        if amount is not None and amount > 0 and amount % ROUND_AMOUNT_UNIT == 0:
            flags.append(Flag(
                check="round_sum", severity="low", where=_where(invoice),
                summary=f"the gross amount {amount} is a round multiple of {ROUND_AMOUNT_UNIT}",
                compared={"a": str(amount), "b": f"a multiple of {ROUND_AMOUNT_UNIT}"},
                source="the invoice's own gross amount",
                content_doc_id=str(invoice.get("content_doc_id") or "")))
    return flags


def _check_odd_timing(invoices):
    """out-of-hours sends -- reads the optional `sent_at`. This check used
    to also flag any invoice *dated* on a weekend, read from the core
    `invoice_date` field; that half was removed (H1 -- see the module
    docstring): a supplier's own invoicing run for the 1st of the month
    lands on a weekend roughly 2 times in 7, so it was a structural false
    positive on routine, correctly-dated invoices at a rate of roughly 29%,
    burying the genuine `sent_at` signal below in noise. `sent_at` alone
    survives as real, working signal about when a human or a compromised
    mailbox actually acted."""
    flags = []
    for invoice in invoices:
        where = _where(invoice)
        sent_at = invoice.get("sent_at")
        if isinstance(sent_at, datetime) and not (
                OUT_OF_HOURS_START_HOUR <= sent_at.hour < OUT_OF_HOURS_END_HOUR):
            flags.append(Flag(
                check="odd_timing", severity="low", where=where,
                summary=(f"the invoice was sent at {sent_at.isoformat()}, outside the "
                         f"{OUT_OF_HOURS_START_HOUR:02d}:00-{OUT_OF_HOURS_END_HOUR:02d}:00 window"),
                compared={"a": sent_at.isoformat(),
                         "b": f"{OUT_OF_HOURS_START_HOUR:02d}:00-{OUT_OF_HOURS_END_HOUR:02d}:00"},
                source="sent_at on the source email",
                content_doc_id=str(invoice.get("content_doc_id") or "")))
    return flags


def _check_near_duplicate_invoice(invoices):
    """same supplier, same amount, dates close together -- a pair that
    already shares an invoice number is left to the stronger, exact
    duplicate finding elsewhere and is not reported twice here."""
    flags = []
    for i in range(len(invoices)):
        for j in range(i + 1, len(invoices)):
            first, second = invoices[i], invoices[j]
            supplier_a = str(first.get("supplier") or "").strip()
            supplier_b = str(second.get("supplier") or "").strip()
            if not supplier_a or supplier_a != supplier_b:
                continue
            number_a = str(first.get("number") or "").strip()
            number_b = str(second.get("number") or "").strip()
            if number_a and number_a == number_b:
                continue
            amount_a = _read_amount(first.get("gross"))
            amount_b = _read_amount(second.get("gross"))
            if amount_a is None or amount_a != amount_b:
                continue
            date_a, date_b = first.get("invoice_date"), second.get("invoice_date")
            if not (isinstance(date_a, date) and isinstance(date_b, date)):
                continue
            span = abs((date_a - date_b).days)
            if span > NEAR_DUPLICATE_WINDOW_DAYS:
                continue
            flags.append(Flag(
                check="near_duplicate_invoice", severity="medium", where=_where(second),
                summary=(f"{_where(second)} looks like a near-duplicate of {_where(first)}: "
                         f"same supplier {supplier_a!r}, both {amount_a}, invoice dates "
                         f"{span} day(s) apart"),
                compared={"a": f"{_where(first)}: {amount_a} on {date_a.isoformat()}",
                         "b": f"{_where(second)}: {amount_b} on {date_b.isoformat()}"},
                source=f"invoice dates and gross amounts for {supplier_a!r}",
                content_doc_id=str(second.get("content_doc_id") or "")))
    return flags


def _check_hidden_instructions(invoices):
    """text in the file aimed at an AI reader -- reuses
    `cfo.safety.injection.scan_instruction_like` (C12) rather than
    reimplementing any pattern matching. Reads an optional `hidden_text`
    list on the invoice: `[{"text", "location", "technique"}, ...]`, the
    shape C1's own hidden-text findings already carry (see
    `cfo.extract.sink.finalise_findings`) -- absent or malformed, this is
    nothing to scan, not an error."""
    flags = []
    for invoice in invoices:
        where = _where(invoice)
        entries = invoice.get("hidden_text")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text") or "")
            if not text.strip():
                continue
            matches = scan_instruction_like(text)
            if not matches:
                continue
            patterns = ", ".join(sorted({match["pattern"] for match in matches}))
            location = str(entry.get("location") or entry.get("technique") or "hidden text")
            flags.append(Flag(
                check="hidden_instructions", severity="critical", where=where,
                summary=(f"hidden text in this document reads like an instruction to an AI "
                         f"reader ({patterns}), at {location}"),
                compared={"a": matches[0]["match"], "b": patterns},
                source=location, content_doc_id=str(invoice.get("content_doc_id") or "")))
    return flags


def check_batch(invoices, *, master=None, matrix=None, history=None):
    """`[Flag, ...]` -- every one of the twelve checks this module runs
    against `invoices`, in `CHECK_IDS` order. `master`, `matrix` and
    `invoices` itself are passed straight to `checks_performed`; a check
    whose `performed` comes back `False` is never even called, let alone
    called and found clean (see the module docstring). `history` is
    accepted, unused -- see `checks_performed`.

    `round_sum`, `odd_timing` and `iban_country_mismatch` are always called
    regardless of `performed`: unlike the five gated checks above, none of
    the three is ever *incapable* of finding something real for at least
    some invoice in the batch (a round amount always has an amount to test;
    an IBAN's country and an expected country do not need the same source
    for every invoice) -- `performed` for these three describes whether
    every signal they *could* use was present anywhere in the batch, not
    whether calling them at all would be pointless.

    An entry in `invoices` that is not a `dict` is dropped before any check
    sees it: this module reads every invoice with `.get()`, the same
    convention `cfo.payments.validate` and `cfo.payments.suppliers` both
    use, and a non-mapping entry has nothing `.get()` could safely read."""
    invoices = [invoice for invoice in (invoices or []) if isinstance(invoice, dict)]
    performed = checks_performed(invoices, master=master, matrix=matrix, history=history)

    flags = []
    if performed["changed_bank_details"]["performed"]:
        flags += _check_changed_bank_details(invoices, master)
    flags += _check_new_payee_large_amount(invoices, master)
    if performed["lookalike_supplier_name"]["performed"]:
        flags += _check_lookalike_supplier_name(invoices, master)
    if performed["lookalike_sender_domain"]["performed"]:
        flags += _check_lookalike_sender_domain(invoices, master)
    flags += _check_iban_country_mismatch(invoices, master)
    flags += _check_sequential_invoice_numbers(invoices)
    if performed["just_under_a_limit"]["performed"]:
        flags += _check_just_under_a_limit(invoices, matrix)
    if performed["split_to_stay_under"]["performed"]:
        flags += _check_split_to_stay_under(invoices, matrix)
    flags += _check_round_sum(invoices)
    flags += _check_odd_timing(invoices)
    flags += _check_near_duplicate_invoice(invoices)
    flags += _check_hidden_instructions(invoices)
    return flags
