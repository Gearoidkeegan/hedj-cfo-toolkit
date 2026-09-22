"""Findings and a Selection -> gap report Markdown -> a .docx gap report.

Contradictions come first, then the remaining findings grouped by section in
policy order, each with its quote or an explicit note that no wording was
found. The omissions annex is always present, even when nothing was omitted.
"""
import re

from cfo.console import ToolkitError
from cfo.documents.docx_build import build_docx
from cfo.policy.findings import FACILITY_SECTION, SEVERITIES
from cfo.policy.findings import counts as count_findings
from cfo.policy.library import SECTION_TITLES, REGULATORY_ASSUMED_COUNTRIES

REQUIRED_META = ("title", "company", "date", "classification")
# Both optional, and never required (`REQUIRED_META` above still is): a
# document built with neither renders its title block exactly as it always
# has (cfo.documents.docx_build._title_block). cli.py's own meta sets both
# together -- "subtitle" to the company name, "version" to the redraft's
# version -- for the new layout; only written into the front matter here
# when the caller actually supplied one.
_OPTIONAL_META = ("subtitle", "version")
# REGULATORY_ASSUMED_COUNTRIES (Ireland, the UK and the rest of the EU) now
# lives in cfo.policy.library, shared with render_policy.py -- the same
# note now also appears at the head of the redrafted policy's Regulatory
# section for a company outside them (final gate review item 5), so both
# documents read the assumed-countries set from one place.


def _cell(value):
    """A table cell (or a quote line) that cannot break the Markdown around it."""
    return str(value).replace("|", "/").replace("\n", " ").strip()


# cfo.extract.sink.loc_label renders a paragraph as "[para N]" and a page as
# the compact "[pN]" -- both raw extraction markers, meant to let
# coverage.plan_batches and a model task find a location again, never to be
# read by a person. Final gate review item 9: a citation built from one
# (`source`/`policy_source`, alone or after a filename or facility name a
# batch already prefixed, e.g. "their-policy.docx, [para 12]") printed the
# raw marker straight into the gap report. Read out in words instead; a
# marker in a form this does not recognise (a table, sheet or slide
# reference) is left exactly as extracted rather than guessed at.
_PARA_MARKER_RE = re.compile(r"\[\s*para\s+(\d+)\s*\]", re.I)
_PAGE_MARKER_RE = re.compile(r"\[\s*p(?:age)?\s*(\d+)\s*\]", re.I)


def _humanise_source(text):
    text = _PARA_MARKER_RE.sub(lambda m: f"paragraph {m.group(1)}", text)
    text = _PAGE_MARKER_RE.sub(lambda m: f"page {m.group(1)}", text)
    return text


def _contradiction_evidence(finding):
    """Both documents, one after the other: the facility clause, then the
    company's own policy -- its wording, or a plain note that it says nothing
    on this."""
    facility_source = _humanise_source(_cell(finding.source))
    lines = [f"**Facility:** {facility_source or 'source not recorded'}", ""]
    lines += [f"> {_cell(finding.quote)}", ""] if finding.quote else [
        "No wording from the facility was captured for this finding.", ""]
    policy_source = _humanise_source(_cell(finding.policy_source))
    if finding.policy_quote:
        lines += [f"**Company's policy:** {policy_source or 'source not recorded'}",
                  "", f"> {_cell(finding.policy_quote)}"]
    elif finding.policy_source:
        lines += [f"**Company's policy:** {policy_source}: no wording on this "
                  "was found."]
    else:
        lines += ["**Policy:** the redrafted policy has no clause for this."]
    return lines


# Plain nouns for the panel's viewpoints, for a sentence naming who raised or
# endorsed a finding -- the raw id ("ned") is never fit for a client-facing
# document. Kept local rather than imported from cfo.policy.panel: that
# module's own VIEWPOINT_LABELS carries a leading article ("A lender") built
# for a sentence's start, not a name to drop after a colon.
_VIEWPOINT_NAMES = {"lender": "the lender", "auditor": "the auditor",
                    "ned": "the non-executive director"}


