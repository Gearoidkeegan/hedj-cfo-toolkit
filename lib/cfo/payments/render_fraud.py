"""T10: fraud judgement, and the report a person actually reads.

The scripts in `cfo.payments.fraud` have already found everything -- every
`Flag` this module renders already carries both compared values and a
source, and no function here calls a fraud check or reads an invoice. What
the `payments.fraud-judgement` task's model decides, per flag, is whether it
matters and why, and it may optionally group several flags that are really
one story (a split payment across three invoices, a changed bank detail and
a lookalike sending domain on the same one) so a reader sees one narrative
rather than a needless repetition.

**The model judges; it never finds.** Its input is the flags the scripts
already raised (and a batch summary), never the invoices themselves. It may
not invent a flag, change a severity downward, or contradict a compared
value the scripts already found -- see `_validate_judgements` and
`judgement_schema`, which enforce this mechanically rather than trust the
model's own restraint. `judgement_schema` is built fresh for each run from
that run's own flags (a static schema cannot know a run's invoice numbers in
advance); the task asset's own `schema.json` fixes the twelve check ids,
which are known ahead of time, but leaves `where` open for exactly this
reason.

**What was not checked is never a footnote.** `checks_performed` (from
`cfo.payments.fraud`) carries a reason for every check that did not run, in
a finance manager's own words, and `_checks_performed_lines` gives it its
own section, always -- whether or not any flag was raised. A report that
says nothing here would let a reader assume every check ran; the sender
domain check silently never running, and nobody knowing, is the exact
failure this whole task chain exists to prevent.

**An empty report is the dangerous case.** With no flags at all, this still
prints what was checked and says so in plain words (I2 in
`cfo.policy.render_report`'s own history: an all-zero scoreboard with
nothing beside it reads as a clean bill of health whether or not anything
was actually looked at). Follows that module for style throughout: a
Markdown document assembled from plain functions, then handed to
`cfo.documents.docx_build.build_docx`.

Dashes in prose are built with `chr(0x2013)`, never typed with two hyphens in
a row -- a literal double hyphen reaches Word as two hyphens in a document a
finance manager reads, because Word only autocorrects what someone types
into it, never what a file already holds. See `cfo.policy.render_report`'s
own guard tests, and this module's equivalent, for why.

**This module never touches a payment.** It takes flags, a task's already
validated judgements, `checks_performed` and a run's `control_block`
(`cfo.payments.signoff.control_block`), and turns them into a document. It
builds nothing an emitter could read: there is no import here, by design,
of the payment model, the batch splitter or any emitter module.
"""
import os

from cfo.console import ToolkitError
from cfo.documents.docx_build import build_docx
from cfo.io import locked, read_json
from cfo.payments.fraud import FLAG_EXCEPTION_MESSAGE_RE as _FLAG_EXCEPTION_MESSAGE_RE
from cfo.payments.fraud import SEVERITIES
from cfo.payments.signoff import STATE_FILENAME as _SIGNOFF_STATE_FILENAME
from cfo.payments.signoff import prepared_by_label
from cfo.tasks.schema_check import validate

REQUIRED_META = ("title", "company", "date", "classification")

# Plain, finance-manager-facing labels for the twelve check ids -- the raw id
# ("just_under_a_limit") is never fit for a heading in a document a finance
# manager reads. Falls back to a humanised version of the id itself (see
# `_check_label`) for any id this dict does not name, so a new check added to
# `cfo.payments.fraud.CHECK_IDS` without a matching update here still renders
# something readable rather than raising.
CHECK_LABELS = {
    "changed_bank_details": "Changed bank details",
    "new_payee_large_amount": "New payee, large amount",
    "lookalike_supplier_name": "Lookalike supplier name",
    "lookalike_sender_domain": "Lookalike sender domain",
    "iban_country_mismatch": "IBAN country mismatch",
    "sequential_invoice_numbers": "Sequential invoice numbers",
    "just_under_a_limit": "Just under an approval limit",
    "split_to_stay_under": "Split to stay under a limit",
    "round_sum": "Round sum",
    "odd_timing": "Odd timing",
    "near_duplicate_invoice": "Near-duplicate invoice",
    "hidden_instructions": "Hidden instructions",
}

