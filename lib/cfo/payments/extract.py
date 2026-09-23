"""T5: invoice field extraction, and the one rule this milestone exists to
enforce -- **a model never types an IBAN or an account number.**

A transposed digit invented by a model is money gone, and it is not
recoverable. So the model (asked by the `payments.invoice-fields` task asset
at `assets/tasks/payments/invoice-fields/`) never returns the digits: it
returns a *location* -- copied verbatim from the `[label]` marker
`cfo.extract.sink.render_md`/`loc_label` already prints beside every passage
the model reads (`"p1"`, `"para 29"`, `"Sheet1!B12"`, ...) -- and this module
does the rest:

1. `find_located_block` finds every extracted block (out of C1's own
   blocks, never anything the model wrote) whose own rendered label matches
   what the model returned -- usually one (a Word or email paragraph's own
   number is unique to it), sometimes several (a PDF page's `loc` carries
   only the page number, so every block on it renders the same label) --
   and hands back their text joined together.
2. `lift_iban` scans that text for IBAN-shaped tokens and checksums each one
   with `cfo.treasury.iban.valid_iban`; `lift_account_number` scans it for a
   digit run sitting next to an "account" label. Either way, the literal
   characters lifted are always ones C1 already extracted from the source
   document -- never anything from the model's own output.
3. `invoice_from_output` calls both, for every invoice, and folds whichever
   of the two actually resolved into the rest of the invoice's fields.

**This scanning step is not a relaxation of the "never trust the model"
rule -- it is a tighter version of it.** The earlier shape of this contract
asked the model for a coordinate (`{"page", "line"}` or `{"cell"}`) and
trusted whatever text sat at that exact spot, without checking it looked
like an IBAN at all. This shape asks the model for *less* -- one string,
copied from what is already printed in front of it, never composed or
counted -- and then does *more* with it: the located block's text is
searched for something that has the right shape and passes a real
checksum, and a script only ever hands back a value that cleared that bar.
A model that pointed at the wrong block, or that (ignoring every
instruction) tried to smuggle digits into the location string itself,
produces either no candidate, a candidate that fails its checksum, or -- at
worst -- a location string that happens to equal no real block's label, all
of which fail exactly the way an honest miss would. Nothing here ever
becomes more willing to accept an unchecked value than the coordinate-based
version was; it is only more willing to say *why* a location did not
resolve, and more able to resolve locations that a real document -- where
an IBAN prints as "IBAN: <value>" on one line, and a Word or email
paragraph has a paragraph number rather than a page and line -- actually
produces.

The schema this reads (`assets/tasks/payments/invoice-fields/schema.json`)
makes a digit-bearing bank-details field structurally impossible, not merely
discouraged: `additionalProperties: false` throughout means the model's
output has no `iban` or `account_number` property at all to write one into,
and `iban_location`/`account_number_location` accept only a plain string --
a location label, never a container a model could stuff digits into the way
the old `{"cell": "<22 characters>"}` shape at least made structurally
awkward (and this shape does not even give it that: a string has no field
of its own for a digit run to hide inside; it only ever gets treated as a
label to look up, never as text to lift, see `find_located_block` below).
This module is the other half of that contract: it never reads a
bank-details value out of `output` -- only a location label -- so there is
no code path here that could carry a model-typed digit into a payment even
if the schema were somehow bypassed.

A location that does not resolve to a real block, an IBAN-shaped token that
fails its checksum, or a block that holds more than one thing that could be
the answer, is an *exception*: it is reported in the warnings list this
module returns, and the corresponding bank-details key is left out of the
returned invoice dict entirely -- never filled in blank. A payment builder
reading this dict for its settlement details sees either a real, checked
value or nothing, never a hole that looks like a value. **Ambiguity is
always an exception, never a guess** -- two IBAN-shaped tokens in a block
that both pass their checksum, or two digit runs each sitting next to an
"account" label, are exactly as unresolved as none at all; this module never
picks between them.

Amounts, dates and the other ordinary fields are passed through unchanged,
as the strings the model wrote (a schema.json pattern keeps them to a plain
`YYYY-MM-DD` date or a plain decimal string -- see that file). This module
does not parse or check them as numbers: whether net + VAT = gross is T6's
job (`cfo.payments.validate`), never this module's -- a model that computed
that itself would be a model that could get it wrong silently, which is
exactly what this whole extraction is built to avoid.

`contact_email` (T16) is one of these ordinary fields -- an email address
printed on the invoice itself, if one is, carried through exactly like
`supplier_name` or `cost_category`. It carries no bank-detail risk (a
wrong email address costs nothing the way a wrong digit in an IBAN does),
so it needs none of the location-and-lift machinery above; it exists so
`cfo.payments.fraud.lookalike_sender_domain` can compare the domain a mail
arrived from against the domain the supplier's own letterhead states,
with no history required -- see that module's own docstring.

`bic` and `vat_number` (T5 review, B3/H4) join `contact_email` as ordinary
scalar fields for the same reason: a BIC identifies a bank, a VAT number
identifies a business, and neither is a digit that moves money on its own
the way an IBAN or account number is -- so both are extracted as a plain
`{"value", "quote"}` pair, never a location. Before this fix, both were
hardcoded to `""` downstream (`cfo.payments.cli.cmd_review`), which meant
no supplier paid by account number alone could ever clear the `bic`/
`bank_code` check, and no brand-new payee's country could ever be checked
against its own VAT prefix. See `SCALAR_FIELDS` below.

`bill_to` (D2, 2026-09-22 real run) joins them for the same reason: it names
a customer, not an account, so it carries none of the risk an IBAN or
account number does either. It exists so `cfo.payments.cli` can compare the
party an invoice is actually addressed to against the debtor name a run was
started with, and warn -- loudly, never refusing -- when they disagree: the
real defect this closes recorded Hedj as payer while both real invoices were
billed to Booterstown United F.C., and said nothing at all. A blank
`bill_to` is a real, reportable outcome in its own right (most invoices
until this field has had a live-model verification run against real
samples), not a reason to guess one from the supplier's own name or invent
one from context -- see that module's own docstring for what the comparison
does with it.

This module also fixes a second defect the same review found (H2):
`_bank_detail` used to call its lifter unconditionally for both
`iban_location` and `account_number_location`, so whichever one a given
invoice does not state -- almost every real invoice states only one --
always produced "could not be resolved ... no payment line will be built
from it", a warning that fired on every single invoice and therefore said
nothing. A blank location is now only ever attempted when it is not
blank; see `_bank_detail`'s own docstring for exactly which cases still
warn.
"""
import re

