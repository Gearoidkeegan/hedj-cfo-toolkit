"""T7: the supplier master -- the one thing T8's bank-detail-change check
compares an invoice against.

A record holds `supplier_id`, `name`, known `aliases`, `country`, `iban`,
`account_number`, `bic`, `vat_number`, `domain`, `first_seen`, `last_seen`,
`invoice_count` and `bank_detail_changes`. `load_master`/`save_master` read
and write it as CSV or Excel (`.csv`, `.xlsx`/`.xlsm`); both give back
identical records for the same data, because `save_master` writes the exact
columns `load_master` reads back, in both formats, and nothing here treats
one format as more authoritative than the other. A file written before
`domain` existed simply has no such column; it still loads, with `domain`
blank on every record, never a `ToolkitError` -- see `_row_to_record`.

**A column this module does not know about is never dropped (T5 review,
H5).** A customer's real master carries columns this tool has no use for
-- `payment_terms`, `ap_contact`, whatever their own process needs -- and
the finding this fixes was exact: accepting one bank-detail change and
writing the file back silently deleted every one of them, no backup, no
warning, because `save_master` wrote only `FIELDS`. Every column
`_row_to_record` does not recognise is now kept verbatim (as the plain
string it read, under its own original column name) and written straight
back by `_record_to_row`/`save_master`, at the end of the row, in the
order it was first seen. A supplier record built in memory (`remember`'s
new-record branch, a test fixture) simply has none of these extra
columns, and that is not an error either -- only a file that already
carried them has anything to preserve.

This was weighed against the finding's other option, writing every change
to a new dated file instead of overwriting the customer's own: rejected,
because a finance team that runs this against the same master every week
would get a new file every week, and the whole point of a master is that
there is one of it. Preserving unknown columns, plus the `bank_detail_changes`
column below, keeps the single file the customer handed over intact and
growing an honest history, rather than either destroying it or multiplying
it.

**`apply_approved_change`'s audit entry now survives a save (the other
half of H5).** It builds a `bank_detail_changes` list entry naming the
field, the old and new value, who approved it and when -- and used to
have it thrown away the moment `save_master` wrote the file back, because
`FIELDS` had no column for it. `bank_detail_changes` is now a real column:
written as a JSON list (`_serialise_history`) and read back the same way
(`_parse_history`), defaulting to `[]` for a file written before this
column existed, exactly like every other backward-compatible field here.
The docstring on `apply_approved_change` promises the previous value is
kept beside the new one, and now that promise survives a round trip
through disk, not only the in-memory dict a caller happens to hold onto
after calling it.

**`match` is exact and near-exact only.** Case-folded exact, then a known
alias, then a normalised form with punctuation and a legal suffix (Ltd,
GmbH, BV, SA, ...) stripped -- never a similarity score, never a substring
or prefix match. "Acme Holdings Ltd" and "Acme Ltd" normalise to two
different strings ("acme holdings" and "acme") and so never match each
other: they read alike, but nothing here treats "reads alike" as "is the
same company". Anything short of exact or near-exact returns `(None,
reason)`; the caller -- not this module -- asks the `supplier-match` task
to weigh a shortlist of candidates with a model, and even that model never
invents an id (see `assets/tasks/payments/supplier-match/`). A fuzzy match
made silently here is exactly how a lookalike supplier gets paid, so this
module never makes one.

**`bank_change` reports a disagreement, never a guess at which side is
right.** It returns `None` when the invoice's stated bank details agree
with the master's, or a dict naming the field, `old` **and** `new` (both,
always -- a change flag a person cannot see both sides of is not
actionable), the `supplier_id` and the record's `last_seen`. Values are
compared with whitespace and case ignored, so "DE89 3704 ..." and
"DE89370400..." are not reported as a change over formatting alone.

**`remember` never lets a bank-detail change launder itself into the
master.** For a supplier already in the master, it advances `last_seen`
and `invoice_count` and folds in a new alias when the invoice's own
spelling of the name differs from the record's -- but it leaves `iban`,
`account_number` and `bic` exactly as they were. If it copied whatever the
current invoice states into the master, the very next run of `bank_change`
would compare the master against itself and never see the swap that made
`remember` run in the first place. Accepting a reviewed bank-detail change
is therefore a deliberate act by whatever calls this (not built by T7); an
ordinary run of `remember` is bookkeeping, not an approval. Only a
genuinely unseen supplier gets its bank details recorded here, because
there is no prior truth yet for it to overwrite.

**`domain` is learned the opposite way round, and deliberately so.** It is
recorded only for a supplier `remember` already recognised (an invoice
that matched an existing record), from that invoice's own `sender_email`
-- never for the record `remember` is creating in the same call. A
brand-new supplier's very first mail might be the fraud itself; if this
recorded a domain from it, that fraudulent domain would become "the
record", and every genuine invoice afterwards would flag against it while
the fraud passed clean. Waiting for a supplier to already be known before
trusting a mail's domain closes exactly that hole -- see
`cfo.payments.fraud`'s own module docstring for the comparison this
enables (T16)."""
import csv
import io
import json
import os
import re
from datetime import date, datetime