# Flag.compared carries either {"old", "new"} (a changed value) or {"a", "b"}
# (two things held up against each other) -- see cfo.payments.fraud.Flag.
# Plain labels for each, for a document a finance manager reads rather than
# a raw dict key.
_COMPARED_LABELS = {"old": "Before", "new": "After", "a": "A", "b": "B"}

DASH = " " + chr(0x2013) + " "

# A flag-derived exception's message, as `cfo.payments.cli.cmd_review` writes
# it today: "[check_id] <flag.summary>" -- see `_flag_dispositions` below.
# Imported from `cfo.payments.fraud`, the one place this format's regex is
# defined -- see `cfo.payments.workbook`'s own identical import, and N5's
# "Also" note (second review) on why a second, independent copy of this
# regex is exactly the kind of thing that drifts unnoticed.


def _read_signoff_state(run_dir):
    """`signoff.json` read directly -- H6: this report must be able to show
    a reviewer's decision without waiting on, or depending on, whatever a
    caller (`cfo.payments.cli`) does with it. Goes straight to the file
    `cfo.payments.signoff` itself writes, under the same `locked()` a
    concurrent writer holds, so this never reads a half-written file, and
    without going through that module's own read functions -- every one of
    which raises when no review has been started yet, and a draft report
    must still build in that case, simply showing no disposition for
    anything. `None` when `run_dir` is falsy, or the run has no
    signoff.json at all."""
    if not run_dir:
        return None
    path = os.path.join(run_dir, _SIGNOFF_STATE_FILENAME)
    with locked(path):
        return read_json(path)


def _flag_dispositions(run_dir):
    """`{(check, where, summary): {"accepted", "reason", "by", "at"}}` --
    the reviewer's own decision, read from `run_dir`'s `signoff.json`, for
    every fraud flag its exceptions cover. A flag with no matching
    exception, or one with no disposition recorded yet, is simply absent
    (never a fabricated "Accepted") -- see `cfo.payments.workbook`'s own
    copy of this function, which this mirrors exactly, so a flag's
    disposition reads the same way in the workbook and in this report.

    Each of `signoff.json`'s own `exceptions` entries is matched back to
    the flag it was raised for by its `where` and the `[check] summary`
    `cmd_review` writes into a flag-derived exception's `message`; an
    exception without the bracket prefix at all (an unreadable document, a
    failed invoice, ...) is not a flag and is skipped.

    **N5 (second review):** keyed on `(check, where, summary)`, not
    `(check, where)` alone -- see `cfo.payments.workbook._flag_dispositions`'
    own docstring for the exact failure this closes (`_check_lookalike_
    sender_domain` can raise up to three flags sharing one `(check, where)`
    pair, and a two-field key silently collapsed them, so a rejection could
    render as an acceptance). `summary` is read straight out of the
    exception's own message, after the same prefix this regex already
    strips -- nothing new to carry, nothing that can drift from what
    `cmd_review` actually wrote.

    When an exception has been dispositioned more than once (a changed
    mind), this uses the LATEST entry, because that is what the reviewer
    actually decided in the end."""
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


def _flag_disposition_line(disposition):
    """One plain sentence for a flag's own reviewer decision -- "Rejected"
    or "Accepted", who by, and their reason -- or `None` when this run's
    signoff.json carries no decision for this flag yet (a draft report, or
    one built before this exception was dispositioned). H6: this is the
    line that was missing entirely -- a rejected exception previously left
    no mark on the finding it belonged to, so a report with four rejections
    among eleven findings read as eleven approvals."""
    if disposition is None:
        return None
    verdict = "Accepted" if disposition.get("accepted") else "Rejected"
    by = _cell(disposition.get("by") or "not recorded")
    at = str(disposition.get("at") or "").strip()
    reason = _cell(disposition.get("reason") or "")
    when = f" at {_cell(at)}" if at else ""
    tail = f"{DASH}{reason}" if reason else ""
    return f"**Reviewer's decision:** {verdict} by {by}{when}{tail}"


