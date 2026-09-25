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

**D2/D3 (2026-09-22 real run):** `cmd_check`/`cmd_review` also compare
every invoice's own `bill_to` (T15, T5 review) against the debtor name the
run was started with, and against the invoice's own `supplier_name` --
`_payer_mismatch`/`_supplier_identity_finding`, both built on
`suppliers.same_party` -- because a real run named Hedj as `--debtor-name`
while both real invoices were billed to Booterstown United F.C. and the
tool said nothing, and separately recorded that same customer as the
*supplier* on an invoice whose real supplier's name lives only inside a
logo image. Both warn, loudly, and never refuse: a parent company paying a
subsidiary's invoice, or a sole trader invoicing under a name close to
their own customer's, are real and legitimate, and this only ever asks a
person to look, never decides for them.

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
import csv
import dataclasses
import datetime
import email.utils
import os
import re
import tempfile
import types
from decimal import Decimal, InvalidOperation

from cfo import extract as extract_mod
from cfo import runs
from cfo.console import ToolkitError
from cfo.io import locked, now_iso, read_json, sha256_bytes, write_json_atomic
from cfo.payments import (amend, approvals, batch as batch_mod, fraud, model, signoff, suppliers,
                          validate)
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


def _payer_mismatch(invoice, debtor_name):
    """`(where, message)`, or `None` -- D2 (2026-09-22 real run): this
    invoice's own `bill_to` party against the debtor name the run was
    started with. A real run named Hedj as `--debtor-name` while both real
    invoices were billed to Booterstown United F.C.; the run recorded Hedj
    as the payer and said nothing at all -- no payment file was actually
    built that time, but on a real run the money would have left the wrong
    company's account.

    **Warns, never refuses** (the controller's own ruling): a parent
    company legitimately pays a subsidiary's invoices, and a tool that
    refused that would be wrong more often than it was right. `None` -- no
    warning -- for an exact-or-near-exact match (`suppliers.same_party`,
    reused rather than a second normalisation written here), and `None`
    just as much when either side is blank: a `bill_to` this invoice never
    stated is a real, reportable absence in its own right (most invoices
    will report one until this brand-new field has had a live-model
    verification run against real samples), not a mismatch to guess at,
    and a blank debtor name is `payments start`'s own gap, not this
    invoice's."""
    bill_to = str(invoice.get("bill_to") or "").strip()
    debtor_name = str(debtor_name or "").strip()
    if not bill_to or not debtor_name:
        return None
    if suppliers.same_party(bill_to, debtor_name):
        return None
    return (_where(invoice),
            f"this invoice is billed to {bill_to!r}, but this run's own debtor is "
            f"{debtor_name!r} -- they do not appear to be the same party, and paying it would "
            "move money from a different company's account unless that is deliberate (a "
            "parent company paying a subsidiary's invoice, say)")


def _supplier_identity_finding(invoice):
    """`(where, message)`, or `None` -- D3 (2026-09-22 real run): the real
    defect that recorded Booterstown United F.C. -- the *customer*, off its
    own `bill_to` line -- as the *supplier* on an invoice whose actual
    supplier's name appears only inside a logo image, never in the text
    layer a model is ever shown. This never tries to recover the real
    name; there is no OCR here, and guessing one would be worse than
    reporting nothing.

    Two different findings, never both at once for one invoice: no
    `supplier_name` extracted at all (checked first -- a blank name is the
    more urgent, more general gap: nothing at all confirms who is being
    paid, whether or not a `bill_to` exists to compare it against), or a
    `supplier_name` that names the same party as this invoice's own
    `bill_to` (`suppliers.same_party`, the same comparison `_payer_mismatch`
    above uses, reused rather than reinvented). **Warns, never refuses**
    (a sole trader can legitimately invoice under a name close to their own
    customer's), and `None` for an ordinary invoice where neither is true."""
    supplier = str(invoice.get("supplier") or "").strip()
    if not supplier:
        return (_where(invoice), "no supplier name could be extracted from this invoice at all; "
                                 "nothing here has confirmed who is actually being paid")
    bill_to = str(invoice.get("bill_to") or "").strip()
    if bill_to and suppliers.same_party(supplier, bill_to):
        return (_where(invoice),
                f"the supplier recorded for this invoice ({supplier!r}) is the same party as "
                f"its own bill-to ({bill_to!r}); the real supplier's name may not have been "
                "read at all -- see the invoice's own text for where it might actually be")
    return None


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
    # D2/D3 (2026-09-22 real run): a wrong-payer or wrong-supplier finding
    # is not a validate.py or fraud.py check (neither reads `bill_to`) and
    # is not `fraud.checks_performed`'s business either -- folded straight
    # into `_warnings` here, the same way every other per-invoice finding
    # in this command already is, so `console.print_problems` shows it
    # exactly like any other warning, loudly, without a person having to
    # know a fourth place to look.
    payer_mismatches = [f for f in (_payer_mismatch(inv, config["debtor_name"])
                                    for inv in invoices) if f]
    supplier_identity_findings = [f for f in (_supplier_identity_finding(inv)
                                              for inv in invoices) if f]
    result_warnings += payer_mismatches
    result_warnings += supplier_identity_findings
    # B1 (was D5, 2026-09-22 real run): both findings above compare a
    # `bill_to` against something else, so when NO invoice in the batch
    # states one at all, neither comparison has anything to run against --
    # the payer check does not run, and without this, the run reports
    # nothing, which is the exact failure the payer check exists to
    # prevent, one level up (see this class's own docstring). A per-invoice
    # blank `bill_to` alongside others that ARE populated is not this: the
    # check ran, it just had nothing to say about that one invoice.
    if not any(str(inv.get("bill_to") or "").strip() for inv in invoices):
        no_bill_to_reason = ("no invoice in this batch stated a bill-to party, so it could not "
                             "be compared against anything")
        not_performed = not_performed + ["payer_mismatch", "supplier_identity"]
        result_warnings += [("payer_mismatch", no_bill_to_reason),
                            ("supplier_identity", no_bill_to_reason)]
    # D4 (2026-09-22 real run): money-relevant warnings, so
    # console.print_problems never truncates them away regardless of how
    # many ordinary (marked-failed, not-performed) warnings arrived first
    # -- the payer mismatch and supplier-identity findings above, plus
    # validate.py's own amount-relevant warnings (an early-settlement
    # discount, a near-duplicate invoice raising the same amount twice).
    important_warnings = warnings + payer_mismatches + supplier_identity_findings
    return {"invoices": len(invoices), "missing": missing, "failed": failed_display,
            "errors": len(errors), "warnings": len(warnings),
            "flags": len(flags), "flags_by_check": {cid: sum(f.check == cid for f in flags)
                                                     for cid in fraud.CHECK_IDS},
            "not_performed": not_performed, "_warnings": result_warnings,
            "_important": important_warnings}


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


