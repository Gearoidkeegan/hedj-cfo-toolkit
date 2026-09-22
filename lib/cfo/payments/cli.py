"""T14: the `payments` command group's bodies -- the wiring that turns
thirteen separately-built pieces (T1-T13) into one tool a person can run
start to finish. Follows `cfo.policy.cli` for shape: one function per
command, each reading the run's own state, doing one step, writing its
result and returning a small dict for the CLI's single JSON line.

Three gaps are closed here, deliberately, because nothing upstream ever
calls the functions that close them:

1. **An approved bank-detail change reaches the supplier master.**
   `cmd_disposition` calls `suppliers.apply_approved_change` itself, once,
   right after `signoff.disposition` records an *accepted*
   `changed_bank_details` exception -- see `_bank_change_lookup` (finds the
   change) and `_apply_if_bank_change` (applies it). Nothing else in this
   module, or anywhere else, calls `apply_approved_change`.
2. **An unreadable document reaches the workbook.** `cmd_start` is the one
   place that reads every extracted document's `text_chars` and decides a
   document carries no readable text at all; that list is threaded through
   `check`/`review`/`build` and handed to
   `workbook.build_payments_workbook(..., unreadable_documents=...)`
   untouched.
3. **A cost category reaches the workbook.** `Payment` has no
   `cost_category` field (see `cfo.payments.model`'s own docstring on why);
   `cmd_review` builds `cost_categories = {payment.reference: category}`
   alongside the batch, from the same invoice the payment came from, and
   `cmd_build` passes it to `build_payments_workbook(..., cost_categories=...)`.

`payments build` refuses to write `pain001` or `csv` -- an actual payment
instruction -- before `signoff.is_signed_off` is true. `workbook` and
`report` may be built earlier, as drafts: `build_payments_workbook` and
`render_fraud.report_markdown` already read `signoff.control_block` and
show "not yet reviewed" honestly when it is blank, so the NOT-REVIEWED
draft this module names is that same file, not a special case built here.

Two things a real model would compute, this module never does: it never
picks a supplier match above `suppliers.match`'s exact/near-exact levels (an
unmatched name is a `lookalike_supplier_name` candidate, or a gap to report,
never a guess), and it never invents a limit, a bank detail or a judgement.
"""
import dataclasses
import datetime
import email.utils
import os
import re
import tempfile
from decimal import Decimal, InvalidOperation

from cfo import extract as extract_mod
from cfo import runs
from cfo.console import ToolkitError
from cfo.io import locked, read_json, sha256_bytes, write_json_atomic
from cfo.payments import approvals, batch as batch_mod, fraud, model, signoff, suppliers, validate
from cfo.payments.extract import invoice_from_output
from cfo.tasks.prepare import task_work_dir

INVOICE_FIELDS_TASK = "payments.invoice-fields"
RED_TEAM_TASK = "payments.red-team"
ALL_OUTPUTS = ("pain001", "csv", "workbook", "report")
PAYMENT_OUTPUTS = ("pain001", "csv")  # never written before sign-off -- see cmd_build
DEFAULT_OUTPUTS = "workbook,report"


# --- run-scoped state: <run>/payments/<name>.json, the same shape
# cfo.policy.state uses for <run>/policy/<name>.json, inlined here rather
# than shared -- that module's own directory name is "policy", not
# configurable, so reusing it would put this tool's state in the wrong
# folder. ---

def _payments_dir(run_dir):
    path = os.path.join(run_dir, "payments")
    os.makedirs(path, exist_ok=True)
    return path


def _state_path(run_dir, name):
    return os.path.join(_payments_dir(run_dir), f"{name}.json")


def _read_state(run_dir, name, default=None):
    return read_json(_state_path(run_dir, name), default)


def _write_state(run_dir, name, data):
    write_json_atomic(_state_path(run_dir, name), data)
    return data


def _require_state(run_dir, name, hint):
    data = _read_state(run_dir, name)
    if data is None:
        raise ToolkitError((name, f"not ready yet: run `payments {hint}` first"))
    return data


def _digest(text):
    return sha256_bytes(str(text).replace("\r\n", "\n").encode("utf-8"))


# --- cmd_start: extraction, the supplier master and the approval matrix,
# and the one place an unreadable document is ever noticed. ---

def _invoice_units(manifest):
    """One entry per invoice a person actually sent, in `display_file`
    order: `cfo.extract.extract` gives an email's PDF attachment its own
    document, alongside the covering email, and a fraud run must judge the
    invoice as one thing, not two. `content_doc_id` is the attachment's own
    id when there is one -- the invoice's real text lives there, not in
    "please find attached" -- and `email_doc_id` is the covering email's id,
    kept only so `sender_email` can be lifted from its own `From` header
    (see `_sender_email`). A document with no supported attachment is its
    own content and carries no `email_doc_id` unless it is itself an email
    (an invoice emailed as plain text in the body, not as an attachment)."""
    docs = {d["doc_id"]: d for d in manifest["documents"]}
    children = {}
    for doc in docs.values():
        parent = doc.get("parent")
        if parent:
            children.setdefault(parent, []).append(doc["doc_id"])

    units = []
    for doc in docs.values():
        if doc.get("parent"):
            continue  # handled as another document's content below
        kids = sorted(children.get(doc["doc_id"], []))
        content_id = kids[0] if kids else doc["doc_id"]
        content_doc = docs[content_id]
        units.append({
            "content_doc_id": content_id,
            "email_doc_id": doc["doc_id"] if doc["type"] == "email" else None,
            "display_file": doc["files"][0],
            "readable": content_doc.get("text_chars", 0) > 0,
        })
    return sorted(units, key=lambda u: u["display_file"])


def cmd_start(args):
    """Extracts every invoice under `--invoices`, loads the supplier master
    and the approval matrix (each optional: a run with neither still works,
    per `suppliers.load_master`/`approvals.load_matrix`), and pins the batch
    the run will eventually build -- the debtor's own account and name, the
    currency the run sells in, the execution date and the reference prefix
    every batch's external reference is built from. None of this is in the
    brief's own CLI shape; it has to live somewhere, and `cfo.payments.batch`
    needs all of it before a single `Batch` can exist, so it is pinned once,
    here, the same way `policy start` pins the clause selection for the rest
    of a policy run.

    Sets aside the one thing `cmd_check` cannot itself discover: which
    invoices carry no readable text at all (an image-only scan, say).
    `unreadable.json` is threaded, untouched, all the way to
    `workbook.build_payments_workbook`'s own `unreadable_documents` -- see
    the module docstring's gap 2.
    """
    if not os.path.isdir(args.invoices):
        raise ToolkitError(("--invoices", f"not a folder: {args.invoices}"))
    runs.load_run(args.run)
    extract_mod.extract(args.run, [args.invoices])

    master = suppliers.load_master(args.master)
    matrix = approvals.load_matrix(args.matrix)

    manifest = read_json(os.path.join(args.run, "extract", "manifest.json"), {"documents": []})
    units = _invoice_units(manifest)
    unreadable = [(u["display_file"], "no readable text was found in this document -- if it is "
                                      "a scanned image with no text layer, it cannot be processed "
                                      "and a better copy is needed")
                  for u in units if not u["readable"]]

    config = {
        "invoices_dir": os.path.abspath(args.invoices),
        "master_path": os.path.abspath(args.master) if args.master else None,
        "matrix_path": os.path.abspath(args.matrix) if args.matrix else None,
        "debtor_account": args.debtor_account or "", "debtor_name": args.debtor_name or "",
        "debtor_bic": args.debtor_bic or "", "initiating_party": args.initiating_party or "",
        "sell_currency": (args.sell_currency or "").upper(),
        "execution_date": args.execution_date or runs.load_run(args.run)["started_at"][:10],
        "reference_prefix": args.reference_prefix or os.path.basename(os.path.abspath(args.run)),
    }
    _write_state(args.run, "config", config)
    _write_state(args.run, "units", units)
    _write_state(args.run, "unreadable", unreadable)
    # Cleared, not left stale, if `start` is ever run again (a corrected
    # --invoices folder, say): every later state file is only ever built
    # from `units`/`config`, and a stale finding for a document `units` no
    # longer lists would otherwise survive into `check`.
    #
    # **`invoice-results` and `failed` are deliberately NOT in this list --
    # see B2.** They are the one thing here real AI-task work went into
    # (`task prepare`/`accept` for every invoice, one at a time) or a
    # person's own judgement call (`payments mark-failed`), and the most
    # common reason `start` is ever re-run is a correction that has nothing
    # to do with either -- a wrong `--execution-date`, say. Wiping them on
    # every re-run destroyed real, already-collected answers for no
    # reason: `cmd_extract_input` already keeps only the results whose
    # `content_doc_id` this run's own `units` still lists and whose sha256
    # still matches (a document that actually changed, or that dropped out
    # of `--invoices` altogether, is dropped there, not here) -- that
    # per-document check is the correct place to decide staleness, not a
    # blanket wipe here that cannot tell "the source changed" from "only
    # the execution date changed".
    for stale in ("pending", "checked", "flags", "performed",
                  "validate-findings", "batches", "cost-categories", "exception-meta"):
        write_json_atomic(_state_path(args.run, stale), None)

    return {"run": args.run, "invoices": len(units),
            "readable": sum(u["readable"] for u in units),
            "unreadable": [where for where, _msg in unreadable],
            "master_suppliers": len(master), "matrix_roles": len(matrix)}


