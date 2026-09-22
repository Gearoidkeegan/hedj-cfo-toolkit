"""Findings: what is wrong with a policy, how serious it is, and the evidence.

Severity comes from fixed rules so two runs rank the same gaps the same way.
The models write the wording of a finding, never its severity.
"""
import dataclasses

from cfo.policy import coverage as coverage_module, library

SEVERITIES = ("critical", "high", "medium", "low")
COVERAGE = ("covered", "partial", "missing")
_MISSING = {"lite": "high", "moderate": "medium", "comprehensive": "low"}
# Where a contradiction goes when the policy has no clause for it at all.
FACILITY_SECTION = "Facility obligations"
_PARTIAL = {"lite": "high", "moderate": "medium", "comprehensive": "medium"}


@dataclasses.dataclass
class Finding:
    id: str
    severity: str
    kind: str
    clause_id: str
    clause_title: str
    section: str
    section_title: str
    summary: str
    detail: str = ""
    quote: str = ""
    source: str = ""
    viewpoint: str = ""
    # A contradiction cites two documents: `quote`/`source` are the facility's
    # (the source names the facility), these are the company's own policy's.
    # `policy_source` alone, with no quote, names a policy that says nothing on it.
    policy_quote: str = ""
    policy_source: str = ""


def severity_for(kind, clause_tier, requested_tier):
    if kind == "contradiction":
        return "critical"
    if kind == "parameter":
        return "medium"
    if kind == "missing":
        return _MISSING[clause_tier]
    if kind == "partial":
        return _PARTIAL[clause_tier]
    raise ValueError(f"unknown finding kind '{kind}'")


def _section_title(clause):
    return library.SECTION_TITLES.get(clause.section, clause.section)


def section_order_key():
    """Return a mapping of section id to its position in the policy.

    Callers sort with `section_order_key().get(finding.section, 99)`, so a finding
    with no section — a contradiction that belongs to the facility obligations rather
    than to a policy section — sorts after the sectioned findings of the same severity.
    """
    return {section: index for index, section in enumerate(library.SECTION_IDS)}


def clause_order_key(selection):
    """Return a mapping of clause id to its position in the policy.

    `selection.selected` is already in policy order (cfo.policy.select.select
    appends to it as it walks the library in order), so this is the clause's
    place in the redraft, not an alphabetical accident of its id. Callers sort
    with `clause_order_key(selection).get(finding.clause_id, 99999)` as the
    tie-break after severity and section, so a gap report walks each section
    the way the policy itself reads rather than by clause-id spelling (M3) --
    a contradiction with no clause at all (`f"facility-{index}"`, see
    `build` above) sorts after every real clause in its section, which is the
    only sections where such an id would ever tie against something real.
    """
    return {clause.id: index for index, clause in enumerate(selection.selected)}


def build(selection, coverage, contradictions, gaps):
    by_id = {clause.id: clause for clause in selection.selected}
    out = []

    for index, item in enumerate(contradictions):
        clause = by_id.get(item.get("clause_id"))
        # A contradiction can outlive its clause: a facility may require something the
        # policy has no section for at all. It is still the most serious thing the tool
        # finds, so it stands on its own rather than being hung on an unrelated clause.
        clause_id = clause.id if clause else (item.get("clause_id") or f"facility-{index}")
        out.append(Finding(id=f"contradiction:{clause_id}:{index}", severity="critical",
                           kind="contradiction", clause_id=clause_id,
                           clause_title=clause.title if clause else FACILITY_SECTION,
                           section=clause.section if clause else "",
                           section_title=_section_title(clause) if clause else FACILITY_SECTION,
                           summary=item.get("summary", ""), detail=item.get("detail", ""),
                           quote=item.get("quote", ""), source=item.get("source", ""),
                           policy_quote=item.get("policy_quote", ""),
                           policy_source=item.get("policy_source", "")))

    for clause_id, result in coverage.items():
        clause = by_id.get(clause_id)
        if clause is None:
            continue
        status = result.get("status")
        if status is None or status not in COVERAGE:
            raise ValueError(f"unknown coverage status '{status}'")
        if status not in ("missing", "partial"):
            continue
        summary = (f"The policy has no clause covering {library.decapitalize(clause.title)}."
                   if status == "missing" else
                   f"{clause.title} is only partly covered.")
        out.append(Finding(id=f"{status}:{clause.id}",
                           severity=severity_for(status, clause.tier, selection.tier),
                           kind=status, clause_id=clause.id, clause_title=clause.title,
                           section=clause.section, section_title=_section_title(clause),
                           summary=summary, detail=result.get("detail", ""),
                           quote=result.get("quote", ""), source=result.get("source", "")))

    for gap in gaps:
        clause = by_id.get(gap.get("clause_id"))
        if clause is None:
            continue
        out.append(Finding(id=f"parameter:{clause.id}:{gap.get('id')}", severity="medium",
                           kind="parameter", clause_id=clause.id, clause_title=clause.title,
                           section=clause.section, section_title=_section_title(clause),
                           summary=f"{gap.get('label')} is not set; the board must decide it.",
                           detail=gap.get("detail", "")))

    order, clause_order = section_order_key(), clause_order_key(selection)
    out.sort(key=lambda f: (SEVERITIES.index(f.severity), order.get(f.section, 99),
                            clause_order.get(f.clause_id, 99999)))
    # A contradiction with no section sorts after the sectioned ones of the same
    # severity, which keeps a stable order without hiding it. Within a section,
    # findings now walk the policy's own clause order (M3), not clause-id
    # spelling -- Python's sort is stable, so more than one finding on the same
    # clause keeps the order they were appended above (contradictions, then
    # coverage, then parameter gaps).
    return out


def _states_value(entry, clause, parameter_id):
    """True when the existing policy states a value for this parameter: a
    stated value for it, or -- for a permitted-products parameter -- the
    instruments the clause's wording permits."""
    if any(item.get("parameter_id") == parameter_id for item in entry.get("stated", []) or []):
        return True
    return clause.id in coverage_module.INSTRUMENT_CLAUSE_IDS and bool(entry.get("instruments"))


def parameter_gaps(selection, coverage, existing_policy):
    """Board decisions the company's existing policy leaves unset.

    Only meaningful when they had a policy: a clause their policy covers but
    whose value it never states still needs its number set by the board. A
    value the policy states (see cfo.policy.coverage.merge's `stated`) is not a
    gap. With no existing policy the whole document is new, and the decisions
    table already says so -- reporting every decision as a gap as well would
    be noise.
    """
    if not existing_policy:
        return []
    gaps = []
    for clause in selection.selected:
        entry = coverage.get(clause.id) or {}
        if entry.get("status") not in ("covered", "partial"):
            continue
        for parameter in clause.board_parameters():
            if _states_value(entry, clause, parameter["id"]):
                continue
            gaps.append({"clause_id": clause.id, "id": parameter["id"],
                         "label": parameter["label"],
                         "detail": "Their policy has this clause but states no value for it; "
                                   "the value is the board's to set."})
    return gaps


def counts(found):
    tally = {name: 0 for name in SEVERITIES}
    for finding in found:
        tally[finding.severity] += 1
    return tally
