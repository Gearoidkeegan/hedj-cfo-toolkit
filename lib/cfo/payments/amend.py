"""Task 1 of the payments: staged-output milestone -- the amendment store
and the instructed-lines digest, with no CLI attached yet. See
`.superpowers/sdd/2026-09-22-payments-staged-output/task-1-brief.md` and
`docs/plans/2026-09-22-payments-staged-output.md` for the contract this
module answers to; `docs/specs/2026-09-22-payments-staged-output-design.md`
for why it exists at all.

Modelled on `cfo.payments.signoff`'s own shape: state lives at
`<run_dir>/payments/amendments.json`, written through `cfo.io.
save_via_temp`'s equivalent (a locked read-modify-write via `cfo.io.
locked` and `cfo.io.write_json_atomic`), append-only. A changed mind is a
second entry, never a correction of the first. `latest` (below) applies
last-entry-of-any-kind-wins per reference, the same rule
`_rejected_exception_ids` below (moved unchanged from `cli.py`) already
applies to `signoff.json`'s dispositions -- but `excluded_references`
does NOT use `latest` any more; see the note below on why that analogy
broke.

**Task 3's own fix, ruled by the controller -- exclusion is its own
axis.** `excluded_references` used to read `latest`'s last-entry-of-
any-kind per reference, on the theory that it was the same rule
`_rejected_exception_ids` already applies to dispositions. That analogy
is false: a disposition is a repeated answer to one yes/no question
("is this exception acceptable?"), so last-one-wins is exactly right
there. Amendments are not one question -- `exclude`/`restore` answer
*whether* to pay, while `value-date` and `remittance` answer *how*.
Collapsing them onto one axis meant a `remittance` typo fix recorded
after an `exclude` silently un-excluded the payment: the accept-all/
reject-all defect (P1.2) in a new costume. `excluded_references` now
tracks its own axis, independent of every other kind of amendment (see
its own docstring) -- `restore` is the *only* thing that un-excludes.

**Global constraint 1, "one exclusion path, two sources":**
`instructed_batches` is `cli.py`'s own `_filter_rejected_payments`, moved
here unchanged except for the one line the brief calls for: its
`dropped_refs` now unions the rejected exceptions' payment references
with `excluded_references`. Every guard the original carried -- the two
independent total recomputations, the refusal when a reference either
source believed it had dropped cannot actually be found -- survives
verbatim and now covers both sources. `lines_digest` is built on top of
this same function, not a second pass over `batches.json`, for the same
reason: two places deciding what reaches a bank is how a rejected payment
got paid once already.

**Global constraint 2:** nothing in this module ever writes a
beneficiary, a bank detail or an amount. `record` stores whatever
`change`/`from_value`/`to_value` its caller gives it -- refusing an
amendment that would touch one of those three is `cmd_amend`'s job (a
later task), not this module's. This module only ever moves a payment out
of a batch, or records a value date or a remittance string against a
reference already in one.
"""
import dataclasses
import json
import os
from decimal import Decimal

from cfo.console import ToolkitError
from cfo.io import locked, now_iso, read_json, sha256_bytes, write_json_atomic
from cfo.payments import signoff

AMENDMENTS_NAME = "amendments"

# The two `change` values this module itself gives meaning to -- see
# `excluded_references`. `value-date` and `remittance` (the other two the
# plan's own JSON shape names) are opaque to this module; it stores them
# and hands them back, nothing more.
EXCLUDE = "exclude"
RESTORE = "restore"

# The `change` value for a value-date amendment (Task 4), and the three
# `resolution` ids the batch-boundary decision offers -- `cmd_amend`'s own
# vocabulary, named here once so `cli.py` and its tests share one spelling
# rather than three independently-typed copies. Opaque to this module in
# exactly the same sense as `EXCLUDE`/`RESTORE` above: `record` does not
# validate `resolution` and never inspects it.
VALUE_DATE = "value-date"
RESOLUTION_KEEP_IN_BATCH = "keep-in-batch"
RESOLUTION_OWN_BATCH = "own-batch"
RESOLUTION_HOLD_BACK = "hold-back"

# Quoted verbatim from `signoff.disposition` -- see the brief: "Refuses a
# blank reason or a blank `by`, in the same words `signoff.disposition`
# already uses." Two independently-typed copies of the same refusal drift;
# importing the module and never re-typing the sentence is not an option
# here (the words are chosen for signoff's own exception language), so
# they are copied once, here, deliberately.
_BLANK_REASON_MESSAGE = "a reason is required, and an empty or whitespace string is not one"
_BLANK_BY_MESSAGE = "the name of the person dispositioning this exception is required"

