"""A Selection of clauses -> policy Markdown -> a .docx policy document.

The clause library (Task 1) holds the authored prose; this module only
chooses which wording to show for the requested tier, lists every board
decision up front, and marks each one inside the clause it belongs to so a
policy can never be adopted with a value the tool invented for it.
"""
import re

from cfo.console import ToolkitError
from cfo.documents.docx_build import POLICY_FIELDS, build_docx
from cfo.policy.library import REGULATORY_ASSUMED_COUNTRIES

REQUIRED_META = ("title", "company", "version", "date", "classification")
_OPTIONAL_META = tuple(key for key, _ in POLICY_FIELDS if key != "version")
# "subtitle" is optional (only cli.py's own meta sets it, to the company
# name, for the title block's new layout) and never required: a document
# built with none renders its title block exactly as it always has (see
# cfo.documents.docx_build._title_block).
_FRONT_KEYS = REQUIRED_META + ("subtitle",) + _OPTIONAL_META
_MARKER = "[Board decision: {label} — typical {typical}]"
_TOKEN_RE = re.compile(r"\{\{(.*?)\}\}")
_REGULATORY_SECTION_ID = "13-regulatory"


def _normalise(token_id):
    """A token's inner text matches a decision id ignoring surrounding
    whitespace and case, so `{{ RATIO-0-6M }}` resolves to `ratio-0-6m`."""
    return token_id.strip().casefold()


def _cell(value):
    """A table cell that cannot break the Markdown table it sits in."""
    return str(value).replace("|", "/").replace("\n", " ").strip()


def _front_matter(meta):
    missing = [key for key in REQUIRED_META if not str(meta.get(key, "")).strip()]
    if missing:
        raise ToolkitError([(key, f"the policy needs a '{key}' value in meta") for key in missing])
    lines = ["---"]
    for key in _FRONT_KEYS:
        value = meta.get(key)
        if value not in (None, ""):
            lines.append(f"{key}: {value}")
    lines += ["---", ""]
    return lines


def typical_cell(decision):
    """A decision's "Typical for this size" cell. A facility requirement that
    bears on the decision (its `constraints`, from cfo.policy.obligations, set
    on the decision by `policy build`) stands beside the typical value, so the
    board sees both before it chooses: "typical 40%; Term Loan A clause 22.3
    requires at least 60% of drawn debt at fixed rates"."""
    constraints = [str(c.get("text") or "").strip() for c in decision.get("constraints") or []]
    constraints = [text for text in constraints if text]
    if not constraints:
        return str(decision["typical"])
    return "; ".join([f"typical {decision['typical']}"] + constraints)


def _decisions_table(decisions):
    if not decisions:
        return []
    # M3: sys-annexes (14-systems/03-annexes.md) tells the reader this table
    # "shows the current, board-approved figure for each" decision; the tool
    # never fills one in (it only ever knows the typical value), so the
    # column must exist for the board to complete rather than the clause's
    # promise having nothing behind it.
    lines = ["This draft is not complete until the board has approved the decisions below.", "",
             "| Clause | Decision | Typical for this size | Board-approved figure |",
             "|---|---|---|---|"]
    for decision in decisions:
        lines.append(f"| {_cell(decision['clause_title'])} | {_cell(decision['label'])} | "
                     f"{_cell(typical_cell(decision))} | |")
    lines.append("")
    return lines


def _substitute_tokens(wording, decisions, clause, warnings):
    """Replace every `{{parameter-id}}` token whose inner text matches one of
    `decisions` -- ignoring surrounding whitespace and case -- with that
    decision's board-decision marker.

    A token that names no decision for this clause (a typo, or a parameter
    that is not the board's to set) is left exactly as written -- never
    silently dropped, and never silently filled -- and recorded in
    `warnings` as a `(clause_id, message)` pair naming the clause and the
    token, the same (where, message) shape build_docx's own `_warnings`
    uses, so a run never ships template syntax into a client-facing
    document without saying so.

    Returns the wording with its resolved tokens replaced, and the
    decisions the wording never placed (to be appended, so a decision can
    never be lost)."""
    by_id = {_normalise(decision["id"]): decision for decision in decisions}
    placed = set()

    def _replace(match):
        decision = by_id.get(_normalise(match.group(1)))
        if decision is None:
            warnings.append((clause.id, f"clause '{clause.title}' has an unresolved token "
                                         f"{match.group(0)} with no matching board decision"))
            return match.group(0)
        placed.add(id(decision))
        return _MARKER.format(label=decision["label"], typical=decision["typical"])

    wording = _TOKEN_RE.sub(_replace, wording)
    trailing = [decision for decision in decisions if id(decision) not in placed]
    return wording, trailing