from cfo.console import ToolkitError
from cfo.extract.sink import loc_label

# The ordinary (non-bank-details) fields this task asks for, and the shape
# every one of them shares in the schema: {"value": <str>, "quote": <str>}.
# Keeping this list here, rather than re-deriving it from the schema file at
# import time, means a caller building or testing `output` by hand has one
# place to check it against.
#
# `bic` (T5 review, B3) and `vat_number` (T5 review, H4) are ordinary
# scalar fields, not bank-details locations: a BIC/SWIFT code identifies a
# bank, not an account, and a VAT number identifies a supplier, not a
# payment destination -- neither one, on its own, moves money the way an
# IBAN or account number does, so neither needs the location-and-lift
# machinery above. `cfo.payments.cli.cmd_review`'s `has_account` check
# needs a real `bic` to ever let an account-number-only supplier be paid,
# and `cfo.payments.fraud.iban_country_mismatch` needs a real `vat_number`
# to have anything to fall back on for a brand-new payee with no master
# record and no extracted `country` field -- see both modules' docstrings.
#
# `bill_to` (D2, 2026-09-22 real run) is the same kind of ordinary field:
# the customer's own name off a "Bill to:" line, never a payment
# destination on its own. `cfo.payments.cli.cmd_check`/`cmd_review` compare
# it against the debtor name a run was started with, and against
# `supplier_name` (D3, the same run), to catch a payment about to be built
# for the wrong company -- see the module docstring above.
SCALAR_FIELDS = ("supplier_name", "invoice_number", "invoice_date", "due_date", "net", "vat",
                 "gross", "currency", "payment_reference", "cost_category", "contact_email",
                 "bic", "vat_number", "bill_to")

# A run of letters and digits, the unit both the IBAN- and account-number
# scans split a block's text into -- splitting on anything that is not
# alphanumeric (":", whitespace, "/", ...) is what turns "IBAN:
# GB44WNTM60071199887766" (label and value printed on the one line every
# real invoice actually uses) into two tokens rather than one run neither
# scan could make sense of.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