# --- cmd_extract_input / cmd_collect: one invoice-fields task, reused for
# every invoice, one at a time -- the same shape cfo.policy.cli's
# obligations-input/obligations-collect use for one facility agreement's
# parts. ---

def cmd_extract_input(args):
    """Lists every readable invoice's own extracted text file
    (`<run>/extract/docs/<doc_id>.md`, already written by `cmd_start`) as
    `files`, in a stable order. The skill works through them one at a time:
    `task prepare --task .../invoice-fields --input invoice=<files[i]>`,
    then `task accept`, then `payments collect` -- reusing the one task id,
    exactly as `policy obligations-input` reuses its own."""
    units = _require_state(args.run, "units", "start")
    readable = [u for u in units if u["readable"]]
    pending = []
    for unit in readable:
        path = os.path.join(args.run, "extract", "docs", f"{unit['content_doc_id']}.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        pending.append({"file": path, "display_file": unit["display_file"],
                        "content_doc_id": unit["content_doc_id"],
                        "email_doc_id": unit["email_doc_id"], "sha256": _digest(text)})
    _write_state(args.run, "pending", pending)
    # A result collected for a document `pending` no longer lists, or whose
    # text has changed since, is stale -- kept only when it still matches.
    wanted = {item["content_doc_id"]: item["sha256"] for item in pending}
    results = _read_state(args.run, "invoice-results", {}) or {}
    kept = {doc_id: entry for doc_id, entry in results.items()
            if wanted.get(doc_id) == entry.get("sha256")}
    _write_state(args.run, "invoice-results", kept)
    return {"invoices": len(pending), "files": [item["file"] for item in pending]}


def cmd_collect(args):
    """Records the invoice-fields task's accepted output against the
    invoice it was prepared from -- found the same way
    `cfo.policy.cli._collect` finds a coverage batch: by the sha256 of the
    text `task prepare` copied into the task's own `inputs/invoice.txt`,
    matched against `pending.json`. Collecting the same invoice again
    replaces its earlier result, so re-running one invoice costs nothing.

    Immediately lifts the bank details (`cfo.payments.extract.
    invoice_from_output`, T5) against the *structured* extracted document
    (`docs/<doc_id>.json`, not the rendered `.md`) -- the only place in this
    tool that ever reads a location the model reported."""
    pending = _require_state(args.run, "pending", "extract-input")
    accepted_path = os.path.join(task_work_dir(args.run, INVOICE_FIELDS_TASK), "accepted.json")
    output = read_json(accepted_path)
    if output is None:
        raise ToolkitError((INVOICE_FIELDS_TASK, "has no accepted output yet: run `task accept` "
                                                  "for this invoice first"))
    input_path = os.path.join(task_work_dir(args.run, INVOICE_FIELDS_TASK), "inputs", "invoice.txt")
    with open(input_path, encoding="utf-8") as fh:
        digest = _digest(fh.read())
    entry = next((item for item in pending if item["sha256"] == digest), None)
    if entry is None:
        raise ToolkitError((INVOICE_FIELDS_TASK, "its accepted output is for an invoice "
                                                  "`extract-input` no longer lists: run "
                                                  "`payments extract-input` again and prepare "
                                                  "one of the files it returns"))

    doc_path = os.path.join(args.run, "extract", "docs", f"{entry['content_doc_id']}.json")
    extracted = read_json(doc_path)
    invoice, exceptions = invoice_from_output(output, extracted)
    invoice["_display_file"] = entry["display_file"]
    invoice["_email_doc_id"] = entry["email_doc_id"]

    results = _read_state(args.run, "invoice-results", {}) or {}
    results[entry["content_doc_id"]] = {"sha256": entry["sha256"], "invoice": invoice,
                                        "exceptions": exceptions}
    _write_state(args.run, "invoice-results", results)
    return {"collected": len(results), "file": entry["display_file"],
            "invoice_number": invoice.get("invoice_number") or "",
            "_warnings": [(entry["display_file"], msg) for _where, msg in exceptions]}


def cmd_mark_failed(args):
    """Records one readable invoice as explicitly, permanently unable to
    be collected this run, with a reason -- the other half of B5's fix.

    `task status --failed` (mentioned in this skill for a *retry* that
    gives up) marks the invoice-fields *task*, and this tool reuses one
    task id for every invoice in a run (see the module docstring); that
    record is a single slot in `run.json`, overwritten the moment the
    next invoice is prepared, so it cannot track more than one failure at
    a time and is the wrong place for this. `failed.json` here is keyed
    by the same `content_doc_id` `units.json` already assigns to that
    invoice, so it survives every later invoice's own prepare/accept, and
    `payments check` and `payments review` both read it directly.

    Once marked, `payments check` no longer refuses over this invoice
    (see there), and `payments review` gives it its own exception, naming
    this reason, so a person still dispositions it and it still leaves an
    audit trail -- it never simply disappears from the run the way a
    missing result used to."""
    units = _require_state(args.run, "units", "start")
    unit = next((u for u in units if u["display_file"] == args.invoice), None)
    if unit is None:
        raise ToolkitError(("--invoice", f"{args.invoice!r} is not one of this run's own "
                                          "invoices -- see `payments start`'s or `payments "
                                          "extract-input`'s own result for the exact name"))
    reason = str(args.reason or "").strip()
    if not reason:
        raise ToolkitError(("--reason", "a reason is required, and an empty or whitespace "
                                        "string is not one"))
    failed = _read_state(args.run, "failed", {}) or {}
    failed[unit["content_doc_id"]] = {"display_file": unit["display_file"], "reason": reason}
    _write_state(args.run, "failed", failed)
    return {"file": unit["display_file"],
            "failed": sorted(entry["display_file"] for entry in failed.values())}


# --- cmd_check: the canonical invoice records every later step reads, the
# validation and fraud checks, and the two extra fields the invoice-fields
# schema never asked for: sender_email and hidden_text. ---

_DATE_HEADER_RE = re.compile(r"^Date:\s*(.*)$")


def _sender_email(run_dir, email_doc_id):
    """`(sender_email, sent_at)` off the covering email's own `From` and
    `Date` headers -- lifted from the *extracted* header blocks, never
    typed by a model (the invoice-fields task is never even given the
    email; it only ever sees the invoice document itself). `(None, None)`
    when there is no covering email, or it carries neither header."""
    if not email_doc_id:
        return None, None
    doc = read_json(os.path.join(run_dir, "extract", "docs", f"{email_doc_id}.json"))
    if doc is None:
        return None, None
    sender, sent_at = None, None
    for block in doc.get("blocks") or []:
        if block.get("kind") != "email_header":
            continue
        if block.get("header") == "From":
            _name, address = email.utils.parseaddr(block.get("text", ""))
            sender = address or None
        elif block.get("header") == "Date":
            try:
                sent_at = email.utils.parsedate_to_datetime(
                    _DATE_HEADER_RE.sub(r"\1", block.get("text", "")))
            except (TypeError, ValueError):
                sent_at = None
    return sender, sent_at