from cfo.console import ToolkitError
from cfo.io import save_via_temp, write_text_atomic

FIELDS = ("supplier_id", "name", "aliases", "country", "iban", "account_number", "bic",
          "vat_number", "domain", "first_seen", "last_seen", "invoice_count")
# The audit trail `apply_approved_change` builds -- a real column now (T5
# review, H5), serialised as a JSON list rather than added to the plain
# scalar `FIELDS` above, because a history is a list of records, not a
# single value `_record_to_row` can format the way it formats a date or a
# count.
HISTORY_FIELD = "bank_detail_changes"
ALIAS_SEPARATOR = "; "

# Legal suffixes _normalise strips, trailing-token by trailing-token, so
# "Acme Trading Company Ltd" loses "ltd" then stops -- never "trading
# company" too, which would risk merging two different businesses. Only the
# ten-ish forms a treasury team is likely to actually see; a suffix outside
# this set is left in place; see the module docstring on why that is the
# safe default, not a gap to be "fixed" with a similarity score.
_LEGAL_SUFFIXES = frozenset({
    "ltd", "limited", "llc", "inc", "incorporated", "plc", "corp", "corporation", "co",
    "company", "gmbh", "ag", "kg", "mbh", "bv", "nv", "sa", "sarl", "sas", "sl", "slu",
    "spa", "srl", "oy", "oyj", "ab", "as", "aps", "asa", "pty", "pte",
})
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_BANK_FIELDS = ("iban", "account_number", "bic")


def _normalise(name):
    """Case-folded, punctuation stripped to a space, legal suffix tokens
    dropped from the end -- see the module docstring for why this stops at
    "near-exact" and never becomes a similarity score."""
    text = _PUNCT_RE.sub(" ", str(name or "").casefold())
    tokens = text.split()
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def same_party(a, b):
    """True when `a` and `b` name the same entity once case, punctuation
    and a legal-suffix token are stripped -- the near-exact test `match`
    already applies to a name against the supplier master (`_normalise`),
    reused rather than reinvented (D2/D3, 2026-09-22 real run: comparing an
    invoice's own bill-to party against the debtor name a run was started
    with, and against its own `supplier_name`) so the two callers can never
    quietly disagree about what "the same party" means.

    Also ignores internal spacing, which `_normalise` alone does not:
    "Booterstown United F.C." and "BOOTERSTOWN UNITED FC" normalise to
    "booterstown united f c" and "booterstown united fc" respectively --
    one space apart, because stripping "." leaves a gap `_normalise` never
    closes -- and are still the same club, not two different spellings.
    Comparing with spacing removed on top of `_normalise` closes exactly
    that gap without touching `_normalise` itself or its own callers.

    Blank on either side is never a match: there is nothing to compare a
    blank name against, and a caller with nothing on one side has a
    different, more honest finding to report (see `cfo.payments.cli`) than
    a false "these agree"."""
    left = _normalise(a).replace(" ", "")
    right = _normalise(b).replace(" ", "")
    if not left or not right:
        return False
    return left == right