def _unreadable_documents_lines(unreadable_documents):
    """Its own section, always present, whether or not there is anything to
    list -- the same "never a footnote, never implied by absence" rule the
    module docstring already holds `checks_performed` to (H6). Before this,
    the word "unreadable" appeared here zero times: a report filed after a
    run of twenty invoices, one of which could not be read at all, implied
    all twenty had been checked. `cfo.payments.workbook`'s Exceptions sheet
    already gets this right; this mirrors it in the document a finance
    manager actually keeps."""
    lines = ["# Unreadable documents", ""]
    docs = list(unreadable_documents or [])
    if not docs:
        lines += [f"No documents were unreadable for this run{DASH}every readable document "
                 "was checked.", ""]
        return lines
    lines += [f"{len(docs)} document(s) could not be read at all, and so were never checked "
             f"for fraud{DASH}there was nothing to check:", ""]
    for where, message in docs:
        lines.append(f"- **{_cell(where)}**{DASH}{_cell(message)}")
    lines.append("")
    return lines


def _cell(value):
    """Flatten anything interpolated into the document: a newline or a
    stray '|' in a script's own text, or the model's, must never be able to
    break the Markdown around it."""
    return str(value).replace("|", "/").replace("\n", " ").strip()


def _english_list(items):
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _check_label(check_id):
    return CHECK_LABELS.get(check_id, str(check_id).replace("_", " ").capitalize())


def _compared_pairs(compared):
    if not isinstance(compared, dict):
        return []
    return [(_COMPARED_LABELS.get(str(key), str(key).title()), value)
            for key, value in compared.items()]


def judgement_schema(flags):
    """A schema for this run's own flags: `check` and `where` are each
    constrained to the values this run's flags actually carry, so a
    judgement naming a check or a location that was never raised is a
    schema violation, not a silent pass-through -- checked with the real
    checker, `cfo.tasks.schema_check.validate`, the same one every task
    output in this toolkit is checked with.

    Built fresh per run rather than shipped as the task asset's own static
    schema.json: that file fixes the twelve check ids, known ahead of any
    run, but a run's own invoice numbers are not known until the scripts
    have already run -- exactly why `where` is left open there and closed
    here."""
    checks = sorted({flag.check for flag in flags})
    wheres = sorted({flag.where for flag in flags})
    return {
        "type": "object",
        "required": ["judgements"],
        "additionalProperties": False,
        "properties": {
            "judgements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["check", "where", "matters", "why"],
                    "additionalProperties": False,
                    "properties": {
                        "check": {"type": "string", "enum": checks},
                        "where": {"type": "string", "enum": wheres},
                        "matters": {"type": "boolean"},
                        "why": {"type": "string"},
                        "group": {"type": "string"},
                    },
                },
            },
        },
    }


def _validate_judgements(flags, judgements):
    """Schema-level rejection (`judgement_schema`, above) plus one check the
    schema cannot express: `check` and `where` are validated as fields on
    their own, so a judgement could still pair a real check with a real but
    mismatched location -- a pairing no flag this run actually raised. That
    exact pairing is checked here against the flags themselves, because a
    judgement about a flag that was never raised is a defect, not a
    difference of opinion."""
    problems = list(validate({"judgements": judgements}, judgement_schema(flags)))
    raised = {(flag.check, flag.where) for flag in flags}
    for index, item in enumerate(judgements):
        if not isinstance(item, dict):
            continue
        pair = (item.get("check"), item.get("where"))
        if pair not in raised:
            problems.append((f"$.judgements[{index}]",
                             f"no flag was raised for check {item.get('check')!r} at "
                             f"{item.get('where')!r}"))
    return problems