def _collapse_value_date_warnings(warnings, execution_date, limit=3):
    """One line per overdue invoice reads as noise once most of a run is
    overdue (every sample invoice was), and pushes warnings that matter off
    a truncated console. Past `limit`, say it once and name them all."""
    if len(warnings) <= limit:
        return warnings
    names = ", ".join(where for where, _ in warnings)
    return [("batch", f"{len(warnings)} invoices have a value date before this batch's execution "
                      f"date {execution_date.isoformat()} and will be paid on the execution date: "
                      f"{names}")]


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
    # B3 (F7): `amendments.json` and `draft.json` are newer than this
    # function and it never learned about either. Left alone, an
    # exclusion recorded before this reset stayed excluded after it --
    # silently, on the authority of a review the reviewer had just
    # explicitly discarded -- and a stale draft kept pointing a later
    # sign-off at a document this run no longer stands behind. Cleared
    # under the exact same guard as everything above: only reachable once
    # nothing has actually been decided yet.
    _write_state(run_dir, amend.AMENDMENTS_NAME, None)
    _write_state(run_dir, "draft", None)


def cmd_review(args):
    """Opens sign-off for this run (once; a second call just reports what
    is still outstanding, like `policy panel-merge` re-run). Every fraud
    flag, every unreadable document, every invoice explicitly marked
    failed, every invoice this run could not resolve bank details for, and
    (D2/D3, 2026-09-22 real run) every invoice whose bill-to party
    disagrees with the run's own debtor or with its own recorded supplier
    becomes one exception; `signoff.start_review`
    assigns each an id, and
    `exception-meta.json` remembers, per id, enough to act on an
    *accepted* one later (see `cmd_disposition`) -- and, for every kind,
    `payment_references` (a list) naming every payment it would drop if
    *rejected* instead (see B1, in `amend.instructed_batches`, called
    from `cmd_build`): `[]` for a kind that never had a payment to begin
    with, one element for the ordinary single-invoice case, and more
    than one for a fraud flag that reasons across several invoices at
    once (D7 -- `fraud.Flag.content_doc_ids`). `payment_reference`
    (singular) is kept alongside it for a caller with no reason to read
    a list, but is never a second, independently-set value: it is always
    `payment_references[0]`, or `None` when the list is empty.

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
                                    "payment_reference": None, "payment_references": []})

    # **N1 -- the guard the second review asks for, "whatever you choose":**
    # refuse to open a review, naming the offenders, rather than silently
    # resolve either of the two shapes that made B1's fix reopenable.
    #
    # (a) two payments that literally share a `reference` -- the milder
    # variant reproduced in the second review: `amend.instructed_batches`
    # (called from cmd_build) matches a rejected exception's payment_reference
    # against EVERY payment carrying it, so a genuine reference collision drops both
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
        meta.append({"kind": "unreadable", "where": where, "payment_reference": None,
                    "payment_references": []})

    for unit in readable:
        if unit["content_doc_id"] in failed and unit["content_doc_id"] not in results:
            where = unit["display_file"]
            exceptions.append((where, f"marked failed: {failed[unit['content_doc_id']]['reason']}"
                               " -- it will not be included in this payment run"))
            meta.append({"kind": "failed_invoice", "where": where, "payment_reference": None,
                        "payment_references": []})

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
        ref = where_to_reference.get(where)
        meta.append({"kind": "validate_error", "where": where,
                    "payment_reference": ref, "payment_references": [ref] if ref else []})

    for flag in flags:
        exceptions.append((flag.where, f"[{flag.check}] {flag.summary}"))
        change = (bank_changes.get(flag.content_doc_id)
                 if flag.check == "changed_bank_details" else None)
        # Only the four keys `apply_approved_change` actually reads --
        # `last_seen` is a `date`, which plain `json.dumps` cannot write,
        # and nothing here needs it back.
        bank_change = ({"field": change["field"], "old": change["old"], "new": change["new"],
                       "supplier_id": change["supplier_id"]} if change else None)
        # D7 (.superpowers/sdd/2026-09-22-payments-staged-output/defect-d-
        # brief.md): a flag's own payment_references comes from EVERY
        # content_doc_id it carries -- `flag.content_doc_ids` for a
        # multi-invoice finding (split_to_stay_under,
        # near_duplicate_invoice, sequential_invoice_numbers, and
        # lookalike_sender_domain's own batch-pair comparison), or just
        # `flag.content_doc_id` alone for the ordinary single-invoice
        # case -- never from `where_to_reference`, which two invoices
        # sharing a number could collapse to the wrong one (see the
        # module's own docstring and the second review's N1). Before this,
        # a Flag carried only one content_doc_id no matter how many
        # invoices its own summary named, so a finding about several
        # invoices could only ever drop one of them, an artefact of
        # iteration order -- the partial version of B1's own defect,
        # wearing a hat: rejecting it looked like it worked, because
        # *one* payment did disappear.
        #
        # `payment_references` is the canonical list -- every id that
        # resolves to an actual payment (a content_doc_id with no
        # matching payment, e.g. one whose bank details never resolved,
        # contributes nothing: there is no payment to drop), in order,
        # with no duplicate. `payment_reference` is never a second,
        # independently-derived value: it is this same list's own first
        # entry, or `None` when the list is empty -- one representation,
        # not two that can disagree. A flag built with no content_doc_id
        # at all (a test fixture, mostly -- see `fraud.Flag`'s own
        # default) simply gets an empty list, the same honest answer an
        # unresolved invoice already gets.
        doc_ids = flag.content_doc_ids or ((flag.content_doc_id,) if flag.content_doc_id else ())
        seen_refs, payment_references = set(), []
        for doc_id in doc_ids:
            ref = content_doc_id_to_reference.get(doc_id)
            if ref and ref not in seen_refs:
                seen_refs.add(ref)
                payment_references.append(ref)
        meta.append({"kind": "flag", "check": flag.check, "where": flag.where,
                    "bank_change": bank_change,
                    "payment_reference": payment_references[0] if payment_references else None,
                    "payment_references": payment_references})

    # D2/D3 (2026-09-22 real run): a wrong-payer or wrong-supplier finding
    # gets an exception here exactly like every other kind above -- a
    # disposition and an audit-trail entry, never a silent drop and never
    # a silent keep either (the controller's own ruling: warn, never
    # refuse). `payment_reference`/`payment_references` come from
    # `content_doc_id_to_reference`, the same map a fraud flag's own
    # exception above uses, so rejecting either of these still drops the
    # payment it belongs to (B1's own `_filter_rejected_payments`,
    # unchanged, reads `payment_references` generically and has no
    # notion of "kind" to special-case here). Both findings are always
    # single-invoice -- one invoice's own bill-to or supplier identity --
    # so `payment_references` is always at most one element.
    for invoice in invoices:
        reference = content_doc_id_to_reference.get(str(invoice.get("content_doc_id") or ""))
        refs = [reference] if reference else []
        payer_finding = _payer_mismatch(invoice, config["debtor_name"])
        if payer_finding is not None:
            exceptions.append(payer_finding)
            meta.append({"kind": "payer_mismatch", "where": payer_finding[0],
                        "payment_reference": reference, "payment_references": refs})
        supplier_finding = _supplier_identity_finding(invoice)
        if supplier_finding is not None:
            exceptions.append(supplier_finding)
            meta.append({"kind": "supplier_identity", "where": supplier_finding[0],
                        "payment_reference": reference, "payment_references": refs})

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
            "control_total": str(split.control_total),
            "_warnings": _collapse_value_date_warnings(value_date_warnings, execution_date)}


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


# --- cmd_draft: the NOT-REVIEWED workbook, staged as its own named
# command, and the record `cmd_sign_off` (below) checks a draft against.
#
# `_draft_is_current` and the message helpers beneath it are module-level
# on purpose, not logic buried inside `cmd_sign_off` -- Task 5's `payments
# final` refuses for these same two reasons and is told to call this
# helper and reference these constants rather than re-derive either. Two
# independently typed copies of a sentence drift, and these sentences are
# the product (global constraint 3: "sign-off attests to a draft the
# reviewer saw" -- this task is where that becomes true).
#
# `cmd_amend` (below) has its own, separate sign-off refusal --
# `signoff.is_signed_off` alone is what it needs (constraint 3's other
# half: amending after sign-off is refused outright, there being no
# draft-staleness question at amend time, only at sign-off time).

_NO_DRAFT_MESSAGE = ("no draft has been produced for this run -- run `payments draft` and "
                     "look at it first")

# C1/F10: `draft.json` records `path`, but nothing used to stat it -- a
# draft workbook deleted after review still let sign-off succeed,
# recording an attestation that points at evidence no longer on disk.
# Reusing `_NO_DRAFT_MESSAGE` verbatim, plus a sentence naming what is
# actually missing, rather than a second, differently-worded refusal:
# from the reviewer's side, "the draft you looked at is gone" and "no
# draft was ever made" call for the exact same next step (`payments
# draft`), so they should read as the same problem, not two.
_DRAFT_FILE_GONE_MESSAGE = (_NO_DRAFT_MESSAGE + " -- the draft workbook this run recorded is "
                           "gone from disk")


def _assert_draft_file_present(where, draft):
    """Shared by `cmd_sign_off` and `cmd_final` (C1/F10), the same two
    call sites that already share `_NO_DRAFT_MESSAGE` and
    `_stale_draft_message` -- see the module comment above `_draft_is_
    current`. Refuses the moment `draft["path"]` no longer names a real
    file: recording a sign-off against a workbook that was deleted is a
    weaker claim than this tool makes for itself everywhere else."""
    path = draft.get("path")
    if not path or not os.path.isfile(path):
        raise ToolkitError((where, _DRAFT_FILE_GONE_MESSAGE))


def _stale_draft_message(amendments_since):
    """The `payments sign-off` refusal text for a stale draft (handed
    over by Task 2, fixed here as part of Task 3 -- `payments amend`
    is what makes the mixed case testable at all).

    Staleness itself is decided by `_draft_is_current`'s digest
    comparison, never by this count -- so a draft can go stale with
    *zero* amendments recorded (an exception was re-dispositioned
    instead, which changes `amend.instructed_batches`'s own dropped set
    with no `amend.record` call anywhere). The old, single template
    printed "0 amendment(s) since" in exactly that case, which told the
    reader nothing and read like a bug in the tool rather than a fact
    about their run. This never prints a zero count: when amendments
    explain the staleness, it says how many; when they do not (the only
    other way `_draft_is_current` can go false, today, is a
    re-disposition or a batches.json that has drifted out from under a
    recorded amendment -- see `_draft_is_current`'s own C2/F8 fix), it
    says the payments changed without inventing a number."""
    if amendments_since > 0:
        return (f"the payments changed after the draft you are signing: {amendments_since} "
                "amendment(s) since -- run `payments draft` again")
    return ("the payments changed after the draft you are signing -- an exception was "
            "re-dispositioned since, not an amendment -- run `payments draft` again")


def _draft_is_current(run_dir):
    """True only when this run has a draft (`draft.json`, written by
    `cmd_draft`) AND that draft's own `lines_digest` still matches
    `amend.lines_digest`'s current answer for this run's own
    `batches.json` -- i.e. nothing that would change what an instructed
    payment actually contains (a disposition, an amendment) has happened
    since the draft was taken. False for a run with no draft at all, or
    no `batches.json` yet (review never opened). Never raises -- a plain
    question, safe to ask at any point in the flow, the same way
    `signoff.is_signed_off` is.

    **C2/F8:** `amend.lines_digest` (via `amend.instructed_batches`)
    raises `ToolkitError` when a reference either exclusion source
    believes it dropped cannot actually be found in `batches.json` --
    e.g. an amendment recorded against a reference a since-rewritten
    `batches.json` no longer carries. That is caught here and treated as
    "not current", never re-raised: `cmd_sign_off` and `cmd_final` were
    both told to call this helper instead of re-deriving the check, on
    the strength of the docstring sentence above, and a run whose
    batches no longer contain a reference this run's own records say it
    dropped is exactly a run whose draft should not be treated as
    current."""
    draft = _read_state(run_dir, "draft")
    if draft is None:
        return False
    batches_data = _read_state(run_dir, "batches")
    if batches_data is None:
        return False
    batches = _deserialize_batches(batches_data)
    try:
        digest = amend.lines_digest(run_dir, batches)
    except ToolkitError:
        return False
    return draft.get("lines_digest") == digest


def cmd_draft(args):
    """Builds the NOT-REVIEWED workbook -- the exact call `payments build
    --outputs workbook` makes (`cmd_build`, called here directly with a
    synthetic `outputs="workbook"` args object, rather than a second
    workbook builder), same filename, same content, Exceptions sheet and
    all: a draft showing only payment lines would hide what the reviewer
    is there to look at (spec criterion 2).

    Records enough in `draft.json` (`_write_state`, at
    `<run>/payments/draft.json`, like every other state file in this
    tool -- replaced in full on every call, never appended: a person can
    run this any number of times, and only the latest matters) for
    `cmd_sign_off` to tell whether the draft a reviewer looked at is
    still the thing they would be attesting to: its `lines_digest`
    (`amend.lines_digest`, over the *instructed* lines -- after both
    rejected exceptions and amendment exclusions), and the amendment
    count at the time this draft was taken, so a later staleness check
    can say how many amendments happened since.

    **F1 (controller finding on Tasks 1-2):** reports *both* the reviewed
    shape (`signoff.control_block`'s own payment count/control total --
    what `payments review` originally opened sign-off against, before any
    exclusion) and the instructed shape (`amend.instructed_batches`'s own
    count/total -- what will actually be sent), each clearly labelled as
    `reviewed_*`/`instructed_*`. Before this fix, only the reviewed number
    was returned while the digest underneath it was already over the
    instructed lines -- a reviewer who had excluded a payment was told
    "2 payments, GBP 3,200.00" when only one, GBP 1,200.00, would actually
    be instructed. Mirrors the workbook's own "Run total" vs "Run total
    (instructed)" convention (`cfo.payments.workbook._payments_sheet`):
    report both sides of an exclusion decision, never silently swap one
    number for the other.

    Refuses once the run is signed off: at that point there is nothing
    left to draft against -- `payments final` is the command for a
    signed-off run's real output.
    """
    if signoff.is_signed_off(args.run):
        raise ToolkitError(("draft", "this run is already signed off -- there is nothing "
                                     "left to draft; run `payments final` instead"))

    workbook_args = types.SimpleNamespace(run=args.run, outputs="workbook", debtor_name=None,
                                          debtor_bic=None, initiating_party=None,
                                          home_currency=None)
    built = cmd_build(workbook_args)
    path = built["files"]["workbook"]

    batches = _deserialize_batches(_require_state(args.run, "batches", "review"))
    control = signoff.control_block(args.run)
    digest = amend.lines_digest(args.run, batches)
    exceptions_outstanding = len(signoff.outstanding(args.run))

    payment_batches, _dropped_refs = amend.instructed_batches(args.run, batches)
    instructed_line_count = sum(b.count for b in payment_batches)
    instructed_control_total = sum((b.control_total for b in payment_batches), Decimal("0"))

    result = {
        "path": path,
        "reviewed_line_count": control["payment_count"],
        "reviewed_control_total": str(control["control_total"]),
        "instructed_line_count": instructed_line_count,
        "instructed_control_total": str(instructed_control_total),
        "exceptions_outstanding": exceptions_outstanding,
    }
    # B4 (F11): the plan's own shape for draft.json is `{built_at, path,
    # line_count, control_total, lines_digest}` -- every field but
    # `built_at` was written. Without it, nothing can answer "when was
    # the draft this person signed off actually produced?", the first
    # question anyone auditing a payment run asks.
    _write_state(args.run, "draft", dict(result, built_at=now_iso(), lines_digest=digest,
                                        amendment_count=len(amend.amendments(args.run))))
    return result


# --- cmd_amend: exclude a payment, restore a mistaken exclusion, or
# rewrite a payment's remittance text -- before sign-off only. Task 3 of
# the staged-output milestone.
#
# `--beneficiary`/`--account`/`--iban`/`--amount` are defined flags that
# refuse, not undefined flags argparse would reject with an
# "unrecognized arguments" message that teaches nobody anything (global
# constraint 2: an amendment never touches the beneficiary, the bank
# details or the amount -- who gets paid, how much and into which
# account are exactly what invoice fraud targets, and a sentence typed
# into a chat window is the weakest authorisation there is).

_AMEND_REFUSED_FLAGS = ("beneficiary", "account", "iban", "amount")
_AMEND_REFUSAL_MESSAGE = (
    "Who gets paid, how much, and into which account are the three things invoice "
    "fraud targets. If one of them is wrong on this payment, the invoice is wrong -- "
    "correct the invoice and run the batch again."
)

# --- `--value-date`: the batch-boundary decision. Task 4 of the
# staged-output milestone. See docs/specs/2026-09-22-payments-staged-
# output-design.md, "The value date that crosses a batch boundary" -- the
# authority for the three choices and their effect wording below; do not
# reword either sentence (the skill reads them out to a reviewer
# verbatim). Each `effect` string is that section's own "what happens"
# and "what it costs" cells for one choice, joined into the single field
# the decision payload's own JSON shape carries -- quoted, not composed.

_VALUE_DATE_DECISION = "value-date-outside-batch"

_KEEP_IN_BATCH_EFFECT = (
    "The date is recorded on the line; the bank still pays on the batch's execution date. "
    "The date you typed is not the date it is paid."
)
_KEEP_IN_BATCH_UNAVAILABLE_REASON = (
    "refused outright when the new date is earlier than the execution date, because that "
    "would be paying late while recording otherwise"
)
_OWN_BATCH_EFFECT = (
    "A second batch dated to the new date, with its own reference and its own pain.001 file. "
    "One more file for the bank, and the batch count changes, so the review record is "
    "restated."
)
_HOLD_BACK_EFFECT = "Excluded from this run, to be paid separately. It is not paid this run."


def _value_date_choices(new_value_date, execution_date):
    """The three-choice list the decision payload carries, and the list
    `--resolve <id>` is checked against. `keep-in-batch` is the only one
    that can be unavailable -- when the new date is *earlier* than the
    batch's own execution date, recording it would claim a date earlier
    than the one the bank will actually pay on (`model.validate_batch`
    already refuses exactly that value_date/execution_date relationship
    inside a batch). `own-batch` and `hold-back` never depend on the
    direction of the change: a batch dated to any value date validates on
    its own, and excluding a payment has no notion of "too early"."""
    keep_available = new_value_date >= execution_date
    return [
        {"id": amend.RESOLUTION_KEEP_IN_BATCH, "effect": _KEEP_IN_BATCH_EFFECT,
         "available": keep_available,
         "unavailable_reason": None if keep_available else _KEEP_IN_BATCH_UNAVAILABLE_REASON},
        {"id": amend.RESOLUTION_OWN_BATCH, "effect": _OWN_BATCH_EFFECT, "available": True,
         "unavailable_reason": None},
        {"id": amend.RESOLUTION_HOLD_BACK, "effect": _HOLD_BACK_EFFECT, "available": True,
         "unavailable_reason": None},
    ]


_VALUE_DATE_RESOLUTIONS = (amend.RESOLUTION_KEEP_IN_BATCH, amend.RESOLUTION_OWN_BATCH,
                           amend.RESOLUTION_HOLD_BACK)


def _payment_field(run_dir, reference, field):
    """The current value of `field` on the payment named `reference`,
    read straight from this run's own `batches.json` -- `None` if the
    reference is not one of this run's own payments. Only ever used to
    fill in an amendment's own `from_value` for the audit trail; `amend.
    record` (called separately, always) is what actually refuses an
    unknown reference, naming it -- this never raises."""
    data = _read_state(run_dir, "batches", []) or []
    for batch in data:
        for payment in batch.get("payments", []):
            if payment.get("reference") == reference:
                return payment.get(field)
    return None


def _write_remittance(run_dir, reference, remittance):
    """The one place `payments amend --remittance` ever writes: rewrites
    the named payment's own `remittance` field, in place, in this run's
    `batches.json` -- the same file `cmd_build` reads to emit pain001 and
    csv. Never touches `reference` (the join key `exception-meta.json`,
    `dropped_refs` and the draft's own `lines_digest` all rely on -- see
    `cfo.payments.model.Payment`'s own docstring on the split this task
    made and why) or any other field on the payment. Locked, the same
    read-modify-write every other state file in this tool uses, so a
    concurrent amend on the same run can't interleave and lose one.

    Called only after `amend.record` has already accepted the amendment
    (see `cmd_amend`): the audit-trail entry is written first, so this
    call is never the only trace of a change that happened."""
    path = _state_path(run_dir, "batches")
    with locked(path):
        data = read_json(path, []) or []
        for batch in data:
            for payment in batch.get("payments", []):
                if payment.get("reference") == reference:
                    payment["remittance"] = remittance
        write_json_atomic(path, data)


def _write_value_date(run_dir, reference, value_date):
    """The one place a `--value-date` amendment that actually applies
    (the equal-date case, or a `keep-in-batch` resolution) ever writes:
    rewrites the named payment's own `value_date` field, in place, in
    this run's `batches.json` -- never the batch's own `execution_date`
    (that would be the false record `keep-in-batch` exists to avoid), and
    never any other field on the payment. Locked, the same read-modify-
    write every other state file in this tool uses -- see
    `_write_remittance`, which this mirrors."""
    path = _state_path(run_dir, "batches")
    with locked(path):
        data = read_json(path, []) or []
        for batch in data:
            for payment in batch.get("payments", []):
                if payment.get("reference") == reference:
                    payment["value_date"] = value_date.isoformat()
        write_json_atomic(path, data)


def _locate_payment_batch(batches, reference):
    """`(batch_index, payment_index)` for the payment named `reference`
    among `batches` (a list of `model.Batch`, already deserialized) --
    refused, naming the reference, when none of them carries it. This is
    a second, independent lookup from `amend.record`'s own refusal (which
    checks the same `batches.json`, read as plain JSON): `--value-date`
    needs the actual batch object -- its `execution_date`, to decide
    whether the new date crosses it -- before it can decide whether to
    call `amend.record` at all, so it cannot wait for that function's own
    check to fire."""
    for batch_index, batch in enumerate(batches):
        for payment_index, payment in enumerate(batch.payments):
            if payment.reference == reference:
                return batch_index, payment_index
    raise ToolkitError(("--reference", f"{reference!r} is not a payment reference in this "
                        "run's own batches.json -- run `payments review` first, or check the "
                        "reference against this run's own outstanding payments"))


def _resolve_own_batch(args, batches, batch_index, payment_index, new_value_date):
    """`own-batch`: a new `Batch` dated to `new_value_date`, holding only
    the one payment being moved, with its own external reference
    continuing this run's existing sequence (`batch_mod.
    next_external_reference` -- never a reference `build_batches` could
    also have produced, or an earlier `own-batch` call already did: see
    that function's own docstring on why a collision there would be
    worse than cosmetic). Run through `model.validate_batch` before it is
    kept, the same guarantee `build_batches` itself already gives every
    batch it produces. An emptied source batch (the moved payment was its
    only one) is dropped from `batches.json` entirely -- the same thing
    `amend.instructed_batches` already does for its own, filtered view.

    Changes the batch count, so the run's own recorded review shape would
    disagree with `batches.json` the moment this is written --
    `signoff.restate` is called last, after the new shape is already on
    disk, so it recomputes from exactly what a later rebuild would see.
    (`cmd_amend` has already refused if this run is signed off, before
    any of this runs -- `restate` also refuses on its own, independently,
    the one guard that must never be bypassed.)
    """
    config = _require_state(args.run, "config", "start")
    source_batch = batches[batch_index]
    moved_payment = source_batch.payments[payment_index]
    remaining_payments = [p for i, p in enumerate(source_batch.payments) if i != payment_index]

    new_external_reference = batch_mod.next_external_reference(batches,
                                                                config["reference_prefix"])
    new_batch = model.Batch(debtor_account=source_batch.debtor_account,
                            execution_date=new_value_date, sell_currency=source_batch.sell_currency,
                            external_reference=new_external_reference,
                            payments=[dataclasses.replace(moved_payment,
                                                          value_date=new_value_date)])
    problems = model.validate_batch(new_batch)
    if problems:
        raise ToolkitError(problems)

    new_batches = list(batches)
    if remaining_payments:
        new_batches[batch_index] = dataclasses.replace(source_batch, payments=remaining_payments)
    else:
        new_batches.pop(batch_index)  # an emptied source batch is dropped
    new_batches.append(new_batch)

    entry = amend.record(
        args.run, reference=moved_payment.reference, change=amend.VALUE_DATE,
        from_value=moved_payment.value_date.isoformat() if moved_payment.value_date else None,
        to_value=new_value_date.isoformat(), resolution=amend.RESOLUTION_OWN_BATCH,
        reason=args.reason, by=args.by)
    _write_state(args.run, "batches", _serialize_batches(new_batches))
    restate_reason = (f"payments amend --value-date --resolve own-batch moved "
                      f"{moved_payment.reference!r} into its own batch "
                      f"({new_external_reference}, dated {new_value_date.isoformat()}) -- "
                      f"{args.reason}")
    signoff.restate(args.run, new_batches, reason=restate_reason, by=args.by)
    return entry


def _amend_value_date(args):
    """`--value-date <YYYY-MM-DD>`: the one amendment kind that may need to
    ask a question instead of just recording an answer -- see the module
    docstring's constraint 3 and docs/specs/2026-09-22-payments-staged-
    output-design.md, "The value date that crosses a batch boundary".

    Equal to the batch's own `execution_date`: applies and records it like
    any other amendment, no decision involved.

    Not equal, and `--resolve` was not given: returns the decision payload
    and writes **nothing at all** -- `amendments.json`, `batches.json` and
    `draft.json` are untouched. The tool does not pick a default.

    Not equal, `--resolve <id>` given: applies exactly that choice --
    `keep-in-batch` (refused if unavailable), `own-batch`
    (`_resolve_own_batch`), or `hold-back` (Task 3's own exclusion path,
    `resolution` set to name it -- one exclusion path, two sources, per
    global constraint 1; no second code path is written here for it).
    """
    reference = str(args.reference or "").strip()
    new_value_date = _parse_date(args.value_date)
    if new_value_date is None:
        raise ToolkitError(("--value-date", f"{args.value_date!r} is not a valid date -- use "
                            "YYYY-MM-DD"))

    resolve = str(args.resolve or "").strip() or None
    if resolve is not None and resolve not in _VALUE_DATE_RESOLUTIONS:
        raise ToolkitError(("--resolve", f"must be one of {', '.join(_VALUE_DATE_RESOLUTIONS)}, "
                            f"not {resolve!r}"))

    batches = _deserialize_batches(_require_state(args.run, "batches", "review"))
    batch_index, payment_index = _locate_payment_batch(batches, reference)
    batch = batches[batch_index]
    payment = batch.payments[payment_index]
    current_value_date = payment.value_date.isoformat() if payment.value_date else None

    if new_value_date == batch.execution_date:
        entry = amend.record(args.run, reference=reference, change=amend.VALUE_DATE,
                             from_value=current_value_date, to_value=new_value_date.isoformat(),
                             resolution=None, reason=args.reason, by=args.by)
        _write_value_date(args.run, reference, new_value_date)
        return entry

    choices = _value_date_choices(new_value_date, batch.execution_date)

    if resolve is None:
        return {"decision_required": _VALUE_DATE_DECISION, "reference": reference,
               "batch_reference": batch.external_reference,
               "batch_execution_date": batch.execution_date.isoformat(),
               "new_value_date": new_value_date.isoformat(), "choices": choices}

    chosen = next(choice for choice in choices if choice["id"] == resolve)
    if not chosen["available"]:
        raise ToolkitError(("--resolve", chosen["unavailable_reason"]))

    if resolve == amend.RESOLUTION_KEEP_IN_BATCH:
        entry = amend.record(args.run, reference=reference, change=amend.VALUE_DATE,
                             from_value=current_value_date, to_value=new_value_date.isoformat(),
                             resolution=amend.RESOLUTION_KEEP_IN_BATCH, reason=args.reason,
                             by=args.by)
        _write_value_date(args.run, reference, new_value_date)
        entry = dict(entry)
        entry["_warnings"] = [(reference, f"recorded with value date "
                               f"{new_value_date.isoformat()}, but this batch's execution date "
                               f"is unchanged at {batch.execution_date.isoformat()} -- the bank "
                               f"will pay this payment on {batch.execution_date.isoformat()}, "
                               f"not on {new_value_date.isoformat()}")]
        return entry

    if resolve == amend.RESOLUTION_HOLD_BACK:
        return amend.record(args.run, reference=reference, change=amend.EXCLUDE,
                            from_value=None, to_value=None, resolution=amend.RESOLUTION_HOLD_BACK,
                            reason=args.reason, by=args.by)

    return _resolve_own_batch(args, batches, batch_index, payment_index, new_value_date)


def cmd_amend(args):
    """Records exactly one of `--exclude`, `--restore`, `--remittance` or
    `--value-date` against one payment reference. `--reason`/`--by` are
    required always, exactly as `signoff.disposition`/`amend.record`
    already require them.

    **Refused outright, before anything else:** any of the four flags
    invoice fraud actually targets (see the module-level comment above),
    and any amendment once this run is signed off -- constraint 3's other
    half: amending after sign-off would attest to a draft the reviewer
    never saw. (Whether the CURRENT draft is stale is a sign-off-time
    question, answered by `_draft_is_current`/`cmd_sign_off`, not an
    amend-time one; this only ever asks `signoff.is_signed_off`.)

    **`--exclude`** records the exclusion and nothing else -- it does not
    itself filter anything (global constraint 1: `amend.instructed_
    batches`, called from `cmd_build`, is the one place that ever removes
    a payment from what reaches a bank). The payment is marked in the
    workbook the same way a rejected exception's payment already is,
    because both are the same `dropped_references` `cmd_build` already
    passes to `build_payments_workbook` -- nothing here needs to know
    that; it falls out of `amend.excluded_references` joining the
    existing union.

    **`--restore`** is the only thing that un-excludes a reference (Task
    1's own defect, fixed in `amend.excluded_references`: see amend.py's
    module docstring). Refused, naming the reference, when it is not
    currently excluded -- a reviewer who mistypes a reference is told,
    rather than quietly recording a no-op that reads like an action in
    the audit trail.

    **`--remittance`** rewrites `batches.json`'s own `remittance` field
    for that payment (`_write_remittance`), never `reference` -- see
    `_write_remittance`'s own docstring. `amend.record` is called FIRST
    (it is what actually refuses an unknown reference, or a blank reason/
    by, naming what's wrong): the audit-trail entry always exists before
    the file is touched, never the other way around.

    **`--value-date`** (Task 4) may not apply anything at all -- see
    `_amend_value_date`. `--resolve <id>` only ever makes sense alongside
    it, and is refused otherwise (there being no decision it could be
    resolving).
    """
    refused = [name for name in _AMEND_REFUSED_FLAGS if getattr(args, name, None) is not None]
    if refused:
        raise ToolkitError([(f"--{name}", _AMEND_REFUSAL_MESSAGE) for name in refused])

    actions = [name for name in ("exclude", "restore") if getattr(args, name, False)]
    if getattr(args, "remittance", None) is not None:
        actions.append("remittance")
    if getattr(args, "value_date", None) is not None:
        actions.append("value-date")
    if len(actions) != 1:
        raise ToolkitError(("--exclude/--restore/--remittance/--value-date",
                            "give exactly one of --exclude, --restore, --remittance or "
                            "--value-date"))
    action = actions[0]

    if action != "value-date" and getattr(args, "resolve", None) is not None:
        raise ToolkitError(("--resolve", "only makes sense together with --value-date -- there "
                            "is no decision to resolve otherwise"))

    if signoff.is_signed_off(args.run):
        raise ToolkitError(("amend", "this run is already signed off -- an amendment now would "
                            "attest to a draft the reviewer never saw; there is nothing left to "
                            "amend"))

    reference = str(args.reference or "").strip()

    if action == "value-date":
        return _amend_value_date(args)

    if action == "exclude":
        return amend.record(args.run, reference=reference, change=amend.EXCLUDE,
                            from_value=None, to_value=None, resolution=None,
                            reason=args.reason, by=args.by)

    if action == "restore":
        if reference not in amend.excluded_references(args.run):
            raise ToolkitError(("--restore", f"{reference!r} is not currently excluded in this "
                                "run -- there is nothing to restore"))
        return amend.record(args.run, reference=reference, change=amend.RESTORE,
                            from_value=None, to_value=None, resolution=None,
                            reason=args.reason, by=args.by)

    # --remittance: record the audit-trail entry first, then apply it --
    # never the other way around (see the docstring above).
    current = _payment_field(args.run, reference, "remittance")
    entry = amend.record(args.run, reference=reference, change="remittance",
                         from_value=current, to_value=args.remittance, resolution=None,
                         reason=args.reason, by=args.by)
    _write_remittance(args.run, reference, args.remittance)
    return entry


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
    """Delegates to `signoff.sign_off` for the recording itself (its
    signature is untouched -- see the ruling in this task's own brief);
    the two checks added here are Task 2's own, and both are skipped the
    moment `signoff.sign_off` would refuse for its own, pre-existing
    reason (an outstanding exception, or a run already signed off) --
    checked read-only, via `signoff.outstanding`/`signoff.is_signed_off`,
    so that refusal fires first and is never shadowed by either of ours.
    A reviewer who has not looked at an exception must be told that, not
    told their draft is stale."""
    if not signoff.is_signed_off(args.run) and not signoff.outstanding(args.run):
        draft = _read_state(args.run, "draft")
        if draft is None:
            raise ToolkitError(("draft", _NO_DRAFT_MESSAGE))
        _assert_draft_file_present("draft", draft)
        if not _draft_is_current(args.run):
            since = len(amend.amendments(args.run)) - draft.get("amendment_count", 0)
            raise ToolkitError(("draft", _stale_draft_message(since)))
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
    from `amend.instructed_batches`'s own, filtered batches; `workbook`
    and `report` still read the unfiltered `batches` for every count and
    total that must match the signed-off record `_assert_batches_match_
    signoff` just checked them against -- but the workbook is also handed
    the same `dropped_refs` `amend.instructed_batches` returns, so it can
    mark which rows were actually excluded and show the instructed total
    beside the reviewed one (**N4**, second review -- see
    `amend.instructed_batches`'s own docstring and
    `cfo.payments.workbook.build_payments_workbook`'s `dropped_references`
    parameter). `amend.instructed_batches` is now called unconditionally,
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
    payment_batches, dropped_refs = amend.instructed_batches(args.run, batches)

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
            # B2 (F6): `dropped_refs` is the union of two sources -- a
            # rejected exception and an amendment `--exclude` are two
            # different reasons a reference is not being paid, and the
            # only message this run ever produces about it must name the
            # right one. `excluded_references` is re-read rather than
            # trusted from earlier in this call so this reflects the same
            # amendments.json `dropped_refs` itself was built from, not a
            # second, possibly-stale copy.
            excluded_refs = dropped_refs & amend.excluded_references(args.run)
            rejected_refs = dropped_refs - excluded_refs
            if rejected_refs:
                warnings.append(("payments", f"{len(rejected_refs)} payment(s) were excluded "
                                 "because the exception raised against them was rejected: " +
                                 ", ".join(sorted(rejected_refs))))
            if excluded_refs:
                warnings.append(("payments", f"{len(excluded_refs)} payment(s) were excluded "
                                 "by `payments amend --exclude`: " +
                                 ", ".join(sorted(excluded_refs))))
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


# --- cmd_final: stage 3's own command -- Task 5 of the staged-output
# milestone. `payments build --outputs ...` keeps working, unchanged, for
# anyone scripting the tool (the one thing that must stay true); `payments
# final` is the staged surface over the exact same, already-tested logic:
# "tidied" (excluded payments gone, amendments applied, control totals
# recomputed, no NOT-REVIEWED marking) is what `cmd_build` already produces
# for a signed-off run -- asserted here, never re-implemented.
#
# The one behaviour `cmd_final` adds is the gate: confirmed, unconditionally,
# for every output it can be asked for -- never `cmd_build`'s own finer-
# grained per-output gate, which still lets `workbook`/`report` be built pre-
# signoff as drafts. "Confirmed" is `signoff.is_signed_off`; when it is not
# yet true, this names the most specific reason within "not confirmed" --
# reusing Task 2's own two draft messages (constraint 3: "do not retype
# either sentence") rather than a fresh guess, and falling back to the
# pre-existing "cannot be built before sign-off" wording (moved here from
# `cmd_build`'s own per-output refusal, to where the stage boundary
# actually is) only when a current draft exists but sign-off itself simply
# has not happened yet.
#
# Checking `is_signed_off` FIRST, before either draft check, matters: once
# it is True, Task 2's own draft machinery already guarantees a current
# draft existed at the moment of sign-off (cmd_sign_off's own two checks),
# and nothing can move `batches.json` out from under that draft afterwards
# (`cmd_amend`/`signoff.restate` both refuse post-signoff) -- so a signed-off
# run is never asked to satisfy a draft check that a deleted `draft.json`
# could otherwise make it fail with the wrong, misleading message ("run
# `payments draft`" when `cmd_draft` itself would refuse, naming this
# command instead).

_NOT_SIGNED_OFF_MESSAGE = ("cannot be built before sign-off (`payments sign-off`); a "
                           "NOT-REVIEWED draft is available via `payments draft` instead")


def cmd_final(args):
    """`payments final` -- refuses until the draft has been confirmed
    (spec criterion 3: "refuses before the draft has been confirmed, and
    writes nothing"), then delegates to `cmd_build` for everything else:
    the outputs it can build (`csv`, `pain001`, and `workbook`/`report`
    alongside, per the design doc's own menu), the tidying, and the
    dropped NOT-REVIEWED marking, are all `cmd_build`'s own logic,
    unchanged -- see the module comment above this function for why this
    never re-implements any of it.

    Nothing is written before the confirmation check passes: every
    refusal below raises before `cmd_build` is ever called, so a failed
    `payments final` leaves the run's `outputs/` folder exactly as it
    was."""
    if not signoff.is_signed_off(args.run):
        draft = _read_state(args.run, "draft")
        if draft is None:
            raise ToolkitError(("final", _NO_DRAFT_MESSAGE))
        _assert_draft_file_present("final", draft)
        if not _draft_is_current(args.run):
            since = len(amend.amendments(args.run)) - draft.get("amendment_count", 0)
            raise ToolkitError(("final", _stale_draft_message(since)))
        raise ToolkitError(("final", _NOT_SIGNED_OFF_MESSAGE))
    return cmd_build(args)


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


# --- cmd_register: the extract-only path -- Task 9, added 2026-09-22.
# `payments register --run <run>` writes one CSV of what the collected
# invoices say. It needs no `check`, no `review`, no `batches.json` and no
# sign-off -- see task-9-brief.md and the spec's "Two modes, chosen at the
# start" for the three rulings this exists to satisfy. It writes no
# run-state file at all (`checked`, `flags`, `batches`, `exception-meta`,
# `signoff.json` -- none of them), only the one output file below, so a
# run can freely go on to `check`/`review`/`build` afterwards exactly as if
# this had never been called.
#
# **No fraud check runs here, at all -- not run-and-hidden, not run.**
# `cfo.payments.fraud` is never imported by this module in the first place
# (see the top of the file); this function calls nothing from it, directly
# or indirectly. A mode called "extract only" that quietly ran the checks
# anyway would be neither one thing nor the other -- Gearoid's ruling.
#
# **The bank details are the same lifted value the reviewed path uses,
# never re-lifted here and never typed by a model.** `_canonical_invoices`
# (above, in the `cmd_check` section) already carries whatever
# `cfo.payments.extract.invoice_from_output` resolved at `payments collect`
# time -- T5's script-side IBAN/account-number lift, checksummed against
# the source text. This function only ever reads that value back off the
# dict, exactly the way `_invoice_to_payment` does for the reviewed path's
# own payments; it never calls a lifter a second time and never accepts
# one from anywhere else.
#
# **The file says what it is on its face, in two places.** `NOT-CHECKED`
# in the filename, and -- because a filename survives only until someone
# renames it, and the content is what actually reaches a bank -- a banner
# as the literal first row of the file, above the header row. Do not "fix"
# the naive-CSV-parser-skips-one-line consequence of that; it is the
# point, not a wart.

REGISTER_BANNER = ("These invoices have NOT been checked for fraud, and nobody has signed "
                   "them off. This is not a payment file.")

REGISTER_COLUMNS = ("file", "invoice_number", "supplier_name", "bill_to", "invoice_date",
                    "due_date", "currency", "net", "vat", "gross", "payment_reference",
                    "iban", "account_number", "bic", "vat_number", "cost_category",
                    "contact_email")


def _register_row(invoice):
    """One `REGISTER_COLUMNS` row, straight off `invoice` -- the two date
    fields as ISO strings (`_canonical_invoices` already parsed them into
    `datetime.date`), every other field exactly the string
    `_canonical_invoices`/`invoice_from_output` already carries: blank when
    the invoice never stated it, or it never resolved (an IBAN that failed
    its checksum, say) -- never fabricated, and never reformatted."""
    row = {name: str(invoice.get(name) or "") for name in REGISTER_COLUMNS}
    row["file"] = str(invoice.get("filename") or "")
    row["invoice_number"] = str(invoice.get("number") or "")
    for field in ("invoice_date", "due_date"):
        value = invoice.get(field)
        row[field] = value.isoformat() if isinstance(value, datetime.date) else ""
    return row


def cmd_register(args):
    """`payments register --run <run>`: one CSV of what the collected
    invoices say -- see the module-level comment directly above this
    function for the three rulings this exists to satisfy. Refuses only
    when nothing has been collected at all yet."""
    invoices = _canonical_invoices(args.run)
    if not invoices:
        raise ToolkitError(("invoice-results", "no invoices have been collected yet for this "
                            "run: run `payments extract-input`, then `payments collect`, for "
                            "at least one invoice first"))

    run = runs.load_run(args.run)
    slug = str(run.get("company_slug") or "").strip("-") or "payments"
    date_part = run.get("started_at", "")[:10]
    out_dir = os.path.join(args.run, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    final_path = os.path.join(out_dir, f"{slug}-invoice-register-NOT-CHECKED-{date_part}.csv")

    # Staged and renamed into place the same way `cmd_build` stages its own
    # outputs (B4): nothing under the real name until the whole file is
    # written without raising.
    with tempfile.TemporaryDirectory(dir=out_dir) as stage_dir:
        tmp_path = os.path.join(stage_dir, os.path.basename(final_path))
        with open(tmp_path, "w", encoding="utf-8", newline="") as fh:
            csv.writer(fh).writerow([REGISTER_BANNER])
            writer = csv.DictWriter(fh, fieldnames=REGISTER_COLUMNS)
            writer.writeheader()
            for invoice in invoices:
                writer.writerow(_register_row(invoice))
        os.replace(tmp_path, final_path)

    return {"file": final_path, "invoices": len(invoices),
            "_warnings": [("payments register", "no fraud checks were run on these invoices "
                          "in this mode -- not run and hidden, not run at all -- and nobody "
                          "has reviewed or signed off on them; this file is not a payment "
                          "file")]}


COMMANDS = {"start": cmd_start, "extract-input": cmd_extract_input, "collect": cmd_collect,
            "mark-failed": cmd_mark_failed, "check": cmd_check, "review": cmd_review,
            "draft": cmd_draft, "amend": cmd_amend, "disposition": cmd_disposition,
            "sign-off": cmd_sign_off, "build": cmd_build, "final": cmd_final,
            "register": cmd_register, "red-team": cmd_red_team,
            "judgement-input": cmd_judgement_input, "judgement-collect": cmd_judgement_collect}


def dispatch(args):
    return COMMANDS[args.payments_command](args)