def _parse_aliases(raw):
    if isinstance(raw, (list, tuple)):
        return [str(a).strip() for a in raw if str(a).strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    return [a.strip() for a in text.split(";") if a.strip()]


def _format_aliases(aliases):
    return ALIAS_SEPARATOR.join(aliases or [])


def _parse_date(value, where, field):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ToolkitError((where, f"{field} {value!r} is not a date (expected YYYY-MM-DD)"))


def _format_date(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _parse_invoice_count(value, where):
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        raise ToolkitError((where, f"invoice_count {value!r} is not a whole number"))
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value).strip()
    if not text:
        return 0
    try:
        return int(text)
    except ValueError:
        raise ToolkitError((where, f"invoice_count {value!r} is not a whole number"))


def _parse_history(raw, where):
    """`bank_detail_changes` as a list of dicts, from the JSON text a
    previous `save_master` wrote -- `[]` for a blank or missing column (a
    file written before this column existed, or a brand-new record), never
    a `ToolkitError` for the ordinary, backward-compatible case. Malformed
    JSON, or JSON that is not a list, IS a `ToolkitError`: this is an audit
    trail, not a field to patch over with a guess at what it meant."""
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except ValueError:
        raise ToolkitError((where, f"{HISTORY_FIELD} is not valid JSON: {raw!r}"))
    if not isinstance(parsed, list):
        raise ToolkitError((where, f"{HISTORY_FIELD} must be a JSON list, got {type(parsed).__name__}"))
    return parsed


def _serialise_history(history):
    return json.dumps(list(history or []))


def _row_to_record(row, where):
    supplier_id = str(row.get("supplier_id") or "").strip()
    name = str(row.get("name") or "").strip()
    if not supplier_id or not name:
        raise ToolkitError((where, "needs both a supplier_id and a name"))
    record = {
        "supplier_id": supplier_id,
        "name": name,
        "aliases": _parse_aliases(row.get("aliases")),
        "country": str(row.get("country") or "").strip(),
        "iban": str(row.get("iban") or "").strip(),
        "account_number": str(row.get("account_number") or "").strip(),
        "bic": str(row.get("bic") or "").strip(),
        "vat_number": str(row.get("vat_number") or "").strip(),
        "domain": str(row.get("domain") or "").strip().lower(),
        "first_seen": _parse_date(row.get("first_seen"), where, "first_seen"),
        "last_seen": _parse_date(row.get("last_seen"), where, "last_seen"),
        "invoice_count": _parse_invoice_count(row.get("invoice_count"), where),
        HISTORY_FIELD: _parse_history(row.get(HISTORY_FIELD), where),
    }
    # T5 review, H5: any column this module does not otherwise recognise
    # is a customer's own -- payment terms, an AP contact, anything their
    # own process needs -- and is kept exactly as read, under its own
    # name, so a round trip through this module never deletes it. `row`
    # preserves the source file's own column order (see `_read_csv_rows`/
    # `_read_xlsx_rows`), so iterating it keeps that order too.
    known = set(FIELDS) | {HISTORY_FIELD}
    for key, value in row.items():
        if key is None or key in known:
            continue
        record[key] = "" if value is None else str(value)
    return record


def _record_to_row(record):
    row = {
        "supplier_id": record.get("supplier_id", ""),
        "name": record.get("name", ""),
        "aliases": _format_aliases(record.get("aliases")),
        "country": record.get("country") or "",
        "iban": record.get("iban") or "",
        "account_number": record.get("account_number") or "",
        "bic": record.get("bic") or "",
        "vat_number": record.get("vat_number") or "",
        "domain": record.get("domain") or "",
        "first_seen": _format_date(record.get("first_seen")),
        "last_seen": _format_date(record.get("last_seen")),
        "invoice_count": str(int(record.get("invoice_count") or 0)),
        HISTORY_FIELD: _serialise_history(record.get(HISTORY_FIELD)),
    }
    known = set(FIELDS) | {HISTORY_FIELD}
    for key, value in record.items():
        if key in known:
            continue
        row[key] = "" if value is None else str(value)
    return row


def _all_fieldnames(rows):
    """The CSV/Excel header to write: `FIELDS`, then `HISTORY_FIELD`, then
    every unknown column any row carries (T5 review, H5), each added once,
    in the order it is first seen across `rows` -- so a master with no
    unknown columns writes exactly the header it always did, and one that
    has them keeps them stable run to run rather than reordering
    alphabetically or by whichever record happens to sort first."""
    fieldnames = list(FIELDS) + [HISTORY_FIELD]
    seen = set(fieldnames)
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    return fieldnames


def _read_csv_rows(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return []
        return [dict(row) for row in reader if any(str(v or "").strip() for v in row.values())]


def _read_xlsx_rows(path):
    from openpyxl import load_workbook  # lazy: a CSV-only caller needs no openpyxl at all
    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        try:
            header = [str(h or "").strip() for h in next(rows)]
        except StopIteration:
            return []
        result = []
        for values in rows:
            if not any(v is not None and str(v).strip() for v in values):
                continue
            result.append(dict(zip(header, values)))
        return result
    finally:
        workbook.close()


def _write_csv(rows, path, fieldnames):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    write_text_atomic(path, buffer.getvalue())


def _write_xlsx(rows, path, fieldnames):
    from openpyxl import Workbook  # lazy: a CSV-only caller needs no openpyxl at all
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(list(fieldnames))
    for row in rows:
        sheet.append([row.get(field, "") for field in fieldnames])
    save_via_temp(path, workbook.save, suffix=".xlsx")


def load_master(path):
    """`{supplier_id: record}` read from `path` (CSV or Excel), or `{}` when
    `path` is `None` -- there is no supplier master yet, and that is not an
    error. A row missing `supplier_id` or `name`, or a duplicate
    `supplier_id`, is a `ToolkitError`: this is the reference data the fraud
    checks trust, not something to patch over with a guess."""
    if path is None:
        return {}
    if not os.path.isfile(path):
        raise ToolkitError(("path", f"supplier master file not found: {path}"))
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        rows = _read_csv_rows(path)
    elif ext in (".xlsx", ".xlsm"):
        rows = _read_xlsx_rows(path)
    else:
        raise ToolkitError(("path", f"unsupported supplier master file type '{ext}'; "
                                    "use .csv or .xlsx"))
    master = {}
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        where = f"row {i}"
        record = _row_to_record(row, where)
        if record["supplier_id"] in master:
            raise ToolkitError((where, f"duplicate supplier_id {record['supplier_id']!r}"))
        master[record["supplier_id"]] = record
    return master


def save_master(master, path):
    """Write `master` to `path` as CSV or Excel, sorted by `supplier_id` so
    the file a person opens is stable run to run. The header written is
    `FIELDS` plus `HISTORY_FIELD` plus whichever unknown columns any
    record still carries (T5 review, H5, see `_all_fieldnames`) -- never
    only `FIELDS`, which is what used to delete a customer's own columns
    the moment anything here wrote the file back."""
    ext = os.path.splitext(path)[1].lower()
    rows = [_record_to_row(master[sid]) for sid in sorted(master)]
    fieldnames = _all_fieldnames(rows)
    if ext == ".csv":
        _write_csv(rows, path, fieldnames)
    elif ext in (".xlsx", ".xlsm"):
        _write_xlsx(rows, path, fieldnames)
    else:
        raise ToolkitError(("path", f"unsupported supplier master file type '{ext}'; "
                                    "use .csv or .xlsx"))


def match(name, master):
    """`(supplier_id, reason)` for an exact or near-exact match of `name`
    against `master`, or `(None, reason)` -- see the module docstring for
    why this never falls back to a similarity score. Checked in order:
    case-folded exact name, a known alias, then a normalised form
    (punctuation and a legal suffix stripped) of either. A level that
    matches more than one supplier is ambiguous and reported as such,
    rather than picking one -- an ambiguous exact match is still not a
    certain one."""
    name = str(name or "").strip()
    if not name:
        return None, "no supplier name was given to match"
    if not master:
        return None, "the supplier master is empty; nothing to match against"

    folded = name.casefold()
    exact = [sid for sid, rec in master.items()
             if str(rec.get("name") or "").strip().casefold() == folded]
    if len(exact) == 1:
        return exact[0], "exact match on the supplier's name"
    if len(exact) > 1:
        return None, f"{name!r} matches {len(exact)} suppliers' names exactly; ambiguous"

    alias_hits = [sid for sid, rec in master.items()
                  if folded in {a.strip().casefold() for a in (rec.get("aliases") or [])}]
    if len(alias_hits) == 1:
        return alias_hits[0], "matched a known alias"
    if len(alias_hits) > 1:
        return None, f"{name!r} matches {len(alias_hits)} suppliers' aliases; ambiguous"

    normalised = _normalise(name)
    norm_hits = set()
    if normalised:
        for sid, rec in master.items():
            candidates = [rec.get("name")] + list(rec.get("aliases") or [])
            if any(_normalise(str(cand or "")) == normalised for cand in candidates):
                norm_hits.add(sid)
    norm_hits = list(norm_hits)
    if len(norm_hits) == 1:
        return norm_hits[0], "matched once punctuation and a legal suffix were stripped"
    if len(norm_hits) > 1:
        return None, (f"{name!r} matches {len(norm_hits)} suppliers once punctuation and a "
                      "legal suffix are stripped; ambiguous")

    return None, (f"no exact, alias or normalised match for {name!r} among {len(master)} "
                  "supplier(s) in the master; ask the supplier-match task with a shortlist "
                  "of candidates")


def _resolve_supplier_id(invoice, master):
    supplier_id = str(invoice.get("supplier_id") or "").strip()
    if supplier_id:
        return supplier_id
    resolved, _reason = match(invoice.get("supplier"), master)
    return resolved


def _normalise_bank_value(value):
    return re.sub(r"\s+", "", str(value or "")).upper()


def bank_change(invoice, master):
    """`{"field", "old", "new", "supplier_id", "last_seen"}` for the first
    of `iban`, `account_number`, `bic` (in that order) where `invoice`
    disagrees with the master record it resolves to -- `invoice["supplier_id"]`
    if it carries one, otherwise `match(invoice["supplier"], master)`. `None`
    when they agree, or when the supplier could not be resolved at all: with
    no record to compare against, there is nothing to disagree with, and
    that absence is `match`'s finding to report, not this function's.

    Comparison ignores whitespace and case, so re-formatting the same IBAN
    is never reported as a change. Both `old` and `new` are always the
    literal values on each side -- see the module docstring for why."""
    supplier_id = _resolve_supplier_id(invoice, master)
    if not supplier_id or supplier_id not in master:
        return None
    record = master[supplier_id]
    for field in _BANK_FIELDS:
        new = str(invoice.get(field) or "").strip()
        old = str(record.get(field) or "").strip()
        if not new or not old:
            continue  # nothing stated on one side -- absence, not a change
        if _normalise_bank_value(new) != _normalise_bank_value(old):
            return {"field": field, "old": old, "new": new, "supplier_id": supplier_id,
                    "last_seen": record.get("last_seen")}
    return None


def _invoice_seen_date(invoice):
    value = invoice.get("invoice_date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    return date.today()


def _new_supplier_id(master, name):
    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "supplier"
    candidate = base
    n = 2
    while candidate in master:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def remember(invoice, master):
    """`master` with this invoice's supplier added (if unseen) or advanced
    (if seen) -- see the module docstring for exactly what "advanced" does
    and, just as importantly, does not touch. Returns a new dict; `master`
    itself is left unmodified."""
    name = str(invoice.get("supplier") or "").strip()
    updated = dict(master)
    seen_date = _invoice_seen_date(invoice)
    supplier_id = _resolve_supplier_id(invoice, master)

    if supplier_id and supplier_id in master:
        record = dict(master[supplier_id])
        record["last_seen"] = seen_date
        record["invoice_count"] = int(record.get("invoice_count") or 0) + 1
        aliases = list(record.get("aliases") or [])
        canonical = str(record.get("name") or "").strip().casefold()
        if name and name.casefold() != canonical and not any(
                a.strip().casefold() == name.casefold() for a in aliases):
            aliases = aliases + [name]
        record["aliases"] = aliases
        # Learned only here -- a supplier `remember` already recognised --
        # never in the new-record branch below. See the module docstring.
        sender_email = str(invoice.get("sender_email") or "").strip()
        if "@" in sender_email:
            record["domain"] = sender_email.rsplit("@", 1)[-1].strip().lower()
        updated[supplier_id] = record
        return updated

    new_id = _new_supplier_id(master, name)
    updated[new_id] = {
        "supplier_id": new_id,
        "name": name,
        "aliases": [],
        "country": str(invoice.get("country") or "").strip(),
        "iban": str(invoice.get("iban") or "").strip(),
        "account_number": str(invoice.get("account_number") or "").strip(),
        "bic": str(invoice.get("bic") or "").strip(),
        "vat_number": str(invoice.get("vat_number") or "").strip(),
        "domain": "",  # never learned from a brand-new record -- see the module docstring
        "first_seen": seen_date,
        "last_seen": seen_date,
        "invoice_count": 1,
        HISTORY_FIELD: [],  # nothing to record yet for a supplier just added
    }
    return updated


def apply_approved_change(master, change, *, approved_by, at=None):
    """`master` with a bank-detail change written in, because a person
    approved it. The only route by which bank details on a known supplier
    ever move.

    `remember` deliberately refuses to touch them: if an invoice could
    quietly overwrite the record it is checked against, the check would
    launder the very swap it exists to catch. But refusing everywhere leaves
    the opposite hole, and it is not a smaller one -- a supplier that really
    has changed bank would be flagged on every run for ever, the reviewer
    would accept it every time, and within a month they would be clicking
    past the flag without reading it. A control people learn to dismiss is
    worse than no control, because it still costs them time and now buys
    nothing.

    So the change lands here, and only from an approval: `change` is what
    `bank_change` reported, `approved_by` is the person who accepted it at
    sign-off, and the previous value is kept beside the new one rather than
    being erased. Nothing in this module calls it -- the sign-off flow does,
    once the exception it belongs to has been dispositioned as accepted.
    """
    record = (master or {}).get((change or {}).get("supplier_id"))
    if record is None:
        raise ToolkitError(("supplier_id",
                            f"no supplier {(change or {}).get('supplier_id')!r} in the master, "
                            "so there is no record to apply an approved change to"))
    field = change.get("field")
    if field not in ("iban", "account_number", "bic"):
        raise ToolkitError(("field", f"{field!r} is not a bank detail this may change"))
    if not str(approved_by or "").strip():
        raise ToolkitError(("approved_by",
                            "a bank-detail change is only ever applied because a named person "
                            "approved it"))
    when = (at or date.today()).isoformat() if not isinstance(at, str) else at
    history = list(record.get(HISTORY_FIELD) or [])
    history.append({"field": field, "old": change.get("old"), "new": change.get("new"),
                    "approved_by": approved_by, "approved_at": when})
    updated = dict(master)
    updated[record["supplier_id"]] = dict(record, **{field: change.get("new"),
                                                     HISTORY_FIELD: history})
    return updated


def revert_approved_change(master, change, *, reverted_by, at=None):
    """`master` with a previously-approved bank-detail change reverted --
    the field restored to `change["old"]`, and a REVERSAL entry appended to
    `bank_detail_changes`, never a deletion of the acceptance that preceded
    it (N3, second review -- .superpowers/sdd/2026-09-21-payments-fraud/
    second-review-findings.md).

    Before this existed, accepting a `changed_bank_details` exception wrote
    the fraud's IBAN into the master via `apply_approved_change`; if the
    reviewer changed their mind and rejected the very same exception
    afterwards, the *payment* was correctly dropped (`cfo.payments.cli.
    cmd_build`'s own filter) but the master kept the fraud IBAN, live, with
    the reviewer's own name now attached to it in `bank_detail_changes` --
    and `changed_bank_details` compares an invoice against exactly that
    field, so the tool's only critical bank-redirection control was
    permanently disarmed for precisely the account the reviewer had just
    refused.

    Called only from `cfo.payments.cli.cmd_disposition`, and only when the
    exception being rejected previously applied a change (see that
    module's `_revert_if_bank_change`, the mirror of `_apply_if_bank_change`
    for the opposite direction) -- never for a change that was never
    applied in the first place, which this function has no way to tell
    apart from "already reverted" and so is the caller's own job to check
    first."""
    record = (master or {}).get((change or {}).get("supplier_id"))
    if record is None:
        raise ToolkitError(("supplier_id",
                            f"no supplier {(change or {}).get('supplier_id')!r} in the master, "
                            "so there is no record to revert a change on"))
    field = change.get("field")
    if field not in ("iban", "account_number", "bic"):
        raise ToolkitError(("field", f"{field!r} is not a bank detail this may change"))
    if not str(reverted_by or "").strip():
        raise ToolkitError(("reverted_by",
                            "a bank-detail change is only ever reverted because a named person "
                            "rejected it"))
    when = (at or date.today()).isoformat() if not isinstance(at, str) else at
    history = list(record.get(HISTORY_FIELD) or [])
    # The reversal is its own history entry, in the same shape
    # `apply_approved_change` writes (so a reader of `bank_detail_changes`
    # sees one consistent record shape throughout), with `old`/`new`
    # reversed to describe what actually happened here -- the field moving
    # from the fraud value back to the genuine one -- and `action` naming
    # this entry as a reversal so it is never mistaken for a second,
    # independent approval of the same change.
    history.append({"field": field, "old": change.get("new"), "new": change.get("old"),
                    "approved_by": reverted_by, "approved_at": when, "action": "reverted"})
    updated = dict(master)
    updated[record["supplier_id"]] = dict(record, **{field: change.get("old"),
                                                     HISTORY_FIELD: history})
    return updated