def _judgement_note(judgement):
    if judgement is None:
        return "No assessment was returned for this finding."
    why = str(judgement.get("why") or "").strip()
    lead = "Assessed as worth acting on" if judgement.get("matters") else \
        "Assessed as unlikely to matter"
    return f"{lead}{DASH}{why}" if why else f"{lead}."


def _flag_heading(flag):
    return f"{_check_label(flag.check)}{DASH}{str(flag.severity).title()}{DASH}{flag.where}"


def _single_flag_block(flag, judgement, disposition=None, level=2):
    lines = [f"{'#' * level} {_cell(_flag_heading(flag))}", "", _cell(flag.summary), ""]
    pairs = _compared_pairs(flag.compared)
    lines += [f"**{label}:** {_cell(value)}" for label, value in pairs]
    if pairs:
        lines.append("")
    lines.append(f"Source: {_cell(flag.source)}" if str(flag.source or "").strip()
                 else "Source: not recorded.")
    # H6: the reviewer's own decision on this exact finding, when
    # signoff.json has one on record -- see _flag_disposition_line.
    disposition_line = _flag_disposition_line(disposition)
    if disposition_line:
        lines += ["", disposition_line]
    lines += ["", _cell(_judgement_note(judgement)), ""]
    return lines


def _group_block(flags_in_group, judgements_by_key, dispositions_by_key=None, level=2):
    """Several flags the model tied together as one story: one heading, the
    reasoning stated once (deduplicated, in case more than one flag's own
    `why` happens to repeat it word for word), then each flag as its own
    line -- never each flag as its own full block. The policy tool's own
    history is the reason for the "once" rule: a clause there once appeared
    three times with the same two sentences a few lines apart, and it took a
    board-level read to notice.

    **H6:** a reviewer's decision is never shared across the group -- each
    flag's own line carries only its own disposition (`dispositions_by_key`,
    keyed by `(check, where)`), because two findings tied into one story can
    still have been decided differently."""
    dispositions_by_key = dispositions_by_key or {}
    worst = min(SEVERITIES.index(flag.severity) for flag in flags_in_group)
    severity = SEVERITIES[worst]
    checks = _english_list(sorted({_check_label(flag.check) for flag in flags_in_group}))
    heading = f"{checks}{DASH}{severity.title()}{DASH}one story across {len(flags_in_group)} findings"
    lines = [f"{'#' * level} {_cell(heading)}", ""]

    notes = []
    for flag in flags_in_group:
        note = _cell(_judgement_note(judgements_by_key.get((flag.check, flag.where))))
        if note not in notes:
            notes.append(note)
    lines += notes + [""]

    for flag in flags_in_group:
        pairs = "; ".join(f"{label}: {_cell(value)}" for label, value in _compared_pairs(flag.compared))
        source = _cell(flag.source) if str(flag.source or "").strip() else "not recorded"
        disposition = dispositions_by_key.get((flag.check, flag.where, flag.summary))
        disposition_text = ""
        if disposition is not None:
            verdict = "Accepted" if disposition.get("accepted") else "Rejected"
            reason = _cell(disposition.get("reason") or "")
            disposition_text = f"{DASH}Reviewer: {verdict}" + (f" ({reason})" if reason else "")
        lines.append(f"- **{_cell(flag.where)}** ({_check_label(flag.check)}){DASH}{_cell(flag.summary)}"
                     f"{DASH}{pairs}{DASH}source: {source}{disposition_text}")
    lines.append("")
    return lines