def _hidden_text_for(run_dir, doc_id):
    findings = read_json(os.path.join(run_dir, "extract", "hidden-text.json"),
                         {"findings": []})["findings"]
    return [{"text": f["text_preview"], "location": f["loc"], "technique": f["technique"]}
            for f in findings if f["doc_id"] == doc_id]


def _parse_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        return None


def _serialize_invoice(invoice):
    """`invoice` with its two `date` fields and its `datetime` field turned
    into ISO strings, so `write_json_atomic` (plain `json.dumps`, no date
    support) can write it -- see `_deserialize_invoice` for the reverse.
    Every other field is already a plain string; T5 hands amounts back as
    strings on purpose (see `cfo.payments.extract`'s own docstring)."""
    out = dict(invoice)
    for field in ("invoice_date", "due_date"):
        value = out.get(field)
        if isinstance(value, datetime.date):
            out[field] = value.isoformat()
    sent_at = out.get("sent_at")
    if isinstance(sent_at, datetime.datetime):
        out["sent_at"] = sent_at.isoformat()
    return out


def _deserialize_invoice(invoice):
    out = dict(invoice)
    for field in ("invoice_date", "due_date"):
        out[field] = _parse_date(out.get(field))
    sent_at = out.get("sent_at")
    if sent_at:
        try:
            out["sent_at"] = datetime.datetime.fromisoformat(sent_at)
        except (TypeError, ValueError):
            out["sent_at"] = None
    return out


def _canonical_invoices(run_dir):
    """Every collected invoice, in one shape both `cfo.payments.validate`
    (which reads `supplier`/`number`) and `cfo.payments.extract`'s own
    output (`supplier_name`/`invoice_number`) can read -- see the module
    docstring's note on the two field-naming conventions this tool grew
    independently, in T5 and T6/T8, and never reconciled. Both sets of
    keys are carried, deliberately, rather than picking one and breaking
    the other module's own tests."""
    results = _read_state(run_dir, "invoice-results", {}) or {}
    invoices = []
    for content_doc_id, entry in results.items():
        raw = dict(entry["invoice"])
        display_file = raw.pop("_display_file", "")
        email_doc_id = raw.pop("_email_doc_id", None)
        sender_email, sent_at = _sender_email(run_dir, email_doc_id)
        invoice = dict(raw)
        invoice["supplier"] = raw.get("supplier_name", "")
        invoice["number"] = raw.get("invoice_number", "")
        invoice["filename"] = display_file
        # N1 (second review): the one thing every invoice carries that IS
        # actually unique -- unlike `number`, which two invoices from
        # different suppliers can, and in the wild do, share. See
        # `fraud.Flag.content_doc_id`'s own docstring and `cmd_review`
        # below for what this now anchors instead of `_where(invoice)`.
        invoice["content_doc_id"] = content_doc_id
        invoice["invoice_date"] = _parse_date(raw.get("invoice_date"))
        invoice["due_date"] = _parse_date(raw.get("due_date"))
        invoice["terms"] = ""       # never asked for by payments.invoice-fields -- see the report
        # B3 (CLI half): `bic` and `vat_number` joined `cfo.payments.extract`'s
        # own SCALAR_FIELDS at T5's review, and `raw` (straight from
        # `invoice_from_output`) already carries whatever real value was
        # extracted for each -- blanking them here, unconditionally, the way
        # this line once did for both, threw that away again immediately
        # after extraction had just fixed it. With `bic` always blank,
        # `has_account` (below, in `cmd_review`) could never be true, so a
        # supplier paid by account number rather than IBAN could never be
        # paid at all -- the real 08-hallow-point-software.eml sample,
        # clean, USD, paid by account number with its BIC printed, was
        # reported as "no bank details could be resolved" though both had
        # been read correctly.
        invoice["vat_number"] = str(raw.get("vat_number") or "").strip()
        invoice["bic"] = str(raw.get("bic") or "").strip()
        if sender_email:
            invoice["sender_email"] = sender_email
        if sent_at:
            invoice["sent_at"] = sent_at
        hidden = _hidden_text_for(run_dir, content_doc_id) if content_doc_id else []
        if hidden:
            invoice["hidden_text"] = hidden
        invoices.append(invoice)
    return sorted(invoices, key=lambda inv: inv["filename"])


def cmd_check(args):
    """Validation (`cfo.payments.validate`, T6) and the twelve fraud checks
    (`cfo.payments.fraud`, T8) over every collected invoice. Nothing here
    decides anything; `payments review` is what turns the result into
    exceptions a person dispositions.

    **B5: a readable invoice that was never collected refuses this step,
    unless it was explicitly marked failed first.** Before this, `missing`
    was a warning only -- this command exited 0, and nothing downstream
    ever looked at `missing` again, so the invoice (and whatever fraud flag
    it might have carried) simply vanished from the run with no exception
    and no trace beyond a warning line nobody re-read. `payments
    mark-failed` (see its own docstring) is the way to say, explicitly and
    with a reason, that an invoice will never be collected this run; that
    reason becomes its own exception in `payments review` (see there), so
    it still gets a disposition and an audit-trail entry like every other
    invoice, rather than a silent absence."""
    units = _require_state(args.run, "units", "start")
    config = _require_state(args.run, "config", "start")
    results = _read_state(args.run, "invoice-results", {}) or {}
    failed = _read_state(args.run, "failed", {}) or {}
    readable = [u for u in units if u["readable"]]
    missing = [u["display_file"] for u in readable
              if u["content_doc_id"] not in results and u["content_doc_id"] not in failed]
    failed_readable = [u for u in readable if u["content_doc_id"] in failed
                       and u["content_doc_id"] not in results]
    if missing:
        raise ToolkitError([(where, "was never collected, and was never explicitly marked "
                            "failed either: run `payments extract-input`, prepare and accept "
                            "its invoice-fields task, then `payments collect` -- or, if it "
                            "genuinely cannot be read, `payments mark-failed --run <run> "
                            f"--invoice {where!r} --reason \"<why>\"` -- before `payments "
                            "check` can run; a payment that just silently disappeared from "
                            "the batch, fraud flag and all, is worse than either")
                           for where in missing])
    invoices = _canonical_invoices(args.run)

    master = suppliers.load_master(config["master_path"])
    matrix = approvals.load_matrix(config["matrix_path"])

    findings = []
    for invoice in invoices:
        findings += validate.check_invoice(invoice)
    findings += validate.check_batch(invoices)
    errors, warnings = validate.split_findings(findings)

    flags = fraud.check_batch(invoices, master=master, matrix=matrix)
    performed = fraud.checks_performed(invoices, master=master, matrix=matrix)

    _write_state(args.run, "checked", [_serialize_invoice(inv) for inv in invoices])
    _write_state(args.run, "flags", [dataclasses.asdict(f) for f in flags])
    _write_state(args.run, "performed", performed)
    _write_state(args.run, "validate-findings", {"errors": errors, "warnings": warnings})

    not_performed = [cid for cid, entry in performed.items() if not entry["performed"]]
    failed_display = [u["display_file"] for u in failed_readable]
    result_warnings = [(u["display_file"],
                        f"marked failed ({failed[u['content_doc_id']]['reason']}): it will not "
                        "become a payment line, but `payments review` will list it as its own "
                        "exception so it still gets a disposition") for u in failed_readable]
    result_warnings += [(cid, performed[cid]["reason"]) for cid in not_performed]
    result_warnings += warnings
    return {"invoices": len(invoices), "missing": missing, "failed": failed_display,
            "errors": len(errors), "warnings": len(warnings),
            "flags": len(flags), "flags_by_check": {cid: sum(f.check == cid for f in flags)
                                                     for cid in fraud.CHECK_IDS},
            "not_performed": not_performed, "_warnings": result_warnings}


# --- cmd_review: build the candidate payment batch, work out exceptions
# (fraud flags, unreadable documents, an invoice whose bank details never
# resolved, a validation error), and open sign-off. ---