# Two ASCII control characters (unit/record separator) that used to be
# the only thing standing between two different sets of payment lines and
# the same `lines_digest` input -- nothing ever enforced that a real
# field value could not contain either one, and `beneficiary_name` (which
# comes straight off the supplier's own invoice -- the document this
# tool's own threat model treats as attacker-controlled) containing them
# collided two genuine payments onto the digest of one crafted line
# (C3/F3). `lines_digest` no longer relies on them for anything -- see
# `_length_prefixed` below -- kept here only because `tests.test_payments_
# amend`'s own regression test for that defect needs the literal
# characters to reconstruct its reproduction.
_FIELD_SEP = "\x1f"
_LINE_SEP = "\x1e"


def _length_prefixed(value):
    """Encodes one digest field as `<its own UTF-8 byte length>:<the
    bytes themselves>` -- a netstring, in effect. This is `lines_digest`'s
    actual defence against C3/F3, replacing the separator characters
    above: decoding this format (read digits up to `:`, then take exactly
    that many bytes, then repeat for the next field) is unambiguous no
    matter what those bytes contain -- including `_FIELD_SEP`/`_LINE_SEP`
    themselves, a literal colon, or another field's own length prefix --
    because the boundary is always the field's true length, never a
    character the field's own content could contain. `lines_digest` never
    decodes this; hashing the concatenation is enough, and this property
    is exactly what makes the module comment above true rather than
    merely harder to defeat."""
    data = str(value).encode("utf-8")
    return f"{len(data)}:".encode("ascii") + data


# C5/F13: prefixed to every `lines_digest` input, whether or not any
# lines follow -- see that function's own docstring on why an empty
# result must never hash the same as `sha256("")` (a version tag for
# this encoding, in case it changes again).
_DIGEST_FORMAT_TAG = "cfo.payments.amend.lines_digest/v2"


def _payments_dir(run_dir):
    """<run_dir>/payments -- created here, exactly as `cli.py`'s own
    identically-named helper does, so this module works whether or not
    `cli.py` has ever touched this run first."""
    path = os.path.join(run_dir, "payments")
    os.makedirs(path, exist_ok=True)
    return path


def _state_path(run_dir, name):
    return os.path.join(_payments_dir(run_dir), f"{name}.json")


def _read_state(run_dir, name, default=None):
    return read_json(_state_path(run_dir, name), default)


def _batch_references(run_dir):
    """Every payment reference in this run's own `batches.json`, read as
    plain JSON -- never through `cli.py`'s model deserialization, which
    this module has no business depending on (`cli.py` imports this
    module; this module must not import it back)."""
    data = _read_state(run_dir, "batches", []) or []
    return {payment["reference"] for batch in data for payment in batch.get("payments", [])}


def record(run_dir, *, reference, change, from_value, to_value, resolution, reason, by):
    """Appends one entry to `amendments.json` and returns it.

    `from_value`/`to_value` are written as the `from`/`to` keys in the
    stored entry -- the parameters differ only because `from` is a Python
    keyword. `resolution` is carried through untouched (`None` for every
    caller until a later task starts passing `keep-in-batch`/`own-batch`/
    `hold-back`); this function does not validate it.

    Refuses a blank `reason` or a blank `by`, in the same words
    `signoff.disposition` already uses. Refuses a `reference` that is not
    one of this run's own payments, checked against `batches.json`
    directly (see `_batch_references`) -- an amendment against a
    reference that was never actually built into a payment cannot mean
    anything.
    """
    reason_s = str(reason or "").strip()
    if not reason_s:
        raise ToolkitError(("reason", _BLANK_REASON_MESSAGE))
    by_s = str(by or "").strip()
    if not by_s:
        raise ToolkitError(("by", _BLANK_BY_MESSAGE))

    reference_s = str(reference or "").strip()
    known_refs = _batch_references(run_dir)
    if reference_s not in known_refs:
        raise ToolkitError(("reference", f"{reference_s!r} is not a payment reference in this "
                            "run's own batches.json -- run `payments review` first, or check the "
                            "reference against this run's own outstanding payments"))

    entry = {"reference": reference_s, "change": change, "from": from_value, "to": to_value,
             "resolution": resolution, "reason": reason_s, "by": by_s, "at": now_iso()}

    path = _state_path(run_dir, AMENDMENTS_NAME)
    with locked(path):
        entries = read_json(path, []) or []
        entries = list(entries)
        entries.append(entry)
        write_json_atomic(path, entries)
    return dict(entry)