def _bucket_by_group(flags, judgements_by_key):
    """Rendering units in first-appearance order: `("single", flag)` for a
    flag with no grouping key, or one `("group", key)` per distinct
    grouping key the first time it is seen, with every flag sharing that key
    accumulated under it regardless of where it falls in `flags`. Grouping
    is read from the model's own judgement for that flag, keyed by
    `(check, where)` -- never invented here."""
    groups, order, seen = {}, [], set()
    for flag in flags:
        judgement = judgements_by_key.get((flag.check, flag.where))
        key = str((judgement or {}).get("group") or "").strip()
        if key:
            groups.setdefault(key, []).append(flag)
            if key not in seen:
                order.append(("group", key))
                seen.add(key)
        else:
            order.append(("single", flag))
    return order, groups


def _checks_performed_lines(checks_performed):
    """What this run checked, and what it did not and why -- its own
    section, always present whether or not any flag was raised (see the
    module docstring on why an empty findings list must never be the only
    thing a reader sees)."""
    lines = ["# Checks performed", ""]
    check_ids = sorted(checks_performed)
    performed = [cid for cid in check_ids if checks_performed[cid].get("performed")]
    not_performed = [(cid, checks_performed[cid].get("reason", "")) for cid in check_ids
                     if not checks_performed[cid].get("performed")]

    if performed:
        lines += [f"This run checked: {_english_list([_check_label(c) for c in performed])}.", ""]
    else:
        lines += ["No checks could be run for this batch.", ""]

    if not_performed:
        lines += ["**Not checked, and why:**", ""]
        for check_id, reason in not_performed:
            lines.append(f"- **{_check_label(check_id)}:** "
                         f"{_cell(reason) if reason else 'no reason was recorded.'}")
        lines.append("")
    else:
        lines += ["Every check this tool runs was performed for this batch.", ""]
    return lines


def _control_block_lines(control_block):
    """The control block a moment of friction rests on: run id, who
    prepared it, source files and hashes, the payment count and control
    total -- see `cfo.payments.signoff.control_block`. `reviewed_by` and
    `reviewed_at` are blank before sign-off, and that is said in words
    rather than left as a blank a reader might take for "nobody needed
    to"."""
    lines = ["# Control", "",
             f"**Run:** {_cell(control_block.get('run_id', '') or 'not recorded')}", ""]
    # F1 (Summary sheet review, 2026-09-23), small item: `prepared_by_label`
    # annotates the raw account name (`getpass.getuser()`, an operational
    # detail -- see `cfo.payments.signoff`'s own module docstring) so it
    # never reads as a person, directly above "Reviewed by" a few lines
    # below.
    lines += [f"**Prepared by:** "
             f"{_cell(prepared_by_label(control_block.get('prepared_by', '')) or 'not recorded')} "
             f"at {_cell(control_block.get('prepared_at', '') or 'not recorded')}", ""]

    reviewed_by = str(control_block.get("reviewed_by") or "").strip()
    if reviewed_by:
        lines += [f"**Reviewed by:** {_cell(reviewed_by)} at "
                 f"{_cell(control_block.get('reviewed_at', ''))}", ""]
    else:
        lines += ["**Reviewed by:** not yet signed off.", ""]

    lines += [f"**Payments:** {_cell(control_block.get('payment_count', ''))} across "
             f"{_cell(control_block.get('batch_count', ''))} batch(es), control total "
             f"{_cell(control_block.get('control_total', ''))}", ""]

    source_files = control_block.get("source_files") or []
    if source_files:
        lines += ["| Source file | SHA-256 |", "|---|---|"]
        for item in source_files:
            lines.append(f"| {_cell(item.get('name', ''))} | {_cell(item.get('sha256', ''))} |")
        lines.append("")
    return lines