def _where(invoice):
    number = str(invoice.get("number") or "").strip()
    return number or str(invoice.get("filename") or "").strip() or "unknown invoice"


def _read_amount(invoice, field):
    value = invoice.get(field)
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return None


def _invoice_to_payment(invoice, master, reference_prefix, execution_date):
    """The `Payment` this invoice becomes, plus the `value_date` it
    actually carried before B2's clamp (or `None`, when nothing needed
    clamping) -- see the caller for why the second value matters.

    **B2:** `cfo.payments.model.validate_batch` rejects a batch outright
    if any payment's `value_date` is before the batch's own
    `execution_date` -- correct for the model to enforce, and fatal for
    this tool's own realistic use: an accounts-payable run is very often
    paying an invoice that is *already* overdue by the time it is
    actually paid, and the pre-fix code (`value_date = due_date or
    invoice_date`, never compared against anything) sent exactly that
    invoice straight into a batch that could never validate, with no
    actionable message -- `payments review` simply raised. Clamping
    forward to `execution_date` means the payment still goes out, on the
    date it can actually be sent, and the caller is told so it can tell
    the reviewer rather than quietly paying an overdue invoice as if
    nothing had happened."""
    supplier_id, _reason = suppliers.match(invoice.get("supplier"), master)
    country = ""
    if supplier_id and supplier_id in master:
        country = str(master[supplier_id].get("country") or "").strip()
    reference = str(invoice.get("payment_reference") or invoice.get("number") or "").strip()
    if not reference:
        reference = f"{reference_prefix}-{invoice.get('filename', 'invoice')}"
    value_date = invoice.get("due_date") or invoice.get("invoice_date")
    clamped_from = None
    if value_date and execution_date and value_date < execution_date:
        clamped_from = value_date
        value_date = execution_date
    payment = model.Payment(
        beneficiary_name=str(invoice.get("supplier") or ""),
        beneficiary_address_flat="", beneficiary_address={}, beneficiary_country=country,
        iban=str(invoice.get("iban") or ""), account_number=str(invoice.get("account_number") or ""),
        bic=str(invoice.get("bic") or ""),
        currency=str(invoice.get("currency") or "").upper(),
        amount=_read_amount(invoice, "gross") or Decimal("0"),
        value_date=value_date, reference=reference,
    )
    return payment, clamped_from


def _bank_change_lookup(invoices, master):
    """`{content_doc_id: change}` -- the same comparison `cfo.payments.fraud`'s
    `changed_bank_details` check makes internally (`suppliers.bank_change`,
    a public function), called again here because a `Flag` never carries
    the structured `field`/`supplier_id` a later *approved* change needs
    (see `_apply_if_bank_change`) -- only a summary sentence built from
    them. Calling the same public function a second time, rather than
    parsing that sentence back apart, is the honest way to get it.

    **N1 (second review):** keyed by `content_doc_id`, never `_where(invoice)`
    -- two invoices from different suppliers can share the same number, and
    a `where`-keyed dict here would silently collapse to whichever of the
    two was processed last, the same failure shape B1 already fixed for the
    payment mapping below."""
    lookup = {}
    for invoice in invoices:
        change = suppliers.bank_change(invoice, master)
        if change is not None:
            lookup[str(invoice.get("content_doc_id") or "")] = change
    return lookup


def _reset_empty_review(run_dir):
    """`payments review --reset`'s own escape hatch -- B5's second half.
    `cfo.payments.signoff.start_review` refuses outright to run twice for
    one run (by design: the audit trail it opens must never be reset once
    it means something), but before this there was no way back at all
    from a review opened over bad or empty state -- an `--invoices`
    folder that resolved to nothing readable, say. `cmd_review`'s own
    early-return guard (below) would keep reporting the same stale, empty
    `outstanding` forever, and `signoff.start_review` would refuse a
    second call even after the underlying data was fixed.

    Safe only because it checks first: this discards the review's own
    state (never `signoff.json`'s `dispositions` or `sign_off` -- there
    are none yet, or this refuses) only when nothing has actually been
    decided against it. The moment a single disposition or a sign-off
    exists, this refuses too, and correctly: there is no way back from
    that, on purpose, and there must not be."""
    path = os.path.join(run_dir, signoff.STATE_FILENAME)
    with locked(path):
        state = read_json(path)
        if state is None:
            return
        if state.get("dispositions") or state.get("sign_off"):
            raise ToolkitError(("--reset", "this run's review already has a disposition or a "
                                "sign-off recorded against it; resetting it would discard an "
                                "audit trail this tool must never lose -- start a new run "
                                "instead"))
        write_json_atomic(path, None)
    _write_state(run_dir, "exception-meta", None)


