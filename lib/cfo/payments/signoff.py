"""T11: sign-off, the control block, and the audit trail.

The deliberate friction before money moves. Every other module in this
toolkit informs a decision about a batch of invoices; this module is where
that decision is recorded.

ATTESTATION_STATEMENT below is the exact claim this tool makes about what
sign-off is. Quote it verbatim wherever the README, the skill or a report
needs to say what sign-off means -- don't re-type it, because two
independently-typed copies drift, and the wording matters:

    The tool cannot authenticate anyone. It records an attestation -- that a
    named person said they reviewed this batch at this time. That is an audit
    trail and a moment of deliberate friction before money moves. It is not
    dual authorisation, which belongs in the banking channel.

Overstating this -- implying a signature authenticates the signer, or that
recording one is a second, independent authorisation -- would give a false
sense of control, which is worse than no control at all.

**No payment artefact exists on disk before sign-off.** This module does not
write payment files itself (`cfo.payments.emit` does, downstream), but
`is_signed_off` is the single source of truth every emitter and the workbook
must gate on: before it returns True, an artefact carries `NOT-REVIEWED` in
its filename and a banner as its first row or element, never something that
could be mistaken for the real thing.

**Exceptions are dispositioned one at a time.** `disposition` takes exactly
one `exception_id`. There is no function anywhere in this module -- no loop
helper, no convenience list argument -- that records a decision against more
than one exception in a single call. `tests/test_payments_signoff.py`
inspects this module's public callables and fails if that ever stops being
true. A single "approve all" button is how a control becomes theatre; the one
thing this tool sells is that somebody actually looked at each one.

State lives at `<run_dir>/signoff.json`, written through `cfo.io.save_via_temp`
under `cfo.io.locked` so a crash mid-write, or two commands racing on the same
run, cannot corrupt an audit trail that a person's payment decision depends
on. Every disposition is appended, never overwritten -- a changed mind is a
second entry, and the sequence of decisions survives intact.

`control_block`'s `source_files` come from the run's own `run.json["inputs"]`
record (written by `cfo.extract`, one entry per file the run has actually
touched, each already carrying its own SHA-256) -- read here, never
recomputed, so the control block stamped onto an output can never disagree
with the run's own record of what it processed.
"""
import getpass
import json
import os
from decimal import Decimal

from cfo import runs
from cfo.console import ToolkitError
from cfo.io import locked, now_iso, read_json, save_via_temp

STATE_FILENAME = "signoff.json"

# Quoted verbatim in the module docstring above -- see there for why this is
# a named constant rather than text repeated (and able to drift) wherever the
# claim is made.
ATTESTATION_STATEMENT = (
    "The tool cannot authenticate anyone. It records an attestation -- that a "
    "named person said they reviewed this batch at this time. That is an "
    "audit trail and a moment of deliberate friction before money moves. It "
    "is not dual authorisation, which belongs in the banking channel."
)

_NO_REVIEW_MESSAGE = "no review has been started for this run -- call start_review first"


def _state_path(run_dir):
    return os.path.join(run_dir, STATE_FILENAME)


def _dump(tmp_path, data):
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _update(run_dir, fn, *, must_exist):
    """Locked read-modify-write of the state file (mirrors cfo.runs's I2
    pattern): reads the current state (or None), calls `fn(state)` for the
    new value, and writes it back via save_via_temp -- all while holding the
    lock, so a concurrent call on the same run can't interleave with this
    one and split an audit entry across two writes."""
    path = _state_path(run_dir)
    with locked(path):
        state = read_json(path)
        if must_exist and state is None:
            raise ToolkitError(("signoff", _NO_REVIEW_MESSAGE))
        new_state = fn(state)
        save_via_temp(path, lambda tmp: _dump(tmp, new_state))
        return new_state


def _read_required(run_dir):
    path = _state_path(run_dir)
    with locked(path):
        state = read_json(path)
    if state is None:
        raise ToolkitError(("signoff", _NO_REVIEW_MESSAGE))
    return state


def _outstanding_from_state(state):
    dispositioned = {entry["exception_id"] for entry in state["dispositions"]}
    return [dict(exc) for exc in state["exceptions"] if exc["id"] not in dispositioned]


def _normalize_exceptions(exceptions):
    """Every exception is a `(where, message)` pair, the same shape every
    finding and warning in this toolkit uses -- see cfo.payments.validate.
    Assigns each a stable, sequential string id in the order given; that id,
    not the pair itself, is what `disposition` addresses one at a time."""
    normalized = []
    for index, item in enumerate(exceptions):
        if not (isinstance(item, (tuple, list)) and len(item) == 2):
            raise ToolkitError(("exceptions", f"exceptions[{index}] must be a (where, message) "
                                              f"pair, not {item!r}"))
        where, message = item
        message_s = str(message).strip()
        if not message_s:
            raise ToolkitError(("exceptions", f"exceptions[{index}] has a blank message"))
        normalized.append({"id": str(index + 1), "where": str(where).strip(), "message": message_s})
    return normalized


