"""Choosing the clauses that make up one company's policy.

Deterministic: the same profile, scope and length always produce the same
policy, the same omissions and the same board decisions. No model involved.
"""
import dataclasses

from cfo.console import ToolkitError
from cfo.policy import library
from cfo.profile.conditions import evaluate
from cfo.profile.schema import get_path

MID_BAND_TURNOVER = 40000000
COMPREHENSIVE_TURNOVER = 100000000
REQUESTABLE_SCOPES = ("hedging", "treasury")


@dataclasses.dataclass
class Selection:
    scope: str
    tier: str
    size_band: str
    selected: list
    omitted: list
    decisions: list
    sections: list


def size_band(profile):
    turnover = get_path(profile, "size.turnover_eur")
    if isinstance(turnover, (int, float)) and turnover >= MID_BAND_TURNOVER:
        return "mid"
    return "small"


def recommend(profile):
    """What to suggest before asking. The user still chooses."""
    turnover = get_path(profile, "size.turnover_eur") or 0
    has_debt = bool(get_path(profile, "debt.has_debt"))
    dedicated = bool(get_path(profile, "team.treasury_dedicated"))
    entities = get_path(profile, "structure.entities") or 1
    scope = "treasury" if (has_debt or turnover >= MID_BAND_TURNOVER) else "hedging"
    if turnover >= COMPREHENSIVE_TURNOVER or (dedicated and entities > 1):
        tier = "comprehensive"
    elif turnover >= MID_BAND_TURNOVER or has_debt:
        tier = "moderate"
    else:
        tier = "lite"
    return {"scope": scope, "tier": tier}


def _in_scope(clause, scope):
    return clause.scope == "both" or clause.scope == scope


def select(clauses, profile, scope, tier):
    problems = []
    if scope not in REQUESTABLE_SCOPES:
        problems.append(("scope", f"must be one of {', '.join(REQUESTABLE_SCOPES)}"))
    if tier not in library.TIERS:
        problems.append(("tier", f"must be one of {', '.join(library.TIERS)}"))
    if problems:
        raise ToolkitError(problems)

    band = size_band(profile)
    wanted = library.tier_rank(tier)
    selected, omitted, decisions = [], [], []
    for clause in clauses:
        if not _in_scope(clause, scope) or library.tier_rank(clause.tier) > wanted:
            continue
        if not evaluate(clause.applies_if, profile):
            omitted.append({"id": clause.id, "title": clause.title,
                            "section": clause.section, "reason": clause.omit_reason})
            continue
        selected.append(clause)
        for parameter in clause.board_parameters():
            decisions.append({"clause_id": clause.id, "clause_title": clause.title,
                              "id": parameter["id"], "label": parameter["label"],
                              "typical": parameter["typical"][band]})

    return Selection(scope=scope, tier=tier, size_band=band, selected=selected,
                     omitted=omitted, decisions=decisions, sections=group_sections(selected))


def group_sections(selected):
    """The selected clauses grouped by section, in policy order."""
    sections = []
    for section_id in library.SECTION_IDS:
        in_section = [c for c in selected if c.section == section_id]
        if in_section:
            sections.append({"id": section_id, "title": library.SECTION_TITLES[section_id],
                             "clauses": in_section})
    return sections