def cmd_review(args):
    """Opens sign-off for this run (once; a second call just reports what
    is still outstanding, like `policy panel-merge` re-run). Every fraud
    flag, every unreadable document, every invoice explicitly marked
    failed and every invoice this run could not resolve bank details for
    becomes one exception; `signoff.start_review` assigns each an id, and
    `exception-meta.json` remembers, per id, enough to act on an
    *accepted* one later (see `cmd_disposition`) -- and, for every kind,
    the `payment_reference` of the payment it would drop if *rejected*
    instead (see B1, in `cmd_build`'s own `_filter_rejected_payments`):
    `None` for a kind that never had a payment to begin with.

    **B5:** refuses to open at all while any readable invoice is neither
    collected nor explicitly marked failed (`payments mark-failed`) --
    the same gate `payments check` already enforces, checked again here
    because a review must never open over data check has not actually
    seen. `--reset` (see `_reset_empty_review`) discards a review opened
    over bad or empty state, but only before anything has been decided
    against it."""
    if getattr(args, "reset", False):
        _reset_empty_review(args.run)

    if signoff.is_signed_off(args.run) or _read_state(args.run, "exception-meta") is not None:
        outstanding = signoff.outstanding(args.run)
        return {"outstanding": outstanding, "started": False}

    units = _require_state(args.run, "units", "start")
    results = _read_state(args.run, "invoice-results", {}) or {}
    failed = _read_state(args.run, "failed", {}) or {}
    readable = [u for u in units if u["readable"]]
    missing = [u["display_file"] for u in readable
              if u["content_doc_id"] not in results and u["content_doc_id"] not in failed]
    if missing:
        raise ToolkitError([(where, "was never collected, and was never explicitly marked "
                            "failed either: run `payments check` first -- it refuses to run "
                            "for the same reason, and names exactly what to do about each one")
                           for where in missing])

    config = _require_state(args.run, "config", "start")
    execution_date = _parse_date(config["execution_date"]) or datetime.date.today()
    unreadable = _require_state(args.run, "unreadable", "start")
    invoices = [_deserialize_invoice(inv) for inv in _require_state(args.run, "checked", "check")]
    flags = [fraud.Flag(**row) for row in _require_state(args.run, "flags", "check")]
    master = suppliers.load_master(config["master_path"])
    bank_changes = _bank_change_lookup(invoices, master)

    # Payments are built FIRST, not last: an exception's own meta entry
    # (below) needs to be able to name the exact *payment reference* it
    # would drop if rejected (B1), and that mapping only exists once a
    # payment has actually been built from the invoice that raised it.
    #
    # **N1 (second review):** `where_to_reference` (a plain `{where:
    # reference}` dict) is kept for `validate_error` meta below, because a
    # validate.py finding only ever carries a `where` string, never a
    # content_doc_id -- but it is no longer what a *flag's* payment_reference
    # comes from (see `content_doc_id_to_reference`, which cannot collide,
    # because content_doc_id is actually unique). `where_to_references` (a
    # set per where) exists purely to detect the collision `where_to_
    # reference` itself cannot represent -- two invoices sharing a `where`
    # whose payments ended up with two DIFFERENT references -- so it can be
    # refused rather than silently resolved to whichever invoice happened to
    # be processed last (B1's exact failure mode, reopened on this data
    # shape: see the second review's N1).
    payments, cost_categories, where_to_reference = [], {}, {}
    where_to_references, content_doc_id_to_reference, reference_owners = {}, {}, {}
    value_date_warnings = []
    unresolved_exceptions, unresolved_meta = [], []
    for invoice in invoices:
        has_iban = bool(str(invoice.get("iban") or "").strip())
        has_account = bool(str(invoice.get("account_number") or "").strip()) and \
            bool(str(invoice.get("bic") or "").strip() or str(invoice.get("bank_code") or "").strip())
        if has_iban or has_account:
            payment, clamped_from = _invoice_to_payment(invoice, master,
                                                        config["reference_prefix"], execution_date)
            payments.append(payment)
            where = _where(invoice)
            where_to_reference[where] = payment.reference
            where_to_references.setdefault(where, set()).add(payment.reference)
            content_doc_id_to_reference[str(invoice.get("content_doc_id") or "")] = payment.reference
            reference_owners.setdefault(payment.reference, []).append(where)
            if clamped_from is not None:
                value_date_warnings.append((where,
                    f"its value date {clamped_from.isoformat()} is before this batch's "
                    f"execution date {execution_date.isoformat()}; it will be paid on the "
                    "execution date instead of silently as if it were not yet overdue"))
            category = str(invoice.get("cost_category") or "").strip()
            if category:
                cost_categories[payment.reference] = category
        else:
            where = _where(invoice)
            unresolved_exceptions.append((where, "no bank details could be resolved for this "
                                          "invoice (no location was given, or the value at it "
                                          "failed its checksum); it will not be included in "
                                          "this payment run"))
            unresolved_meta.append({"kind": "unresolved_bank_details", "where": where,
                                    "payment_reference": None})

    # **N1 -- the guard the second review asks for, "whatever you choose":**
    # refuse to open a review, naming the offenders, rather than silently
    # resolve either of the two shapes that made B1's fix reopenable.
    #
    # (a) two payments that literally share a `reference` -- the milder
    # variant reproduced in the second review: `_filter_rejected_payments`
    # (cmd_build) matches a rejected exception's payment_reference against
    # EVERY payment carrying it, so a genuine reference collision drops both
    # if either is ever rejected, silently, no matter how the flag-to-payment
    # mapping above is built. `reference_owners` was collected in the loop
    # above, directly off the payments actually built, never re-derived.
    duplicate_references = {ref: wheres for ref, wheres in reference_owners.items()
                            if len(wheres) > 1}
    if duplicate_references:
        raise ToolkitError([("payments", f"{count} payments in this batch share the reference "
                             f"{ref!r} ({', '.join(wheres)}) -- rejecting an exception against "
                             "any one of them would drop all of them, or the wrong one; refusing "
                             "to open this review until each payment has its own reference")
                            for ref, wheres in sorted(duplicate_references.items())
                            for count in [len(wheres)]])

    # (b) a `where` that a validate_error exception (below) would need
    # `where_to_reference` for, but that dict cannot represent honestly,
    # because the invoices sharing that `where` ended up with different
    # references -- `where_to_references` (built above) is what makes that
    # ambiguity detectable at all; see the loop over `errors` below, the one
    # remaining consumer of `where_to_reference` (a flag's own
    # payment_reference no longer goes through it at all -- see
    # `content_doc_id_to_reference`).
    exceptions, meta = [], []
    for where, message in unreadable:
        exceptions.append((where, message))
        meta.append({"kind": "unreadable", "where": where, "payment_reference": None})

    for unit in readable:
        if unit["content_doc_id"] in failed and unit["content_doc_id"] not in results:
            where = unit["display_file"]
            exceptions.append((where, f"marked failed: {failed[unit['content_doc_id']]['reason']}"
                               " -- it will not be included in this payment run"))
            meta.append({"kind": "failed_invoice", "where": where, "payment_reference": None})

    # N1: a validate_error's own `where` is all validate.py ever hands
    # back -- there is no content_doc_id to fall back on the way a flag now
    # has one (see `content_doc_id_to_reference` below). If that `where` is
    # ambiguous (two invoices share it, and their payments ended up with
    # different references), there is no honest single `payment_reference`
    # to record for it, and guessing one is exactly B1's failure mode
    # reopened -- refuse instead, naming the offenders, rather than silently
    # pick whichever invoice happened to be processed last.
    errors = _require_state(args.run, "validate-findings", "check")["errors"]
    ambiguous_error_wheres = sorted({where for where, _message in errors
                                     if len(where_to_references.get(where, ())) > 1})
    if ambiguous_error_wheres:
        raise ToolkitError([(where, f"{len(where_to_references[where])} different invoices in "
                             "this batch are named as this exact invoice number, and this "
                             "validation finding cannot tell which one it belongs to -- refusing "
                             "to open this review until the invoices sharing this number are "
                             "told apart (see the batch-level duplicate-invoice-number finding "
                             "for which suppliers are involved)")
                            for where in ambiguous_error_wheres])
    for where, message in errors:
        exceptions.append((where, message))
        meta.append({"kind": "validate_error", "where": where,
                    "payment_reference": where_to_reference.get(where)})

    for flag in flags:
        exceptions.append((flag.where, f"[{flag.check}] {flag.summary}"))
        change = (bank_changes.get(flag.content_doc_id)
                 if flag.check == "changed_bank_details" else None)
        # Only the four keys `apply_approved_change` actually reads --
        # `last_seen` is a `date`, which plain `json.dumps` cannot write,
        # and nothing here needs it back.
        bank_change = ({"field": change["field"], "old": change["old"], "new": change["new"],
                       "supplier_id": change["supplier_id"]} if change else None)
        # N1: a flag's own payment_reference comes from its content_doc_id,
        # which is unique to the one invoice that raised it -- never from
        # `where_to_reference`, which two invoices sharing a number could
        # collapse to the wrong one (see the module's own docstring and the
        # second review's N1). A flag built with no content_doc_id at all
        # (a test fixture, mostly -- see `fraud.Flag`'s own default) simply
        # gets `None` here, the same honest answer an unresolved invoice
        # already gets.
        meta.append({"kind": "flag", "check": flag.check, "where": flag.where,
                    "bank_change": bank_change,
                    "payment_reference": content_doc_id_to_reference.get(flag.content_doc_id)})

    exceptions += unresolved_exceptions
    meta += unresolved_meta

    sell_currency = config["sell_currency"] or _majority_currency(payments) or ""
    split = batch_mod.build_batches(
        payments, debtor_account=config["debtor_account"], execution_date=execution_date,
        sell_currency=sell_currency, reference_prefix=config["reference_prefix"])

    _write_state(args.run, "cost-categories", cost_categories)
    _write_state(args.run, "batches", _serialize_batches(split))
    # Written *before* start_review, not after: start_review cannot be
    # called a second time for this run (cfo.payments.signoff refuses), so
    # if this process died between the two calls, meta keyed by the ids
    # start_review is about to assign (1, 2, 3, ... in the order given --
    # see signoff._normalize_exceptions) must already be safe on disk, not
    # lost along with everything after a call that cannot be retried.
    _write_state(args.run, "exception-meta",
                {str(index + 1): item for index, item in enumerate(meta)})
    result = signoff.start_review(args.run, list(split), exceptions)
    return {"outstanding": result["exceptions"], "started": True,
            "batch_count": len(split), "payment_count": split.payment_count,
            "control_total": str(split.control_total), "_warnings": value_date_warnings}


def _majority_currency(payments):
    counts = {}
    for payment in payments:
        counts[payment.currency] = counts.get(payment.currency, 0) + 1
    return max(counts, key=counts.get) if counts else None


def _serialize_batches(split):
    out = []
    for b in split:
        out.append({
            "debtor_account": b.debtor_account, "execution_date": b.execution_date.isoformat(),
            "sell_currency": b.sell_currency, "external_reference": b.external_reference,
            "payments": [dict(dataclasses.asdict(p),
                              amount=str(p.amount),
                              value_date=p.value_date.isoformat() if p.value_date else None)
                        for p in b.payments],
        })
    return out


def _deserialize_batches(data):
    out = []
    for b in data:
        payments = []
        for p in b["payments"]:
            fields = dict(p)
            fields["amount"] = Decimal(fields["amount"])
            fields["value_date"] = _parse_date(fields.get("value_date"))
            payments.append(model.Payment(**fields))
        out.append(model.Batch(debtor_account=b["debtor_account"],
                               execution_date=_parse_date(b["execution_date"]),
                               sell_currency=b["sell_currency"],
                               external_reference=b["external_reference"], payments=payments))
    return out