def _batches_summary(batches):
    """(batch_count, payment_count, control_total) from whatever was handed
    to start_review -- anything exposing `.count` and `.control_total`, the
    same properties `cfo.payments.model.Batch` defines, without importing
    that module or `cfo.payments.batch` (built in parallel on a different
    file) to get them."""
    batches = list(batches)
    payment_count = sum(batch.count for batch in batches)
    control_total = sum((batch.control_total for batch in batches), Decimal("0"))
    return len(batches), payment_count, control_total


# Defect F1 (Summary sheet review, 2026-09-23), small item: `prepared_by`
# below is `getpass.getuser()` -- a best-effort operational detail, never
# an authentication claim (see the module docstring). `control_block`
# keeps handing that raw value out unchanged (every existing reader of
# `block["prepared_by"]` must keep getting exactly that); it is the two
# places a third party actually reads it -- the workbook's Sign-off
# sheet, the fraud report -- that call `prepared_by_label` at render
# time, so an operating-system account name is never printed as though
# it were a person, directly above a real reviewer's real name.
PREPARED_BY_QUALIFIER = "the operating-system account that ran this tool, not a person"


def prepared_by_label(prepared_by):
    """`prepared_by`, annotated with `PREPARED_BY_QUALIFIER` so a reader
    can never mistake it for a person. Blank stays blank -- `start_review`
    records "" when the platform can't report a username at all (its own
    except branch below), and there is nothing to annotate around
    nothing."""
    prepared_by = str(prepared_by or "").strip()
    if not prepared_by:
        return ""
    return f"{prepared_by} ({PREPARED_BY_QUALIFIER})"


def start_review(run_dir, batches, exceptions):
    """Opens the review for this run: records the batch shape (payment
    count, control total, batch count) and the exceptions that need a human
    decision before sign-off is possible. Returns the exceptions with their
    assigned ids, so a caller (the CLI) can hand each one to `disposition`.

    Raises if a review has already been started for this run -- re-running
    it would either lose the audit trail already recorded, or silently
    reset it, and a corrupted or reset audit trail is worse than refusing to
    start a second one. Call `disposition`, `outstanding` and `sign_off`
    against the review already open instead.
    """
    runs.load_run(run_dir)  # fail fast if run_dir is not a valid run folder
    normalized = _normalize_exceptions(exceptions)
    batch_count, payment_count, control_total = _batches_summary(batches)
    try:
        prepared_by = getpass.getuser()
    except Exception:
        # Best-effort only: this is an operational detail (who ran the
        # tool), never an authentication claim -- see the module docstring.
        # A platform where the username can't be read must not stop a run.
        prepared_by = ""
    prepared_at = now_iso()

    def _init(state):
        if state is not None:
            raise ToolkitError(("start_review", "a review has already been started for this "
                                                "run -- use disposition/outstanding/sign_off, "
                                                "not start_review again"))
        return {
            "prepared_by": prepared_by,
            "prepared_at": prepared_at,
            "batch_count": batch_count,
            "payment_count": payment_count,
            "control_total": str(control_total),
            "exceptions": normalized,
            "dispositions": [],
            "sign_off": None,
        }

    _update(run_dir, _init, must_exist=False)
    return {
        "prepared_by": prepared_by, "prepared_at": prepared_at, "batch_count": batch_count,
        "payment_count": payment_count, "control_total": control_total,
        "exceptions": normalized,
    }


def disposition(run_dir, exception_id, *, accepted, reason, by):
    """Records one decision against one exception: accepted or rejected,
    with a reason and the person who made it. There is deliberately no way
    to pass more than one `exception_id` here -- see the module docstring.

    Appends to the audit trail; never overwrites. Calling this again for an
    exception already dispositioned is allowed -- a changed mind is a
    second entry, not a correction of the first -- and both survive, in
    order.
    """
    if not isinstance(accepted, bool):
        raise ToolkitError(("accepted", f"must be True or False, not {accepted!r}"))
    reason_s = str(reason or "").strip()
    if not reason_s:
        raise ToolkitError(("reason", "a reason is required, and an empty or whitespace "
                                      "string is not one"))
    by_s = str(by or "").strip()
    if not by_s:
        raise ToolkitError(("by", "the name of the person dispositioning this exception is "
                                  "required"))
    exception_id_s = str(exception_id)

    def _append(state):
        known_ids = {exc["id"] for exc in state["exceptions"]}
        if exception_id_s not in known_ids:
            raise ToolkitError((exception_id_s, "is not a known exception for this run"))
        entry = {"exception_id": exception_id_s, "accepted": accepted, "reason": reason_s,
                 "by": by_s, "at": now_iso()}
        state["dispositions"].append(entry)
        return state

    new_state = _update(run_dir, _append, must_exist=True)
    return dict(new_state["dispositions"][-1])


def outstanding(run_dir):
    """The exceptions -- `{"id", "where", "message"}` -- that have no
    disposition recorded against them yet, in the order start_review was
    given them. A rejected exception is not outstanding: it has been looked
    at and decided, even though it won't become a payment line."""
    return _outstanding_from_state(_read_required(run_dir))