def _viewpoint_names(raw):
    """`finding.viewpoint` as a plain, readable phrase: one or more panel
    viewpoint ids, comma-separated (see cfo.policy.panel.merge_reviews),
    turned into their proper names and joined as an English list."""
    names = [_VIEWPOINT_NAMES.get(part.strip(), part.strip())
            for part in raw.split(",") if part.strip()]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _evidence(finding):
    lines = []
    if finding.kind == "contradiction":
        lines += _contradiction_evidence(finding)
    elif finding.quote:
        lines += [f"> {_cell(finding.quote)}", "",
                 f"Source: {_humanise_source(_cell(finding.source))}" if finding.source
                 else "Source: not recorded."]
    elif finding.kind == "missing":
        lines += ["No matching wording was found in the company's policy."]
    elif finding.kind == "parameter":
        lines += ["This is a board decision, not wording to look for in an existing policy."]
    else:
        lines += ["No supporting quote was captured for this finding."]
    if finding.viewpoint:
        # M3: `viewpoint` means two different things depending on how it got
        # there (cfo.policy.panel.merge_reviews) -- a "reviewer"-kind finding
        # is one a reviewer raised themselves; on any other kind, it names
        # who endorsed a finding the scripts had already raised. Rendering
        # both the same way ("Raised by: lender" for an endorsement) claimed
        # the lender had raised something the scripts found instead.
        verb = "Raised by" if finding.kind == "reviewer" else "Endorsed by"
        lines += ["", f"*{verb}: {_viewpoint_names(finding.viewpoint)}*"]
    return lines


def _finding_block(finding, level):
    lines = [f"{'#' * level} {_cell(finding.clause_title)}", "", _cell(finding.summary)]
    if finding.detail:
        lines += ["", _cell(finding.detail)]
    lines += [""] + _evidence(finding) + [""]
    return lines


def _groupable(finding):
    """What makes two findings' blocks identical apart from their summary:
    the same clause, the same kind, and word-for-word the same explanation
    and evidence. Anything a reviewer touched (`viewpoint`) or that carries
    its own quote falls out of this by construction, because `_evidence`
    then differs."""
    return (finding.clause_id, finding.kind, finding.severity, finding.detail,
            tuple(_evidence(finding)))


def _finding_blocks(findings, level):
    """The findings of one section, as markdown, with a run of parameter
    findings on the same clause collapsed under one heading.

    A clause with three unset board parameters produced three consecutive
    blocks with the same heading ("Hedge ratio bands by tenor"), the same
    "the value is the board's to set" sentence and the same "this is a board
    decision" line -- differing only in which value each named. A board
    reading that sees repetition rather than three decisions. Each parameter
    is still its own Finding: severity, ordering, the scoreboard and
    findings.json all count them exactly as before, and nothing but the
    rendering changes.

    Only a *consecutive* run is grouped, so the severity and clause ordering
    findings.build established is preserved untouched, and only when
    `_groupable` says the surrounding text is identical -- otherwise each
    finding keeps its own block, wording and all."""
    lines = []
    index = 0
    while index < len(findings):
        finding = findings[index]
        run = [finding]
        if finding.kind == "parameter":
            key = _groupable(finding)
            while index + len(run) < len(findings) and _groupable(findings[index + len(run)]) == key:
                run.append(findings[index + len(run)])
        index += len(run)
        if len(run) == 1:
            lines += _finding_block(finding, level)
            continue
        lines += [f"{'#' * level} {_cell(finding.clause_title)}", ""]
        if finding.detail:
            lines += [_cell(finding.detail), ""]
        lines += [f"- {_cell(item.summary)}" for item in run] + [""]
        lines += _evidence(finding) + [""]
    return lines


def _severity_table(selection, found):
    lines = ["| Section | Critical | High | Medium | Low |", "|---|---|---|---|---|"]
    section_ids = {section["id"] for section in selection.sections}
    for section in selection.sections:
        in_section = [f for f in found if f.section == section["id"]]
        if not in_section:
            continue
        row = {severity: sum(1 for f in in_section if f.severity == severity) for severity in SEVERITIES}
        lines.append(f"| {_cell(section['title'])} | {row['critical']} | {row['high']} | "
                     f"{row['medium']} | {row['low']} |")
    # Facility contradictions with no matching policy section
    facility_findings = [f for f in found if f.section not in section_ids]
    if facility_findings:
        row = {severity: sum(1 for f in facility_findings if f.severity == severity) for severity in SEVERITIES}
        lines.append(f"| {_cell(FACILITY_SECTION)} | {row['critical']} | {row['high']} | "
                     f"{row['medium']} | {row['low']} |")
    tally = count_findings(found)
    lines.append(f"| Total | {tally['critical']} | {tally['high']} | {tally['medium']} | {tally['low']} |")
    return lines