# --- cmd_disposition / cmd_sign_off: cfo.payments.signoff does the
# recording; this is only where an *accepted* changed_bank_details
# exception goes on to update the supplier master (gap 1). ---

def _apply_if_bank_change(run_dir, exception_id, by):
    meta = _read_state(run_dir, "exception-meta", {}) or {}
    item = meta.get(str(exception_id))
    if not item or item.get("kind") != "flag" or item.get("check") != "changed_bank_details":
        return False
    change = item.get("bank_change")
    if not change:
        return False
    config = _require_state(run_dir, "config", "start")
    if not config["master_path"]:
        raise ToolkitError(("--master", "this run has no supplier master file to update; "
                                        "the accepted change was recorded, but nothing on disk "
                                        "was changed"))
    master = suppliers.load_master(config["master_path"])
    record = master.get(change.get("supplier_id"))
    if record is not None and str(record.get(change["field"]) or "") == str(change["new"]):
        return False  # already applied -- a second accept of the same change is a no-op
    updated = suppliers.apply_approved_change(master, change, approved_by=by)
    suppliers.save_master(updated, config["master_path"])
    return True


def _revert_if_bank_change(run_dir, exception_id, by):
    """The mirror of `_apply_if_bank_change`, for a *rejection*: undoes an
    earlier acceptance of this exact `changed_bank_details` exception, if
    (and only if) that acceptance is actually the thing currently live on
    the master (N3, second review -- .superpowers/sdd/2026-09-21-payments-
    fraud/second-review-findings.md).

    Before this existed, accepting the exception wrote the fraud's IBAN
    into the master; rejecting the same exception afterwards correctly
    dropped the payment but left that IBAN standing, live, with the
    reviewer's own name now against it in `bank_detail_changes` -- and
    `changed_bank_details` compares every future invoice against exactly
    that field, so the tool's only critical bank-redirection control was
    permanently disarmed for precisely the account the reviewer had just
    refused. A changed mind must undo what the earlier one did, not merely
    stop repeating it.

    Checked the same way `_apply_if_bank_change`'s own no-op guard is: by
    what the master's field actually holds right now, not by re-reading
    signoff.json's disposition history -- a disposition can be recorded
    more than once (see `signoff.disposition`'s own docstring), and asking
    the master directly is the one place that can never disagree with
    itself about whether the change is still live."""
    meta = _read_state(run_dir, "exception-meta", {}) or {}
    item = meta.get(str(exception_id))
    if not item or item.get("kind") != "flag" or item.get("check") != "changed_bank_details":
        return False
    change = item.get("bank_change")
    if not change:
        return False
    config = _require_state(run_dir, "config", "start")
    if not config["master_path"]:
        return False  # nothing on disk to revert -- see _apply_if_bank_change's own raise
    master = suppliers.load_master(config["master_path"])
    record = master.get(change.get("supplier_id"))
    if record is None or str(record.get(change["field"]) or "") != str(change["new"]):
        return False  # never applied, or already reverted -- nothing to undo
    updated = suppliers.revert_approved_change(master, change, reverted_by=by)
    suppliers.save_master(updated, config["master_path"])
    return True


def cmd_disposition(args):
    """Records one decision against one exception (`signoff.disposition`
    takes exactly one `exception_id`; nothing here ever loops over several
    -- see that module's own docstring on why). When the decision is
    *accept* on a `changed_bank_details` exception, applies the change to
    the supplier master, with `--by` as the approving name, so the same
    legitimate change is not flagged again on the next run.

    **N3:** when the decision is *reject* on that same kind of exception,
    and an earlier *accept* of it already applied the change, this reverts
    it -- see `_revert_if_bank_change`. A changed mind must not leave the
    fraud IBAN standing in the master just because the payment it would
    have funded was correctly dropped.
    """
    if not (args.accept ^ args.reject):
        raise ToolkitError(("--accept/--reject", "give exactly one of --accept or --reject"))
    entry = signoff.disposition(args.run, args.id, accepted=args.accept, reason=args.reason,
                                by=args.by)
    if args.accept:
        master_updated = _apply_if_bank_change(args.run, args.id, args.by)
    else:
        master_updated = _revert_if_bank_change(args.run, args.id, args.by)
    entry["master_updated"] = master_updated
    return entry


def cmd_sign_off(args):
    return signoff.sign_off(args.run, reviewed_by=args.by)


# --- cmd_build: the actual outputs -- pain001, csv, the workbook and the
# fraud report -- gated on sign-off for the two that are an actual payment
# instruction. ---

def _assert_batches_match_signoff(run_dir, batches):
    """B6: the same guard `cfo.payments.workbook._assert_control_matches_
    batches` already puts in front of the workbook, applied here too, in
    front of *every* output this command builds. Before this fix, the
    pain001 and csv branches read `batches.json` straight off disk and
    checked it against nothing -- an injected extra payment, or a batch
    file that had simply drifted from what `payments review` actually
    told the reviewer and `signoff.json` actually recorded, reached a
    bank instruction with no complaint at all. This raises the moment
    `batches.json` disagrees with the run's own signed-off record, before
    any output -- payment file, workbook or report -- is built from it."""
    control = signoff.control_block(run_dir)
    live_batch_count = len(batches)
    live_payment_count = sum(batch.count for batch in batches)
    live_control_total = sum((batch.control_total for batch in batches), Decimal("0"))
    recorded = (control["batch_count"], control["payment_count"], control["control_total"])
    live = (live_batch_count, live_payment_count, live_control_total)
    if recorded != live:
        raise ToolkitError(("batches", f"the run's signed-off record (batch count, payment "
                            f"count, control total) is {recorded}, but batches.json on disk is "
                            f"{live} -- refusing to build anything from a payment file that "
                            "could disagree with what was actually reviewed and signed off"))


def _rejected_exception_ids(run_dir):
    """Every exception id whose *latest* disposition is a rejection.
    `signoff.disposition`'s own docstring: calling it again for an
    exception already dispositioned is allowed -- a changed mind is a
    second entry, appended, never overwritten -- so the last entry
    recorded for an id is the decision that actually stands. There is no
    public getter in `cfo.payments.signoff` for every disposition at
    once (only `outstanding`, which reports exactly the opposite: the
    ones with *no* disposition at all), so this reads `signoff.json`
    directly."""
    state = read_json(os.path.join(run_dir, signoff.STATE_FILENAME))
    if not state:
        return set()
    latest = {}
    for entry in state.get("dispositions", []):
        latest[entry["exception_id"]] = entry["accepted"]
    return {exception_id for exception_id, accepted in latest.items() if not accepted}


def _filter_rejected_payments(run_dir, batches):
    """`batches`, with every payment a rejected exception named removed --
    **B1.** `signoff.outstanding`'s own docstring already promised that a
    rejected exception "won't become a payment line"; nothing before this
    fix ever implemented that promise anywhere -- `cmd_build` read
    `batches.json` and wrote it out, dispositions or no. A clone of this
    tool that accepted every exception produced a byte-identical payment
    file to one that rejected them all, because nothing here had ever
    once compared the two.

    `exception-meta.json`'s own `payment_reference` (set in `cmd_review`,
    from the same invoice the payment was actually built from) is the
    link, rather than re-matching a `where` string against a payment's
    own `reference` -- the two are usually equal but are not the same
    field, and are not guaranteed to agree (see `_invoice_to_payment`:
    `reference` prefers `payment_reference` off the invoice itself, which
    `_where` never reads at all).

    **B6:** recomputes the surviving total two independent ways -- live,
    fresh from `Batch.control_total` (a property, summed only from
    whatever payments are actually left after filtering), and by
    subtracting exactly what this function itself removed from the
    batches' own original total -- and refuses to return anything at all
    if the two disagree, or if a reference this run believed it had
    dropped cannot actually be found in the batches given to it. A
    filtered payment file whose own header disagreed with itself would be
    worse than the bug this exists to fix: a bank would reject it, and
    nobody would know why.
    """
    rejected_ids = _rejected_exception_ids(run_dir)
    if not rejected_ids:
        return list(batches), set()
    meta = _read_state(run_dir, "exception-meta", {}) or {}
    dropped_refs = {meta[exception_id]["payment_reference"] for exception_id in rejected_ids
                   if meta.get(exception_id, {}).get("payment_reference")}
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

    live_total = sum((batch.control_total for batch in filtered), Decimal("0"))
    expected_total = original_total - dropped_total
    if live_total != expected_total or found_refs != dropped_refs:
        raise ToolkitError(("payments", f"the payment total after removing rejected payments "
                            f"({live_total}) does not match the total this run recomputed "
                            f"independently ({expected_total}) -- refusing to emit a payment "
                            "file that could disagree with what was actually reviewed"))
    return filtered, dropped_refs