def sign_off(run_dir, *, reviewed_by, at=None):
    """Refuses while any exception is outstanding, naming every one of them
    in the raised ToolkitError. Otherwise records who reviewed the batch and
    when, once -- a run that is already signed off refuses a second one
    rather than overwriting the first reviewer's record."""
    reviewed_by_s = str(reviewed_by or "").strip()
    if not reviewed_by_s:
        raise ToolkitError(("reviewed_by", "the reviewer's name is required"))
    at_s = at or now_iso()

    def _finish(state):
        if state.get("sign_off") is not None:
            existing = state["sign_off"]
            raise ToolkitError(("sign_off", f"already signed off by {existing['reviewed_by']} "
                                            f"at {existing['reviewed_at']}"))
        remaining = _outstanding_from_state(state)
        if remaining:
            raise ToolkitError([(exc["id"], f"outstanding, not yet dispositioned "
                                            f"({exc['where']}: {exc['message']})")
                                for exc in remaining])
        state["sign_off"] = {"reviewed_by": reviewed_by_s, "reviewed_at": at_s}
        return state

    new_state = _update(run_dir, _finish, must_exist=True)
    return dict(new_state["sign_off"])


def restate(run_dir, batches, *, reason, by):
    """Rewrites this run's own recorded batch shape -- batch count, payment
    count and control total -- to match `batches`, and appends an audit
    entry to `signoff.json` naming what changed and why.

    Exists for exactly one reason (Task 4 of the payments: staged-output
    milestone): `payments amend --value-date --resolve own-batch` changes
    the *number* of batches, which makes `cli._assert_batches_match_
    signoff`'s own guard start refusing every later build -- correctly,
    since the run's recorded shape no longer matches what is actually on
    disk. This is the one place that record is allowed to move.

    **Refuses once this run is signed off.** Restating the recorded shape
    after sign-off would let the record of what a reviewer actually
    attested to be rewritten after the fact -- turning the attestation
    into a lie. A `restate` that still worked post-sign-off would be a
    worse defect than the batch-boundary bug this task exists to fix.

    Called by `cmd_amend` (`payments amend --value-date --resolve
    own-batch`) and by nothing else -- there is no other legitimate reason
    for a run's reviewed shape to move once `start_review` has recorded
    it.
    """
    reason_s = str(reason or "").strip()
    if not reason_s:
        raise ToolkitError(("reason", "a reason is required, and an empty or whitespace "
                                      "string is not one"))
    by_s = str(by or "").strip()
    if not by_s:
        raise ToolkitError(("by", "the name of the person restating this run's batch shape is "
                                  "required"))
    batch_count, payment_count, control_total = _batches_summary(batches)

    def _restate(state):
        if state.get("sign_off") is not None:
            raise ToolkitError(("restate", "this run is already signed off -- restating the "
                                "recorded batch shape now would let the record of what was "
                                "actually signed off be rewritten after the fact"))
        entry = {
            "at": now_iso(), "reason": reason_s, "by": by_s,
            "from": {"batch_count": state["batch_count"], "payment_count": state["payment_count"],
                     "control_total": state["control_total"]},
            "to": {"batch_count": batch_count, "payment_count": payment_count,
                   "control_total": str(control_total)},
        }
        state["batch_count"] = batch_count
        state["payment_count"] = payment_count
        state["control_total"] = str(control_total)
        state.setdefault("restatements", []).append(entry)
        return state

    new_state = _update(run_dir, _restate, must_exist=True)
    return dict(new_state["restatements"][-1])


def is_signed_off(run_dir):
    """False for a run with no review started at all, or one not yet signed
    off; True once sign_off has succeeded. Never raises -- this is the check
    every emitter and the workbook gate the real artefact on, and it must be
    safe to call at any point in the flow."""
    path = _state_path(run_dir)
    with locked(path):
        state = read_json(path)
    return bool(state and state.get("sign_off"))


def control_block(run_dir):
    """run_id, tool_version, prepared_by, prepared_at, reviewed_by,
    reviewed_at, source_files ([{name, sha256}]), payment_count,
    control_total, batch_count -- for stamping onto every output, including
    the NOT-REVIEWED artefacts written before sign-off (reviewed_by and
    reviewed_at are simply blank then; this never raises for that reason).

    tool_version and source_files are read live from the run's own
    run.json on every call, never cached in signoff.json -- so this can
    never disagree with the run's own record of its version or its inputs.
    """
    run_data = runs.load_run(run_dir)
    state = _read_required(run_dir)
    sign_off_entry = state.get("sign_off")
    source_files = [{"name": item["file"], "sha256": item["sha256"]} for item in run_data["inputs"]]
    return {
        "run_id": os.path.basename(os.path.abspath(run_dir)),
        "tool_version": run_data["toolkit_version"],
        "prepared_by": state["prepared_by"],
        "prepared_at": state["prepared_at"],
        "reviewed_by": sign_off_entry["reviewed_by"] if sign_off_entry else "",
        "reviewed_at": sign_off_entry["reviewed_at"] if sign_off_entry else "",
        "source_files": source_files,
        "payment_count": state["payment_count"],
        "control_total": Decimal(state["control_total"]),
        "batch_count": state["batch_count"],
    }