def amendments(run_dir):
    """Every amendment entry ever recorded for this run, in the order
    `record` appended them."""
    return list(_read_state(run_dir, AMENDMENTS_NAME, []) or [])


def latest(run_dir):
    """`{reference: entry}` -- the decision that stands per reference: the
    *last* entry `amendments` holds for it, whichever kind of change it
    was. The same last-one-wins rule `_rejected_exception_ids` already
    applies to `signoff.json`'s dispositions.

    **Not what `excluded_references` uses any more** (Task 3's own fix):
    a reference's *latest entry of any kind* is the wrong question for
    exclusion specifically -- see that function's own docstring. This
    stays a general per-reference "what does the record currently say"
    lookup, for whatever future amendment kind genuinely is a single
    yes/no answer the way a disposition is."""
    result = {}
    for entry in amendments(run_dir):
        result[entry["reference"]] = entry
    return result


def excluded_references(run_dir):
    """The currently-excluded references -- exclusion's own axis,
    independent of `latest`'s last-entry-of-any-kind. A reference is
    excluded when the latest entry *whose `change` is `exclude` or
    `restore`* is an `exclude`; every other kind of amendment (a
    value-date change, a remittance typo fix) is invisible to this axis
    and leaves exclusion exactly as it was.

    Walking `amendments` in order and toggling a set on `exclude`/
    `restore` (ignoring every other `change`) gives last-one-wins on
    this axis alone, for free -- no separate "latest exclusion-or-
    restore entry per reference" lookup needed. `restore` is the only
    thing that removes a reference from this set; nothing else -- not a
    disposition, not any other kind of amendment -- can undo an
    exclusion once recorded (Task 1's own defect, fixed here: see the
    module docstring)."""
    excluded = set()
    for entry in amendments(run_dir):
        if entry["change"] == EXCLUDE:
            excluded.add(entry["reference"])
        elif entry["change"] == RESTORE:
            excluded.discard(entry["reference"])
    return excluded


def _rejected_exception_ids(run_dir):
    """Moved unchanged from `cli.py`. Every exception id whose *latest*
    disposition is a rejection. `signoff.disposition`'s own docstring:
    calling it again for an exception already dispositioned is allowed --
    a changed mind is a second entry, appended, never overwritten -- so
    the last entry recorded for an id is the decision that actually
    stands. There is no public getter in `cfo.payments.signoff` for every
    disposition at once (only `outstanding`, which reports exactly the
    opposite: the ones with *no* disposition at all), so this reads
    `signoff.json` directly."""
    state = read_json(os.path.join(run_dir, signoff.STATE_FILENAME))
    if not state:
        return set()
    latest_decision = {}
    for entry in state.get("dispositions", []):
        latest_decision[entry["exception_id"]] = entry["accepted"]
    return {exception_id for exception_id, accepted in latest_decision.items() if not accepted}