# 2 letters (country) + 2 digits (check digits) + 11-30 more letters/digits:
# the general ISO 13616 shape (15-34 characters total), checked here only
# to keep obviously-wrong tokens (an invoice number, a date, a phone number)
# out of the checksum step below -- `cfo.treasury.iban.valid_iban` is the
# real gate, this is only a shape filter ahead of it.
_IBAN_SHAPE_RE = re.compile(r"^[A-Za-z]{2}[0-9]{2}[A-Za-z0-9]{11,30}$")

# A domestic account number has no checksum to gate it, so a candidate is
# only ever a digit run of a plausible length that sits on the same line as
# the word "account" -- see `lift_account_number`.
_DIGIT_RUN_RE = re.compile(r"\d{4,20}")


def _doc_list(extracted):
    return extracted if isinstance(extracted, list) else [extracted]


def _block_text(block):
    """The block's own text -- a table's rows joined into one string, the
    same way `cfo.extract.runner._block_text` reads a table for hint
    matching, since a bank detail sitting in a table cell is exactly as
    real as one sitting in a paragraph. Every other block kind already
    carries its text directly."""
    if block.get("kind") == "table":
        return "\n".join(" ".join(str(c) for c in row) for row in (block.get("rows") or []))
    return str(block.get("text") or "")


def _matching_blocks(label, docs):
    """Every block, across `docs` in order, whose own rendered location
    (`cfo.extract.sink.loc_label`) equals `label` exactly -- the same label
    a model was shown printed in square brackets ahead of every passage it
    read, so this is a lookup against text the model actually saw, never a
    coordinate it had to derive.

    A label is not always one block: a PDF page's `loc` carries only
    `{"page": N}` (`cfo.extract.pdf` has no paragraph or line concept to
    put in it), so *every* block on that page renders to the same `"pN"`
    label -- unlike a Word or email paragraph's own `para` number, which is
    one block's alone. Returning every match, not just the first, is what
    lets `find_located_block` still resolve a PDF invoice, where "the
    passage at p1" honestly means "somewhere on page 1", not "whichever
    block happened to come first there."""
    return [block for doc in docs for block in (doc.get("blocks") or [])
            if loc_label(block.get("loc") or {}) == label]


def find_located_block(location, extracted):
    """`(text, warnings)`: the text of every block whose own rendered label
    equals `location`, joined in document order, out of `extracted` (one C1
    document dict, or a list of several, in the shape
    `cfo.extract.runner.field_groups` also takes).

    `location` must be the label string itself (`"p1"`, `"para 29"`,
    `"Sheet1!B12"`, ...) -- never a coordinate object; anything else
    (`None`, a dict, a number, an empty or blank string) fails the same way
    a genuinely wrong label would, with `("", [warning])`. A blank location
    is the honest "no bank detail was found on this invoice" answer a model
    gives, so it is reported the same way as any other non-resolving
    location, not raised as a different kind of error.

    Returns `("", [warning])`, never a partial or best-guess string, when
    the label matches no block, or matches only blocks whose text is blank
    -- a caller has exactly one thing to check, whether the first element
    is non-empty, and neither failure mode should ever look different from
    the other. When a label matches several blocks (see
    `_matching_blocks`), joining their text rather than picking one is not
    a loosening of "never guess": the IBAN and account-number scans below
    still only ever accept an unambiguous answer *within* that joined text,
    so a page that genuinely carries two candidate IBANs is still an
    exception, not a coin toss over which block to trust."""
    if not isinstance(location, str) or not location.strip():
        return "", [("location", "must be the location label exactly as shown ahead of the "
                                 "passage it came from, for example 'p1' or 'para 29'")]
    label = location.strip()
    blocks = _matching_blocks(label, _doc_list(extracted))
    if not blocks:
        return "", [("location", f"no passage at location '{label}' was found in the "
                                 "extracted document")]
    text = "\n".join(t for t in (_block_text(b).strip() for b in blocks) if t)
    if not text:
        return "", [("location", f"location '{label}' was found but is blank")]
    return text, []