def _parts(numbers):
    numbers = [str(n) for n in numbers]
    if len(numbers) == 1:
        return f"part {numbers[0]}"
    return "parts " + ", ".join(numbers[:-1]) + " and " + numbers[-1]


def _covenant_checks(status, found):
    """What the covenant checks covered, said plainly -- so a report with no
    contradictions can never be read as a clean bill of health when no
    facility agreement was compared, or one was only partly read.

    `status` is the run's `obligations-status.json`: None when no facility
    agreement was supplied; `merged: false` when they were listed but never
    compared; otherwise one entry per document with the parts read and not."""
    lines = ["# Covenant checks", ""]
    if status is None:
        return lines + ["No facility agreements were provided, so covenant checks were not "
                        "possible. Any hedging, reporting or security obligation in the company's "
                        "loan agreements has not been compared with this policy.", ""]
    if not status.get("merged"):
        return lines + ["Facility agreements were supplied but were never compared with the "
                        "policy, so covenant checks were not completed.", ""]
    checked, problems = [], []
    for doc in status.get("documents", []):
        name = doc.get("facility") or ""
        label = f"{name} ({doc['document']})" if name and name != doc["document"] else doc["document"]
        unread = doc.get("unread") or []
        if not unread:
            checked.append(label)
        elif not doc.get("read"):
            problems.append(f"Facility {label} could not be read, so none of its covenants were "
                            "checked.")
        else:
            problems.append(f"Facility {label} could not be read in full: {_parts(unread)} of "
                            f"{doc.get('parts')} could not be checked, so an obligation in "
                            f"{'that part' if len(unread) == 1 else 'those parts'} may not have "
                            "been compared with the policy.")
    if checked:
        verb = "was" if len(checked) == 1 else "were"
        joined = checked[0] if len(checked) == 1 else ", ".join(checked[:-1]) + " and " + checked[-1]
        sentence = f"{joined} {verb} read in full and compared with the policy."
        if not problems and not any(f.kind == "contradiction" for f in found):
            sentence += " No contradiction with the policy was found."
        lines += [sentence, ""]
    for problem in problems:
        lines += [problem, ""]
    return lines


def _facility_constraints(selection, has_policy, clause_constraints=()):
    """Every facility requirement bearing on a board decision (the same data
    cfo.policy.render_policy.typical_cell puts beside the decision in the
    redraft and the board decisions document), plus every one that bears on
    a clause with no board parameter to attach to instead
    (`clause_constraints` -- cfo.policy.obligations.Context.clause_constraint;
    its Decision cell reads "the clause as a whole"), as its own section.

    Shown whenever there are constraints, whether or not there is an existing
    policy to compare: a loan's requirement still binds the board's figure
    even when a policy exists and meets or falls short of it elsewhere in
    this report (as a constraint on the decisions table, or a Critical
    contradiction) -- this section is the one place a reader sees every
    facility requirement, on a board decision or on a clause as a whole,
    gathered together, so it is never conditional on whether a policy was
    supplied."""
    rows = []
    for decision in selection.decisions:
        for constraint in decision.get("constraints") or []:
            text = str(constraint.get("text") or "").strip()
            if text:
                rows.append((decision["clause_title"], decision["label"], text))
    for constraint in clause_constraints or []:
        text = str(constraint.get("text") or "").strip()
        if text:
            rows.append((str(constraint.get("clause_title") or "").strip(),
                        "the clause as a whole", text))
    if not rows:
        return []
    intro = ("The facility agreements read for this review already require the following of the "
            "decisions below; the board's choice must stay within each one for as long as that "
            "facility is outstanding.")
    if not has_policy:
        intro = "No existing policy was compared, but " + intro[0].lower() + intro[1:]
    lines = ["# Facility constraints", "", intro, "",
             "| Clause | Decision | Facility requirement |", "|---|---|---|"]
    for clause_title, label, text in rows:
        lines.append(f"| {_cell(clause_title)} | {_cell(label)} | {_cell(text)} |")
    lines.append("")
    return lines