def instructed_batches(run_dir, batches):
    """`(batches, dropped_refs)` -- `batches`, with every payment either a
    rejected exception or an amendment exclusion named removed.

    This is `cli.py`'s own `_filter_rejected_payments` -- **B1** --, moved
    here unchanged except for one line: `dropped_refs` is now the union of
    the rejected exceptions' own payment references and
    `excluded_references`, not the first alone (see the module docstring's
    note on global constraint 1).

    `exception-meta.json`'s own `payment_references` (a list, set by
    `cmd_review` from every invoice the finding actually named, via
    `fraud.Flag.content_doc_ids`) is the link for the rejected-exception
    side, rather than re-matching a `where` string against a payment's own
    `reference` -- the two are usually equal but are not the same field,
    and are not guaranteed to agree. **D7:** a finding that reasons across
    several invoices at once (`split_to_stay_under`,
    `near_duplicate_invoice`, `sequential_invoice_numbers`, and
    `lookalike_sender_domain`'s own batch-pair comparison) names every one
    of them here, not only the one `where` happens to print -- rejecting
    it drops every payment it named, or the fix is the partial version of
    B1's own defect again, wearing a hat: it looks like it worked, because
    *one* payment does disappear. An entry with no `payment_references`
    key at all (an older or hand-built `exception-meta.json`) falls back
    to its own singular `payment_reference`, wrapped into a one-element
    list -- never a second, independently-set value that could disagree
    with it.

    **B6**, unchanged: recomputes the surviving total two independent
    ways -- live, fresh from `Batch.control_total` (a property, summed
    only from whatever payments are actually left after filtering), and by
    subtracting exactly what this function itself removed from the
    batches' own original total -- and refuses to return anything at all
    if the two disagree, or if a reference either source believed it had
    dropped cannot actually be found in the batches given to it. A
    filtered payment file whose own header disagreed with itself would be
    worse than the bug this exists to fix: a bank would reject it, and
    nobody would know why.

    **C4/F9:** these are two different failures with two different
    messages -- a reference nobody could find (naming it), and a total
    that genuinely disagrees with itself (quoting two different numbers).
    They used to share one message that, on the reference-not-found path,
    quoted the same number twice and named nothing: see the comment at
    the two raises below for why that pairing was never actually two
    genuinely different numbers on that path.
    """
    rejected_ids = _rejected_exception_ids(run_dir)
    rejected_refs = set()
    if rejected_ids:
        meta = _read_state(run_dir, "exception-meta", {}) or {}
        for exception_id in rejected_ids:
            item = meta.get(exception_id) or {}
            refs = item.get("payment_references")
            if refs is None:
                # No `payment_references` list at all -- an older or
                # hand-built exception-meta.json -- falls back to the
                # singular `payment_reference`, wrapped into a
                # one-element list; never a second source that could
                # disagree with a `payment_references` that IS present.
                single = item.get("payment_reference")
                refs = [single] if single else []
            rejected_refs.update(ref for ref in refs if ref)
    dropped_refs = rejected_refs | excluded_references(run_dir)
    if not dropped_refs:
        return list(batches), set()

    original_total = sum((batch.control_total for batch in batches), Decimal("0"))
    filtered, dropped_total, found_refs = [], Decimal("0"), set()
    for batch in batches:
        kept = []
        for payment in batch.payments:
            if payment.reference in dropped_refs:
                dropped_total += payment.amount
                found_refs.add(payment.reference)
                continue
            kept.append(payment)
        filtered.append(dataclasses.replace(batch, payments=kept))
    filtered = [batch for batch in filtered if batch.payments]  # an emptied batch ships nothing

    # C4/F9: these used to be one check sharing one message. `found_refs`
    # is always a subset of `dropped_refs` (it only ever gains a
    # reference `instructed_batches` actually found and removed), so
    # `found_refs != dropped_refs` and `missing_refs` below are the same
    # condition -- and whenever it fires, `live_total` and
    # `expected_total` are already equal (nothing was removed for a
    # reference nobody could find, so neither total moved on its
    # account), which is exactly why the old combined message quoted the
    # same number twice and named no reference. Split so each failure
    # gets its own message, and the totals message is only ever reached
    # when the two numbers it quotes have actually diverged.
    missing_refs = dropped_refs - found_refs
    if missing_refs:
        raise ToolkitError(("payments", f"{len(missing_refs)} reference(s) this run believed "
                            f"had been dropped (a rejected exception or an amendment exclusion) "
                            f"could not be found in batches.json: {sorted(missing_refs)} -- "
                            "refusing to emit a payment file that could disagree with what was "
                            "actually reviewed"))

    live_total = sum((batch.control_total for batch in filtered), Decimal("0"))
    expected_total = original_total - dropped_total
    if live_total != expected_total:
        raise ToolkitError(("payments", f"the payment total after removing rejected or excluded "
                            f"payments ({live_total}) does not match the total this run "
                            f"recomputed independently ({expected_total}) -- refusing to emit a "
                            "payment file that could disagree with what was actually reviewed"))
    return filtered, dropped_refs