def _spaced_iban_candidates(line):
    """IBAN-shaped candidates compacted from runs of alphanumeric groups on
    `line` joined by exactly one space each -- the ISO 13616 *printed*
    format real invoices actually use ("IBAN: IE29 AIBK 9311 5212 3456
    78"), which `_TOKEN_RE` alone would split into six short tokens, none
    15 characters or longer, so `_IBAN_SHAPE_RE` could never match any of
    them (D1, 2026-09-22: this is why a spaced IBAN used to vanish before
    the checksum step ever ran).

    Compacting is lossless: every character `_TOKEN_RE` found survives, in
    order, and only the separating spaces are dropped -- see `lift_iban`'s
    own docstring for why that is not the "re-cased or reformatted" this
    module otherwise refuses to do.

    `line` is always one line's own text -- callers pass one line at a
    time (see `_iban_candidates`), never a whole block joined together --
    so a group can never be welded to text on the line before or after it.
    An IBAN that wraps a line is possible but rare, and joining across
    lines risks combining two unrelated values into one that happens to
    pass the checksum by luck; this function has no way to do that even by
    accident.

    Every contiguous window of two or more tokens within a maximal
    single-space-joined run is tried, not only the run as a whole: a real
    IBAN sitting right before unrelated text with nothing but a single
    space between them ("...3456 78 Account name: ...") must still resolve
    to itself, not be rejected because the *whole* run, compacted, no
    longer has the right shape. A prose sentence, where adjacent words
    joined this way essentially never happen to produce something 15-34
    characters long starting with two letters then two digits, is not
    expected to produce anything here at all."""
    tokens = [(m.group(), m.start(), m.end()) for m in _TOKEN_RE.finditer(line)]
    candidates = []
    n = len(tokens)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and line[tokens[j][2]:tokens[j + 1][1]] == " ":
            j += 1
        if j > i:  # a run of 2+ tokens joined by exactly single spaces
            for start in range(i, j + 1):
                for end in range(start + 2, j + 2):
                    candidates.append("".join(tok for tok, _s, _e in tokens[start:end]))
        i = j + 1
    return candidates


def _iban_candidates(text):
    """Every IBAN-shaped token in `text` -- the plain unspaced tokens
    `_TOKEN_RE` finds, plus (`_spaced_iban_candidates`) the ISO 13616
    printed form, grouped in fours or however else a bank happens to
    print it, compacted -- scanned one line at a time so nothing is ever
    joined across a newline. Order is not significant: every caller either
    deduplicates (`lift_iban`) or only checks membership."""
    candidates = []
    for line in text.splitlines():
        candidates += [tok for tok in _TOKEN_RE.findall(line) if _IBAN_SHAPE_RE.match(tok)]
        candidates += [cand for cand in _spaced_iban_candidates(line)
                       if _IBAN_SHAPE_RE.match(cand)]
    return candidates


def lift_iban(location, extracted, *, valid_iban):
    """`(literal, warnings)` for the IBAN at `location` -- the block's own
    text (`find_located_block`) scanned for IBAN-shaped tokens
    (`_iban_candidates`), each checksummed with `valid_iban`
    (`cfo.treasury.iban.valid_iban` in real use; injected here the same way
    `invoice_from_output` injects it, so a caller can test the checksum
    branch with a fake).

    Exactly one checksum-passing candidate resolves. The literal returned
    is always made only of characters `_TOKEN_RE` found in the source text,
    in the order they appeared there -- never re-cased, never a digit
    retyped or guessed. **That includes removing the spaces from the ISO
    13616 *printed* form real invoices actually use** ("IE29 AIBK 9311
    5212 3456 78"): this is not the "re-cased or reformatted" this module
    otherwise refuses to do, because the *electronic* form -- the one ISO
    13616 itself defines, and the one a pain.001 `IBAN` element requires,
    rejecting anything else -- carries no separators at all. The grouped,
    spaced form is only ever how the same value is printed for a human to
    read; the compacted string is not a reconstruction of the IBAN, it
    *is* the IBAN. Compacting is lossless -- every character the source
    printed survives, in order, nothing added, guessed, or dropped, never
    joined across a line break -- and a model still never sees or supplies
    a single one of these digits either way.

    Zero, or more than one, checksum-passing candidate is an exception
    naming what was found: **ambiguity is never a guess between
    candidates.** Two identical tokens -- the same IBAN printed twice, or
    the same IBAN found once compacted from spaced groups and once already
    unspaced -- count once, not twice: that is the same answer stated
    twice over, not two different answers to choose between."""
    text, warnings = find_located_block(location, extracted)
    if not text:
        return "", warnings
    candidates = _iban_candidates(text)
    passing = list(dict.fromkeys(tok for tok in candidates if valid_iban(tok)))
    if len(passing) == 1:
        return passing[0], []
    if not passing:
        found = f" (found: {', '.join(dict.fromkeys(candidates))})" if candidates else ""
        return "", [("location", f"no IBAN-shaped text that passes its checksum was found at "
                                 f"'{location}'{found}")]
    return "", [("location", f"more than one IBAN-shaped value at '{location}' passes its "
                             f"checksum ({', '.join(passing)}); the correct one cannot be "
                             "chosen automatically")]