def cmd_build(args):
    """Builds every requested output. **B4:** every one of them is built
    to a temporary file inside `outputs/` itself first, and nothing is
    given its real name until *every* requested output has been built
    without raising -- a build that fails partway (a workbook's rate
    lookup, say) must never leave a live pain001/csv on disk next to a
    stale NOT-REVIEWED report from a previous, unrelated attempt, with
    the command itself reporting failure. **B6:** `batches.json` is
    checked against the run's own signed-off record before anything is
    built, for every output, not only the workbook. **B1:** `pain001` and
    `csv` -- the two that are an actual payment instruction -- are built
    from `_filter_rejected_payments`'s own, filtered batches; `workbook`
    and `report` still read the unfiltered `batches` for every count and
    total that must match the signed-off record `_assert_batches_match_
    signoff` just checked them against -- but the workbook is also handed
    the same `dropped_refs` `_filter_rejected_payments` returns, so it can
    mark which rows were actually excluded and show the instructed total
    beside the reviewed one (**N4**, second review -- see
    `_filter_rejected_payments`'s own docstring and
    `cfo.payments.workbook.build_payments_workbook`'s `dropped_references`
    parameter). `_filter_rejected_payments` is now called unconditionally,
    not only when pain001/csv were requested: a run built with the default
    `workbook,report` outputs is exactly the reproduction the second review
    used -- four rejections, nineteen payments, no marker on any of them
    and a Run total above what the bank was actually told to send."""
    from cfo.payments import render_fraud
    from cfo.payments.emit import csv_emit, pain001
    from cfo.payments.workbook import build_payments_workbook

    names = [n.strip() for n in (args.outputs or DEFAULT_OUTPUTS).split(",") if n.strip()]
    unknown = [n for n in names if n not in ALL_OUTPUTS]
    if unknown:
        raise ToolkitError([(n, f"is not one of {', '.join(ALL_OUTPUTS)}") for n in unknown])

    signed_off = signoff.is_signed_off(args.run)
    payment_outputs = [n for n in names if n in PAYMENT_OUTPUTS]
    if payment_outputs and not signed_off:
        # This is the product, not a safety rail around it (see the module
        # docstring): a person must have looked before either of these,
        # the two forms that actually move money, can exist on disk.
        raise ToolkitError([(n, "cannot be built before sign-off (`payments sign-off`); a "
                               "NOT-REVIEWED draft is available for 'workbook' and 'report' "
                               "instead") for n in payment_outputs])

    config = _require_state(args.run, "config", "start")
    batches = _deserialize_batches(_require_state(args.run, "batches", "review"))
    _assert_batches_match_signoff(args.run, batches)
    unreadable = _read_state(args.run, "unreadable", []) or []
    cost_categories = _read_state(args.run, "cost-categories", {}) or {}
    flags = [fraud.Flag(**row) for row in _read_state(args.run, "flags", []) or []]
    performed = _read_state(args.run, "performed", {}) or {}

    run = runs.load_run(args.run)
    slug = str(run.get("company_slug") or "").strip("-") or "payments"
    date_part = run.get("started_at", "")[:10]
    marker = "" if signed_off else "-NOT-REVIEWED"
    out_dir = os.path.join(args.run, "outputs")
    os.makedirs(out_dir, exist_ok=True)

    # N4: computed unconditionally, not only when pain001/csv were
    # requested -- the workbook (built by default, alongside the report)
    # needs to know which references were dropped too, even when no
    # payment instruction is being built at all this call.
    payment_batches, dropped_refs = _filter_rejected_payments(args.run, batches)

    files, warnings = {}, []
    # Nothing below is written under its real name until every requested
    # output has been built without raising -- see the docstring (B4).
    # `stage_dir` is inside `out_dir` itself, so the final `os.replace` is
    # always a same-filesystem rename, never a copy that could be caught
    # half-done.
    with tempfile.TemporaryDirectory(dir=out_dir) as stage_dir:
        staged = []

        def _stage(final_path):
            tmp_path = os.path.join(stage_dir, os.path.basename(final_path))
            staged.append((tmp_path, final_path))
            return tmp_path

        if "pain001" in names:
            debtor_name = args.debtor_name or config["debtor_name"]
            initiating_party = args.initiating_party or config["initiating_party"]
            debtor_bic = args.debtor_bic or config["debtor_bic"]
            paths = []
            for batch in payment_batches:
                data = pain001.emit(batch, debtor_name=debtor_name, debtor_bic=debtor_bic,
                                    initiating_party_name=initiating_party)
                final_path = os.path.join(
                    out_dir, f"{slug}-pain001-{batch.external_reference}-{date_part}.xml")
                with open(_stage(final_path), "wb") as fh:
                    fh.write(data)
                paths.append(final_path)
            files["pain001"] = paths
        if "csv" in names:
            final_path = os.path.join(out_dir, f"{slug}-payments-{date_part}.csv")
            lines = []
            for i, batch in enumerate(payment_batches):
                text = csv_emit.emit(batch)
                lines.append(text if i == 0 else "\n".join(text.splitlines()[1:]) + "\n")
            with open(_stage(final_path), "w", encoding="utf-8", newline="") as fh:
                fh.write("".join(lines) if payment_batches else csv_emit.emit(
                    model.Batch(debtor_account="", execution_date=datetime.date.today(),
                               sell_currency="", external_reference="", payments=[])))
            files["csv"] = final_path
        if dropped_refs:
            warnings.append(("payments", f"{len(dropped_refs)} payment(s) were excluded "
                             "because the exception raised against them was rejected: " +
                             ", ".join(sorted(dropped_refs))))
        if "workbook" in names:
            final_path = os.path.join(out_dir,
                                      f"{slug}-payments-workbook{marker}-{date_part}.xlsx")
            result = build_payments_workbook(_stage(final_path), args.run, batches, flags,
                                             performed, unreadable_documents=unreadable,
                                             cost_categories=cost_categories,
                                             home_currency=args.home_currency,
                                             dropped_references=dropped_refs)
            files["workbook"] = final_path
            warnings += result.get("_warnings", [])
        if "report" in names:
            meta = {"title": "Payments fraud report", "company": run.get("company_name", ""),
                   "date": date_part, "classification": "Internal"}
            control = signoff.control_block(args.run)
            final_path = os.path.join(out_dir, f"{slug}-fraud-report{marker}-{date_part}.docx")
            # `judgements` is [] until `payments judgement-collect` has run
            # for this run (see below) -- schema-valid on its own, and
            # render_fraud's own fallback text ("No assessment was returned
            # for this finding") says exactly that, honestly, rather than
            # this module inventing a verdict.
            judgements = _read_state(args.run, "judgements", []) or []
            # `run_dir` and `unreadable_documents` are not optional in
            # practice, whatever their defaults say. Without the first the
            # report shows every finding with no sign which the reviewer
            # refused, so a reader concludes all were approved; without the
            # second it states in as many words that nothing was unreadable,
            # which is a positive false claim when a document was. Both were
            # added to build_report and tested by calling it directly, and
            # this call site was never updated -- so the tests passed and the
            # filed document lied.
            result = render_fraud.build_report(flags, judgements, performed, control,
                                               _stage(final_path), meta,
                                               run_dir=args.run,
                                               unreadable_documents=unreadable)
            files["report"] = final_path
            warnings += result.get("_warnings", [])

        # Every requested output built without raising -- commit them all
        # now, atomically, to their real names.
        for tmp_path, final_path in staged:
            os.replace(tmp_path, final_path)

    # N6 (second review): a signed-off build supersedes any NOT-REVIEWED
    # draft this same run left behind from an earlier, pre-signoff build --
    # keeping both self-labels the draft, but a reader who opens the wrong
    # one of the two would still be reading it. Removed only for an output
    # this call actually rebuilt under its real name (never guessed at for
    # an output this call was not asked to build), and only once that real
    # file exists on disk under `staged` above.
    if signed_off:
        twins = {
            "workbook": os.path.join(
                out_dir, f"{slug}-payments-workbook-NOT-REVIEWED-{date_part}.xlsx"),
            "report": os.path.join(
                out_dir, f"{slug}-fraud-report-NOT-REVIEWED-{date_part}.docx"),
        }
        for name, twin_path in twins.items():
            if name in files and os.path.isfile(twin_path):
                os.remove(twin_path)

    return {"files": files, "signed_off": signed_off, "_warnings": warnings}