def lines_digest(run_dir, batches):
    """sha256 over the *instructed* payment lines -- after both exclusion
    sources have been applied by `instructed_batches` -- one canonical
    line per payment, in batch order. This is the answer to "is the thing
    being signed off the thing that was shown?" (constraint 3, Task 2), so
    it has to cover every field that could change what actually gets
    paid, to whom, or when -- not only the six
    `docs/plans/2026-09-22-payments-staged-output.md` originally named
    (reference, beneficiary, currency, amount, value date, batch
    reference).

    **Task 4, controller findings F2/F4:** two gaps in the original six,
    both closed here rather than worked around, because both were things
    a batch can change without the digest noticing:

    - **F4.** The batch's own `execution_date` -- the day the bank
      actually pays -- was not a field. `own-batch` moves a payment into
      a batch dated differently with nothing else about the payment
      changing, which was invisible to this digest and so to sign-off's
      own staleness check.
    - **F2.** Every field that says *where the money goes* was
      unguarded: `iban`, `account_number`, `bic`, `bank_code`,
      `beneficiary_country`, the beneficiary's address (both
      `beneficiary_address_flat` and the structured
      `beneficiary_address`), and the batch's own `debtor_account` and
      `sell_currency`. Between this digest and `cli.
      _assert_batches_match_signoff` (batch count, payment count, control
      total only), swapping a creditor's IBAN in `batches.json` after a
      draft passed sign-off reached the emitted pain.001 with a passing
      control -- editing the amount was already caught, editing the
      account was not.

    `beneficiary_address` (a dict) is serialised via `json.dumps(...,
    sort_keys=True)` -- deterministic regardless of key insertion order,
    and every key/value in it is a plain string (see `cfo.payments.
    model.Payment`'s own docstring on the shape), so this never needs to
    handle anything `json.dumps` can't render exactly the same way twice.

    Stable across runs for the same content: rebuilding the same batches
    from disk (`Decimal`/`date` round-tripped through JSON and back)
    must not change it, because `str(Decimal(...))`, `date.isoformat()`
    and `json.dumps(..., sort_keys=True)` are exactly as stable as the
    values themselves. Changes when any one of these fields changes on
    any line -- including a line dropping out of the instructed set
    altogether, since `instructed_batches` is called first, every time.

    **Why a batch's own `external_reference` must never collide with
    another batch's.** Each line carries its own batch's
    `external_reference`, but the digest adds no separate batch-boundary
    marker of its own -- so two *different* batches sharing one
    `external_reference` would make "one batch of two payments" and "two
    batches of one payment each" render to the exact same digest. Every
    batch `cfo.payments.batch.build_batches` produces is already unique
    (it numbers them itself); `cfo.payments.batch.next_external_reference`
    exists so a batch assembled by hand outside that numbering (`own-batch`,
    in `cli.py`) keeps that same guarantee -- see its own tests.

    **C3/F3: every field is length-prefixed (`_length_prefixed`), not
    separator-joined.** The comment that used to sit above `_FIELD_SEP`/
    `_LINE_SEP` claimed no real field value could ever collide two
    different sets of lines onto one digest input -- nothing enforced
    that, and a `beneficiary_name` (supplier-controlled, in this tool's
    own threat model) containing those separator characters did exactly
    that. Length-prefixing every field removes the separator characters'
    role entirely: the byte length prefixed to each field is the one
    thing no field's own content can ever forge, so no combination of
    field values -- whatever they contain -- can shift a boundary. Every
    payment always contributes the same sixteen fields, in the same
    order, with no separator needed between them or between lines: the
    fixed field count per line is enough for the concatenation of every
    line's fields to be unambiguous on its own.

    **C5/F13: a header precedes the lines, so an empty result never
    hashes as `sha256("")`.** With every payment excluded, `instructed`
    below is `[]` -- no lines at all -- and hashing nothing but an empty
    join made every such run indistinguishable from every other one, and
    from a run with no batches at all. `_DIGEST_FORMAT_TAG` (a version
    tag, so a later change to this encoding cannot silently collide with
    digests computed under this one), the *original* payment count
    (before `instructed_batches` drops anything -- zero only when there
    truly were no batches), and the sorted, dropped references
    themselves (not just how many) are always present, even when no
    lines follow -- so "every payment excluded" and "no batches at all"
    digest differently from each other, from `sha256("")`, and from any
    other fully-excluded run that dropped a different set of references.
    """
    instructed, dropped_refs = instructed_batches(run_dir, batches)
    original_count = sum(batch.count for batch in batches)
    chunks = [_length_prefixed(_DIGEST_FORMAT_TAG), _length_prefixed(str(original_count))]
    for reference in sorted(dropped_refs):
        chunks.append(_length_prefixed(reference))
    for batch in instructed:
        for payment in batch.payments:
            value_date = payment.value_date.isoformat() if payment.value_date else ""
            address = json.dumps(payment.beneficiary_address or {}, sort_keys=True)
            for field in (
                payment.reference, payment.beneficiary_name, payment.beneficiary_country,
                payment.beneficiary_address_flat, address, payment.iban,
                payment.account_number, payment.bic, payment.bank_code,
                payment.currency, str(payment.amount), value_date,
                batch.external_reference, batch.execution_date.isoformat(),
                batch.debtor_account, batch.sell_currency,
            ):
                chunks.append(_length_prefixed(field))
    return sha256_bytes(b"".join(chunks))