def lift_account_number(location, extracted):
    """`(literal, warnings)` for the account number at `location` -- the
    block's own text (`find_located_block`), read line by line, for a digit
    run of 4-20 digits sitting on the same line as the word "account"
    (case-insensitive: "Account number:", "Account No.", ...). Account
    numbers carry no checksum, so this can never validate a candidate the
    way `lift_iban` does; a block with more than one distinct digit run
    next to an "account" label is exactly as unresolved as one with none --
    an exception naming what was found, never a guess between them. The
    same digit run repeated on two lines (the number printed twice) is one
    answer, not two."""
    text, warnings = find_located_block(location, extracted)
    if not text:
        return "", warnings
    candidates = []
    for line in text.splitlines():
        if "account" not in line.lower():
            continue
        candidates += _DIGIT_RUN_RE.findall(line)
    unique = list(dict.fromkeys(candidates))
    if len(unique) == 1:
        return unique[0], []
    if not unique:
        return "", [("location", f"no digit run next to an 'account' label was found at "
                                 f"'{location}'")]
    return "", [("location", f"more than one digit run next to an 'account' label was found at "
                             f"'{location}' ({', '.join(unique)}); the correct one cannot be "
                             "chosen automatically")]


def _scalar_value(output, name):
    """`(value, warnings)` for one of `SCALAR_FIELDS`: the field's own
    `value`, passed through exactly as the model wrote it (a string, never
    parsed, never computed -- see the module docstring), and a warning when
    a value is reported with no quote to support it or a quote with no
    value -- the same "a value with no quote is not a value" rule
    `cfo.extract.runner.collect` and `cfo.policy.coverage` both already
    apply, kept consistent here rather than invented a third way. A field
    genuinely absent from the invoice is reported as blank value and blank
    quote together, which is not a warning -- it is the honest, expected
    outcome for a due date or a cost category this invoice never states."""
    entry = output.get(name)
    if not isinstance(entry, dict):
        return "", [(name, "was not reported")]
    value = str(entry.get("value") or "").strip()
    quote = str(entry.get("quote") or "").strip()
    if value and not quote:
        return "", [(name, "a value was reported with no supporting quote; it was ignored")]
    if quote and not value:
        return "", [(name, "a quote was reported with no value; it was ignored")]
    return value, []


def _bank_detail(where, location_field, output, extracted, *, lifter):
    """`(literal, warnings)` for one bank-details field: whatever `lifter`
    (`lift_iban` or `lift_account_number`, already bound to anything it
    needs beyond `location`/`extracted`) resolves at `output[location_field]`.
    A location that does not resolve, or a block that resolves to nothing
    usable, is reported as a warning at `where` and returns `""` -- never
    an unchecked literal, and never a location's own text standing in for a
    value.

    A **blank** location is not attempted at all, and is never a warning by
    itself here (T5 review, H2): most invoices state only an IBAN or only
    an account number, never both, so the field the invoice does not use is
    expected to come back with no location -- exactly as blank as a due
    date this invoice never printed (see `_scalar_value`). The old
    behaviour called `lifter` unconditionally, which for a blank location
    always failed inside `find_located_block` and always added "could not
    be resolved ... no payment line will be built from it" -- a warning
    that fired on whichever field an invoice does not use, on every single
    invoice, and therefore carried no information at all. Only a
    **non-blank** location that still fails to resolve reaches `lifter`
    and is still reported exactly as before -- that failure is real and
    stays a warning. Whether *both* locations came back blank (so neither
    bank detail resolved and no payment line can be built for this invoice
    at all) is `invoice_from_output`'s call, not this function's -- it is
    the one place that sees both fields together.

    `where` names the invoice this bank detail belongs to (see
    `invoice_from_output`, which builds it from the invoice number), the
    same "the reader must be able to find this row without counting them"
    rule `cfo.payments.validate.check_invoice` (T6) applies to its own
    `where` values -- an exception that only said "iban" would be useless
    once a caller has more than one invoice's exceptions in front of it."""
    location = output.get(location_field)
    if not isinstance(location, str) or not location.strip():
        return "", []
    literal, warnings = lifter(location, extracted)
    warnings = [(where, message) for _where, message in warnings]
    if not literal:
        return "", warnings + [(where, "could not be resolved from the location given; "
                                       "no payment line will be built from it")]
    return literal, warnings