def report_markdown(flags, judgements, checks_performed, control_block, meta, run_dir=None,
                    unreadable_documents=()):
    """The full fraud report, front matter to the control block, ready for
    `build_docx(..., template="report")`.

    `flags` are `cfo.payments.fraud.Flag` instances (`check_batch`'s own
    output, unmodified); `judgements` is the `payments.fraud-judgement`
    task's already-schema-checked output's `judgements` list -- re-checked
    here regardless (`_validate_judgements`), because a judgement citing a
    flag that was never raised must never reach a reader as though it had
    been. `checks_performed` is `cfo.payments.fraud.checks_performed`'s own
    return value; `control_block` is `cfo.payments.signoff.control_block`'s.

    **H6:** `run_dir`, when given, is read directly for `signoff.json`'s own
    dispositions (`_flag_dispositions`) -- every finding shows the
    reviewer's actual decision on it, "Accepted" or "Rejected", rather than
    leaving a reader to conclude a silent "Reviewed by" line above eleven
    findings means all eleven were approved. `unreadable_documents` --
    `[(where, message), ...]`, the same shape `cfo.payments.workbook`
    already takes -- gets its own "Unreadable documents" section
    (`_unreadable_documents_lines`), always present, so a report filed
    after a run of twenty invoices never implies all twenty were checked
    when one of them could not be read at all."""
    missing = [key for key in REQUIRED_META if not str(meta.get(key, "")).strip()]
    if missing:
        raise ToolkitError([(key, f"the fraud report needs a '{key}' value in meta")
                            for key in missing])
    if not isinstance(checks_performed, dict) or not checks_performed:
        raise ToolkitError(("checks_performed", "the fraud report needs the run's "
                            "checks_performed dict, naming every check this tool runs"))
    if not isinstance(control_block, dict):
        raise ToolkitError(("control_block", "the fraud report needs the run's control block"))

    problems = _validate_judgements(flags, judgements)
    if problems:
        raise ToolkitError(problems)

    lines = ["---"] + [f"{key}: {meta[key]}" for key in REQUIRED_META] + ["---", ""]

    judgements_by_key = {}
    for item in judgements:
        judgements_by_key.setdefault((item.get("check"), item.get("where")), item)
    dispositions_by_key = _flag_dispositions(run_dir)

    lines += ["# Findings", ""]
    if not flags:
        lines += ["No fraud flags were raised for this batch.", "",
                 "That is not the same as a clean bill of health" + DASH + "see 'Checks "
                 "performed' below for exactly what was, and was not, looked at.", ""]
    else:
        order, groups = _bucket_by_group(flags, judgements_by_key)

        def _unit_severity(unit):
            kind, value = unit
            if kind == "single":
                return SEVERITIES.index(value.severity)
            return min(SEVERITIES.index(flag.severity) for flag in groups[value])

        for kind, value in sorted(order, key=_unit_severity):
            if kind == "single":
                lines += _single_flag_block(
                    value, judgements_by_key.get((value.check, value.where)),
                    dispositions_by_key.get((value.check, value.where, value.summary)))
            else:
                lines += _group_block(groups[value], judgements_by_key, dispositions_by_key)

    lines += _unreadable_documents_lines(unreadable_documents)
    lines += _checks_performed_lines(checks_performed)
    lines += _control_block_lines(control_block)
    return "\n".join(lines)


def _severity_counts(flags):
    tally = {severity: 0 for severity in SEVERITIES}
    for flag in flags:
        if flag.severity in tally:
            tally[flag.severity] += 1
    return tally


def build_report(flags, judgements, checks_performed, control_block, out_path, meta,
                 run_dir=None, unreadable_documents=(), brand=None):
    """Render, build and save the fraud report. Returns counts rather than
    the flags themselves, so the result stays plain JSON for a CLI command
    to `emit()` directly -- `emit()` is `cfo.console.emit`, the JSON-line
    stdout writer every command uses, never a payment emitter.

    `run_dir` and `unreadable_documents` are passed straight through to
    `report_markdown` -- see there (H6)."""
    md = report_markdown(flags, judgements, checks_performed, control_block, meta,
                         run_dir=run_dir, unreadable_documents=unreadable_documents)
    result = build_docx(md, out_path, template="report", brand=brand)
    return {"document": result["document"], "flags": len(flags),
            "counts": _severity_counts(flags), "_warnings": result["_warnings"]}