def _clause_body(clause, tier, scope, decisions_by_clause, warnings):
    """The clause's wording with its board decisions marked.

    A clause may place a decision itself by writing `{{parameter-id}}` where the
    value belongs, which reads better than a marker tacked on the end. Any
    decision the wording does not place is appended, so a clause can never
    lose one. `scope` selects a clause's scope-specific wording where it has
    one (`Clause.wording`) -- most clauses have none and read the same
    either way."""
    wording, trailing = _substitute_tokens(clause.wording(tier, scope),
                                           decisions_by_clause.get(clause.id, []),
                                           clause, warnings)
    markers = [_MARKER.format(label=decision["label"], typical=decision["typical"])
              for decision in trailing]
    return " ".join([wording] + markers).strip()


def _regulatory_jurisdiction_note(country):
    """None, or a note that this policy's Regulatory section -- EMIR, MiFID
    and hedge accounting -- is framed around Ireland, the UK and the EU,
    for a company registered outside them (final gate review item 5): the
    gap report already said this (`render_report.report_markdown`); a
    company outside those jurisdictions previously got the same regulatory
    clauses in the policy its board actually adopts with no such note at
    all. Framed as something to confirm, not a ruling that a regime does
    or does not apply -- the skill's own ground rule ("regulation is
    flagged, not asserted") holds for the redraft too, so this never gates
    the section out; it only says, in the section the board is reading,
    what that section's framing assumes."""
    country = str(country or "").strip().upper()
    if not country or country in REGULATORY_ASSUMED_COUNTRIES:
        return None
    # Built, not typed: a literal "--" reaches Word as two hyphens in a
    # document a board reads (see render_report's note).
    dash = " " + chr(0x2013) + " "
    return ("This section's regulatory clauses" + dash + "EMIR, MiFID and hedge accounting"
           + dash + "are "
           f"framed around Ireland, the United Kingdom and the European Union. The company's "
           f"registration country ({country}) is outside them: whether any of these regimes "
           "applies is a matter for the company's own advisers to confirm, not a position this "
           "policy assumes.")


def drafted_clauses(selection):
    """Every selected clause as the redraft words it -- id, title, section
    and the rendered text, board decisions marked as undecided with their
    typical value. For the review panel on a run with no existing policy:
    the draft is the only policy there is, so a reviewer shown only clause
    titles reports "the policy does not state X" about clauses that do."""
    decisions_by_clause = {}
    for decision in selection.decisions:
        decisions_by_clause.setdefault(decision["clause_id"], []).append(decision)
    warnings = []
    return [{"id": clause.id, "title": clause.title, "section": clause.section,
             "text": _clause_body(clause, selection.tier, selection.scope,
                                  decisions_by_clause, warnings)}
            for section in selection.sections for clause in section["clauses"]]


def policy_markdown(selection, meta, warnings=None):
    """The full policy, front matter to the last clause, ready for
    `build_docx(..., template="policy")`.

    `warnings` is an optional list that any unresolved-token warnings are
    appended to (see `_substitute_tokens`); `build_policy` always passes
    one through so they reach its result. Called on its own, without a
    list, tokens are still never silently filled -- the warnings are just
    not collected anywhere."""
    if warnings is None:
        warnings = []
    lines = _front_matter(meta)
    lines += _decisions_table(selection.decisions)
    decisions_by_clause = {}
    for decision in selection.decisions:
        decisions_by_clause.setdefault(decision["clause_id"], []).append(decision)
    note = _regulatory_jurisdiction_note(meta.get("country"))
    for section in selection.sections:
        lines += [f"# {section['title']}", ""]
        if note and section["id"] == _REGULATORY_SECTION_ID:
            lines += [note, ""]
        for clause in section["clauses"]:
            lines += [f"## {clause.title}", "",
                     _clause_body(clause, selection.tier, selection.scope, decisions_by_clause,
                                 warnings), ""]
    return "\n".join(lines)


def build_policy(selection, out_path, meta, brand=None, style=None):
    """Render, build and save the policy. Returns counts rather than the
    Clause objects themselves, so the result stays plain JSON for a CLI
    command to `emit()` directly.

    `style` is an optional `cfo.documents.style_profile.StyleProfile`, passed
    straight through to `build_docx`, which applies its fonts, sizes,
    margins, page size, heading colour and heading numbering -- falling back
    to the template's own choice, with a warning from `read_style`, for
    whatever the profile could not read."""
    token_warnings = []
    md = policy_markdown(selection, meta, token_warnings)
    result = build_docx(md, out_path, template="policy", brand=brand, style=style)
    return {"document": result["document"], "clauses": len(selection.selected),
            "decisions": len(selection.decisions),
            "_warnings": token_warnings + result["_warnings"]}