def invoice_from_output(output, extracted, *, valid_iban=None):
    """`(invoice, exceptions)`: the accepted `payments.invoice-fields` task
    output, plus the lifted and checksummed bank details, folded into one
    dict -- and every `(where, message)` exception raised while doing it.

    `invoice` carries `SCALAR_FIELDS` (each the plain string the model
    wrote, unchanged -- see the module docstring on why amounts are never
    parsed or checked here) plus `iban` and/or `account_number` **only**
    when each was actually resolved this way -- the IBAN via `lift_iban`
    (a checksum-passing candidate found in the located block), the account
    number via `lift_account_number` (an unambiguous digit run next to an
    "account" label there). A field that could not be resolved this way is
    left out of `invoice` entirely, not set to `""`: a caller building a
    payment from this dict must be unable to mistake "no bank details
    found" for "an empty bank details string", which is exactly the
    difference between an exception and a payment line built on nothing.

    `valid_iban` defaults to `cfo.treasury.iban.valid_iban`, imported
    lazily and only once there is a located block to check candidates in --
    the same injection pattern `cfo.payments.model.validate_batch` uses, so
    a caller can test the checksum branch with a fake and an invoice with no
    IBAN at all never needs `cfo.treasury.iban` to exist.

    Net + VAT = gross is never checked here -- that is
    `cfo.payments.validate.check_invoice` (T6). This function's only job on
    those three fields is to carry the strings the model wrote through
    unchanged."""
    if not isinstance(output, dict):
        raise ToolkitError(("output", "invoice output must be an object"))

    invoice, exceptions = {}, []
    for name in SCALAR_FIELDS:
        value, warnings = _scalar_value(output, name)
        invoice[name] = value
        exceptions.extend(warnings)

    # Names the invoice an exception belongs to, the same way T6's own
    # `where` values do: its number when the model read one, "invoice"
    # otherwise -- never bare "iban", which tells a caller nothing once it
    # has more than one invoice's exceptions in front of it.
    label = invoice.get("invoice_number") or "invoice"

    def _lift_iban(location, docs):
        check = valid_iban
        if check is None:
            from cfo.treasury.iban import valid_iban as check
        return lift_iban(location, docs, valid_iban=check)

    iban, iban_warnings = _bank_detail(f"{label}.iban", "iban_location", output, extracted,
                                       lifter=_lift_iban)
    exceptions.extend(iban_warnings)
    if iban:
        invoice["iban"] = iban

    account_number, account_warnings = _bank_detail(
        f"{label}.account_number", "account_number_location", output, extracted,
        lifter=lift_account_number)
    exceptions.extend(account_warnings)
    if account_number:
        invoice["account_number"] = account_number

    # H2's other half: a blank location on its own is never a warning (see
    # `_bank_detail`), but a blank location on *both* fields together means
    # neither bank detail resolved and no payment line can be built for
    # this invoice at all -- a real, actionable finding, reported once here
    # rather than not at all. Gated on there being no warning already for
    # either field: a location that was given but failed to resolve has
    # already said so, at that field, and does not need this on top.
    if not iban and not account_number and not iban_warnings and not account_warnings:
        exceptions.append((label, "no IBAN or account number location was given; no payment "
                                  "line will be built from this invoice"))

    return invoice, exceptions