# --- cmd_judgement_input / cmd_judgement_collect: the one AI-task step
# `payments build`'s report reads a verdict from -- the same shape T17's
# `policy panel-input`/`policy panel-merge` use for their own single-task
# steps (see cmd_coverage_input/cmd_obligations_input's docstrings there),
# reused rather than reinvented for the one task this tool has that judges
# rather than extracts.

FRAUD_JUDGEMENT_TASK = "payments.fraud-judgement"


def _render_judgement_flags(flags):
    """The `flags` input `assets/tasks/payments/fraud-judgement/prompt.md`
    reads: one block per flag, every field the prompt names (`check`,
    `where`, `summary`, `compared`, `source`) plus `severity` (shown, but
    the prompt is explicit that severity alone is never a reason to wave a
    flag through) -- never the invoices or the supplier master themselves,
    which `cfo.payments.fraud`'s own module docstring already establishes
    the model is not shown."""
    if not flags:
        return "No fraud flags were raised for this batch.\n"
    blocks = []
    for i, flag in enumerate(flags, start=1):
        compared = ", ".join(f"{k}={v}" for k, v in sorted(flag.compared.items()))
        blocks.append(
            f"## Flag {i}\n"
            f"- check: {flag.check}\n"
            f"- severity: {flag.severity}\n"
            f"- where: {flag.where}\n"
            f"- summary: {flag.summary}\n"
            f"- compared: {compared}\n"
            f"- source: {flag.source}\n")
    return "\n".join(blocks)


def _render_judgement_batch_summary(invoices):
    """The `batch_summary` input: enough for a reader to place the flags in
    context (how many invoices, in what currencies, over what span) --
    never a bank detail, an IBAN or anything else the judgement task has no
    business seeing."""
    currencies = sorted({str(inv.get("currency") or "").strip() for inv in invoices
                        if str(inv.get("currency") or "").strip()})
    suppliers = sorted({str(inv.get("supplier") or "").strip() for inv in invoices
                       if str(inv.get("supplier") or "").strip()})
    dates = sorted(inv["invoice_date"] for inv in invoices if inv.get("invoice_date"))
    date_range = (f"{dates[0].isoformat()} to {dates[-1].isoformat()}" if dates
                 else "not stated on any invoice in this batch")
    return (f"Invoices in this batch: {len(invoices)}\n"
           f"Distinct suppliers: {len(suppliers)}\n"
           f"Currencies: {', '.join(currencies) or 'none stated'}\n"
           f"Invoice date range: {date_range}\n")


def cmd_judgement_input(args):
    """Writes the fraud-judgement task's two declared inputs (see
    `assets/tasks/payments/fraud-judgement/task.json`) as separate files,
    since `task prepare` keys an input purely off its declared name -- the
    same reason `cmd_coverage_input` writes `policy_batch` and `clause_list`
    separately. The skill runs, once per run: `task prepare --task
    .../fraud-judgement --input flags=<flags> --input
    batch_summary=<batch_summary>`, then `task accept`, then `payments
    judgement-collect`."""
    flags = [fraud.Flag(**row) for row in _require_state(args.run, "flags", "check")]
    invoices = [_deserialize_invoice(inv) for inv in _require_state(args.run, "checked", "check")]
    folder = _payments_dir(args.run)
    flags_path = os.path.join(folder, "judgement-flags.md")
    with open(flags_path, "w", encoding="utf-8") as fh:
        fh.write(_render_judgement_flags(flags))
    summary_path = os.path.join(folder, "judgement-batch-summary.md")
    with open(summary_path, "w", encoding="utf-8") as fh:
        fh.write(_render_judgement_batch_summary(invoices))
    return {"flags": flags_path, "batch_summary": summary_path, "flag_count": len(flags)}


def cmd_judgement_collect(args):
    """Records the fraud-judgement task's accepted output as this run's
    `judgements.json`, the state `cmd_build` reads instead of `[]` once
    this has run -- the collect half of `cmd_judgement_input`, the same way
    `payments collect` is the collect half of `payments extract-input` for
    the invoice-fields task."""
    accepted = read_json(os.path.join(task_work_dir(args.run, FRAUD_JUDGEMENT_TASK),
                                      "accepted.json"))
    if accepted is None:
        raise ToolkitError((FRAUD_JUDGEMENT_TASK, "has no accepted output yet: run `task accept` "
                                                   "for this task first"))
    judgements = accepted.get("judgements", [])
    _write_state(args.run, "judgements", judgements)
    return {"judgements": len(judgements)}


# --- cmd_red_team: the one AI-task step this CLI exposes directly, because
# the brief gives it its own command rather than folding it into build. ---

def cmd_red_team(args):
    """Prepares the red-team task's one input (`controls`, a concatenation
    of every document under `--controls`) the first time this is called for
    a run; once that task has been accepted, builds the report from its
    routes instead. The skill runs `task prepare`/`task accept` itself, the
    same way it does for every other AI step in this tool -- this command
    never calls either."""
    from cfo.payments import render_redteam

    accepted = read_json(os.path.join(task_work_dir(args.run, RED_TEAM_TASK), "accepted.json"))
    if accepted is not None:
        run = runs.load_run(args.run)
        material = _read_state(args.run, "redteam-material", {}) or {}
        meta = {"title": "Payments red-team review", "company": run.get("company_name", ""),
               "date": run.get("started_at", "")[:10], "classification": "Internal",
               "material": material}
        out_dir = os.path.join(args.run, "outputs")
        os.makedirs(out_dir, exist_ok=True)
        slug = str(run.get("company_slug") or "").strip("-") or "payments"
        path = os.path.join(out_dir, f"{slug}-red-team-{meta['date']}.docx")
        result = render_redteam.build_report(accepted.get("routes", []), path, meta)
        return {"report": result["document"], "routes": result["routes"]}

    if not args.controls or not os.path.isdir(args.controls):
        raise ToolkitError(("--controls", "a folder of control documents is needed the first "
                                          "time this is run for a run (an approval matrix, "
                                          "payment mandates, a supplier-change log, the company "
                                          "profile -- whichever of these exist)"))
    labels = {"approval-matrix": "approval matrix", "mandate": "payment mandates",
             "supplier-change": "supplier-change log", "profile": "company profile"}
    material, parts = {label: False for label in labels.values()}, []
    for name in sorted(os.listdir(args.controls)):
        path = os.path.join(args.controls, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
        parts.append(f"## {name}\n\n{text.strip()}")
        for key, label in labels.items():
            if key in name.lower():
                material[label] = True
    _write_state(args.run, "redteam-material", material)
    path = os.path.join(_payments_dir(args.run), "controls.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(parts))
    return {"input": path, "material": material}


COMMANDS = {"start": cmd_start, "extract-input": cmd_extract_input, "collect": cmd_collect,
            "mark-failed": cmd_mark_failed, "check": cmd_check, "review": cmd_review,
            "disposition": cmd_disposition, "sign-off": cmd_sign_off, "build": cmd_build,
            "red-team": cmd_red_team, "judgement-input": cmd_judgement_input,
            "judgement-collect": cmd_judgement_collect}


def dispatch(args):
    return COMMANDS[args.payments_command](args)
