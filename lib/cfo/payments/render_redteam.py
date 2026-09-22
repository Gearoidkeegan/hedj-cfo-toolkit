"""Red-team task output -> a Word report: attack routes ranked by severity,
each with its steps, the control that failed and the one that would close
it, plus a plain statement of what material this run was given and what it
could not assess.

This is the only part of the payments tool that is adversarial by design --
it describes how someone would get money out of the company despite its own
stated controls -- so two things are worth saying plainly about its shape:

- **It never touches a payment.** This module takes a red-team task's
  already-accepted routes and a `meta` dict describing what fed the task,
  and turns them into a document. It builds nothing an emitter could read:
  there is no import here of the payment model, the batch splitter or any
  emitter module, by design, not by omission -- see the test that checks
  this module's own import lines for exactly that.
- **It only reasons about controls the routes already name.** Whether a
  route's named control genuinely appears in the material this run was
  given is enforced upstream, mechanically, the same way every other quoted
  claim in this toolkit is checked: the `payments.red-team` task's
  `quote_checks` require `routes[].control_quote` to be a verbatim
  substring of the `controls` input it was shown (see
  `cfo.tasks.quotes.check_quotes`), so a route resting on an invented
  control is a rejected task output, never something this renderer has to
  catch a second time. This module's job starts after that check has
  already passed.

Follows `cfo.policy.render_report` for style: a Markdown document assembled
from plain functions, then handed to `cfo.documents.docx_build.build_docx`.

Dashes in prose are built with `chr(0x2013)`, never typed with two hyphens
in a row -- a literal double hyphen reaches Word as two hyphens in a
document a board reads, because Word only autocorrects what someone types
into it, never what a file already holds. This cost a real fix on the
policy tool; the guard test there, and its equivalent here, explain why.
"""
from cfo.console import ToolkitError
from cfo.documents.docx_build import build_docx

REQUIRED_META = ("title", "company", "date", "classification")
SEVERITIES = ("critical", "high", "medium", "low")
MAX_ROUTES = 15

# The five personas this mode always reasons about separately (see the
# task's prompt.md). Keys here match
# assets/tasks/payments/red-team/schema.json's `persona` enum exactly; this
# dict exists only so the report prints a plain label instead of the raw
# id, the same reason cfo.policy.render_report keeps its own
# _VIEWPOINT_NAMES rather than printing a viewpoint's raw id.
PERSONA_LABELS = {
    "external_fraudster": "External email fraudster",
    "insider": "Insider",
    "compromised_mailbox": "Compromised mailbox",
    "cloned_voice": "Cloned voice or deepfake call",
    "poisoned_invoice": "Poisoned invoice",
}

# The four kinds of material this mode reads (the approval matrix, the
# payment mandates, the supplier-change log and the company profile).
# `meta["material"]` must map each of these labels to whether this run
# actually had it -- see `report_markdown` and `_material_lines`.
MATERIAL_LABELS = ("approval matrix", "payment mandates", "supplier-change log",
                   "company profile")

DASH = " " + chr(0x2013) + " "


def _cell(value):
    """Flatten anything interpolated into the document: a newline or a
    stray '|' in a model's own text must never be able to break the
    Markdown around it."""
    return str(value).replace("|", "/").replace("\n", " ").strip()


def _english_list(items):
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _persona_label(persona_id):
    return PERSONA_LABELS.get(persona_id, str(persona_id))


def _material_lines(material):
    supplied = [label for label in MATERIAL_LABELS if material.get(label)]
    missing = [label for label in MATERIAL_LABELS if not material.get(label)]
    lines = ["# Material this review was given", ""]
    if supplied:
        lines += [f"This review had: {_english_list(supplied)}.", ""]
    else:
        lines += ["This review had none of the material this mode reads.", ""]
    if missing:
        lines += [f"This review did not have: {_english_list(missing)}, so any route that "
                 "would rest on it could not be assessed" + DASH + "their absence here is "
                 "not a finding that those routes are closed.", ""]
    return lines


def _route_block(route, level=2):
    heading = f"{_persona_label(route['persona'])}{DASH}{str(route['severity']).title()}"
    lines = [f"{'#' * level} {_cell(heading)}", "", "**Steps:**", ""]
    lines += [f"{i}. {_cell(step)}" for i, step in enumerate(route["steps"], start=1)]
    lines += ["", f"**Control that failed:** {_cell(route['failed_control'])}", "",
             f"> {_cell(route['control_quote'])}", ""]
    if route.get("control_source"):
        lines += [f"Source: {_cell(route['control_source'])}", ""]
    lines += [f"**Closes it:** {_cell(route['closing_control'])} "
             f"({_cell(route['closing_control_cost'])})", ""]
    return lines


def _severity_counts(routes):
    tally = {severity: 0 for severity in SEVERITIES}
    for route in routes:
        if route.get("severity") in tally:
            tally[route["severity"]] += 1
    return tally


def report_markdown(routes, meta):
    """The full red-team report, front matter to the material statement,
    ready for `build_docx(..., template="report")`.

    `routes` is the red-team task's already-accepted output's `routes`
    list -- each one already passed the task's schema and its
    `quote_checks` (see the module docstring); this function does not
    re-check them, only renders them. `meta` needs `REQUIRED_META` plus a
    `material` dict mapping each of `MATERIAL_LABELS` to whether this run
    had it, so a run with thin material says so rather than reading as a
    clean bill of health.
    """
    missing_meta = [key for key in REQUIRED_META if not str(meta.get(key, "")).strip()]
    if missing_meta:
        raise ToolkitError([(key, f"the red-team report needs a '{key}' value in meta")
                            for key in missing_meta])
    material = meta.get("material")
    if not isinstance(material, dict):
        raise ToolkitError(("material", "the red-team report needs a 'material' dict in "
                            "meta, naming what this run was given"))

    lines = ["---"] + [f"{key}: {meta[key]}" for key in REQUIRED_META] + ["---", ""]

    thin = any(not material.get(label) for label in MATERIAL_LABELS)

    if not routes:
        lines += ["# Attack routes", ""]
        if thin:
            lines += ["No attack routes are reported here. That is not a finding that this "
                     "company's controls hold" + DASH + "it reflects gaps in the material "
                     "below, not every persona having been tried and found unable to get "
                     "past them.", ""]
        else:
            lines += ["No route was found past the controls described below, for any of the "
                     "five personas this mode reasons about.", ""]
    else:
        lines += ["# Attack routes, ranked by severity", ""]
        ranked = sorted(routes, key=lambda r: SEVERITIES.index(r["severity"]))
        for route in ranked:
            lines += _route_block(route)

    lines += _material_lines(material)
    return "\n".join(lines)


def build_report(routes, out_path, meta, brand=None):
    """Render, build and save the red-team report. Returns counts rather
    than the routes themselves, so the result stays plain JSON for a CLI
    command to `emit()` directly -- `emit()` here is `cfo.console.emit`,
    the JSON-line stdout writer every command uses, never a payment
    emitter; this module has no relationship with `cfo.payments.emit`."""
    md = report_markdown(routes, meta)
    result = build_docx(md, out_path, template="report", brand=brand)
    return {"document": result["document"], "routes": len(routes),
            "counts": _severity_counts(routes), "_warnings": result["_warnings"]}