def _omissions_annex(omitted):
    lines = ["# Omitted clauses", ""]
    if not omitted:
        lines.append("No clauses were omitted; every clause in the library that applies to this "
                     "company's scope and length appears in the policy.")
        return lines
    lines += ["| Section | Clause | Reason |", "|---|---|---|"]
    for item in omitted:
        section_title = SECTION_TITLES.get(item["section"], item["section"])
        lines.append(f"| {_cell(section_title)} | {_cell(item['title'])} | {_cell(item['reason'])} |")
    return lines


def report_markdown(selection, found, meta):
    """The full gap report, front matter to the omissions annex, ready for
    `build_docx(..., template="report")`."""
    missing = [key for key in REQUIRED_META if not str(meta.get(key, "")).strip()]
    if missing:
        raise ToolkitError([(key, f"the gap report needs a '{key}' value in meta") for key in missing])

    lines = ["---"] + [f"{key}: {meta[key]}" for key in REQUIRED_META]
    lines += [f"{key}: {meta[key]}" for key in _OPTIONAL_META if str(meta.get(key, "")).strip()]
    lines += ["---", ""]

    # Dashes in prose are built, never typed: a literal "--" reaches Word as
    # two hyphens in a document a board reads, because Word only autocorrects
    # what someone types into it, never what a file already holds.
    dash = " " + chr(0x2013) + " "

    country = str(meta.get("country") or "").strip().upper()
    if country and country not in REGULATORY_ASSUMED_COUNTRIES:
        lines += [f"The company's registration country ({country}) is outside Ireland, the United "
                 "Kingdom and the European Union. This library's regulatory clauses" + dash
                 + "EMIR, MiFID and hedge accounting" + dash + "are framed around those "
                 "jurisdictions; where the company sits elsewhere, check with its own advisers "
                 "which of them, if any, apply.", ""]

    no_existing_policy = not meta.get("existing_policy", True)
    if no_existing_policy:
        lines += ["No existing treasury policy was supplied for this review, so there is nothing "
                 "for this report to compare it with" + dash + "the redraft below is a first "
                 "policy for the company, not a mark-up of one it already had. This report "
                 "instead lists any "
                 "facility constraints the company's loan agreements already place on the board's "
                 "decisions, and, in the omissions annex at the end, the clauses left out of the "
                 "redraft and why.", ""]

    # I2: with no existing policy and nothing else for the scripts to find
    # fault with (no facility contradiction either), an all-zero severity
    # table reads as a clean bill of health for a policy that does not yet
    # exist. Say so in words instead of printing a table with nothing in it.
    if no_existing_policy and not found:
        lines += ["# Summary of findings", "",
                 "There is nothing to compare, so no findings are listed below: this review starts "
                 "from a blank page, not a policy with gaps in it.", ""]
    else:
        lines += ["# Summary of findings", ""] + _severity_table(selection, found) + [""]

    # Only when the caller says what was checked (`policy build` always does);
    # a report rendered without that information says nothing either way.
    if "covenant_checks" in meta:
        lines += _covenant_checks(meta["covenant_checks"], found)

    lines += _facility_constraints(selection, has_policy=not no_existing_policy,
                                   clause_constraints=meta.get("clause_constraints") or [])

    contradictions = [f for f in found if f.kind == "contradiction"]
    if contradictions:
        lines += ["# Contradictions", ""]
        for item in contradictions:
            lines += _finding_block(item, 2)

    remaining = [f for f in found if f.kind != "contradiction"]
    for section in selection.sections:
        in_section = [f for f in remaining if f.section == section["id"]]
        if not in_section:
            continue
        lines += [f"# {section['title']}", ""]
        lines += _finding_blocks(in_section, 3)

    lines += _omissions_annex(selection.omitted)
    return "\n".join(lines)


def build_report(selection, found, out_path, meta, brand=None):
    """Render, build and save the gap report. Returns counts rather than
    the Finding objects, so the result stays plain JSON for a CLI command
    to `emit()` directly."""
    md = report_markdown(selection, found, meta)
    result = build_docx(md, out_path, template="report", brand=brand)
    return {"document": result["document"], "findings": len(found),
            "counts": count_findings(found), "_warnings": result["_warnings"]}
