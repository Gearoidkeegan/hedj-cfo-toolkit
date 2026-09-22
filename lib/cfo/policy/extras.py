"""The four optional outputs, built only when the user asks for them.

The two workbooks are drafts: the board has not taken these decisions yet, so
every value is the typical range for the company's size, marked as awaiting
approval. Nothing here decides anything on the board's behalf.
"""
import os
import re

from cfo.console import ToolkitError
from cfo.documents.docx_build import build_docx
from cfo.policy import findings as findings_module, percent
from cfo.policy.render_policy import REQUIRED_META, typical_cell
from cfo.workbook.build import build_workbook

EXTRAS = ("decisions", "board-paper", "hedj-tab", "authority-matrix")
FILENAMES = {"decisions": "board-decisions.docx", "board-paper": "board-paper.docx",
             "hedj-tab": "hedging-policy-tab.xlsx", "authority-matrix": "authority-matrix.xlsx"}
# The spec: the tool never presents a typical range as a recommendation to adopt.
PENDING_NOTE = "Typical range for a company of this size; the board has not yet approved this."
PENDING_AMOUNT_NOTE = "Amount left for the board to set; the board has not yet approved this."
LIMIT_TYPES = ("trade.execute", "payment.approve", "account.move_funds")
LIMIT_ROLES = (("Finance director or CFO", "Approves within the board's limits."),
               ("Financial controller", "Acts within the delegated limit."),
               ("Treasury or finance manager", "Executes approved transactions."))
# Final gate review item 16 (the owner's ruling): a three-row ladder labelled
# 6, 12 and 24 is ambiguous -- that month on its own, or the band ending
# there? -- so Hedj's own contract (`current_hedging_policy`, keyed on
# (month, assetClass)) gets one row per month instead, each carrying the
# min/max of whichever band that month falls in. `04-fx/04-hedge-ratio-
# bands.md` is one clause with three board parameters, either all selected
# together or all omitted together in a real run, so the three tuples below
# are still where each band's month range comes from -- just read a month
# at a time now, not written one row per tuple.
FX_TENOR_BANDS = (("ratio-0-6m", 1, 6), ("ratio-7-12m", 7, 12), ("ratio-13-24m", 13, 24))
# `06-commodity/02-comm-hedge-ratio-horizon.md`'s two board parameters: one
# ratio, held for as many months as the horizon runs -- unlike FX, there is
# only one band, so every month in range carries the same min/max.
COMMODITY_RATIO_ID = "comm-hedge-ratio-min"
COMMODITY_HORIZON_ID = "comm-horizon-max"
# The owner's cap on the ladder, whatever a typical horizon value says --
# Hedj's own `month` field allows up to 120, but a hedging policy's own
# tenor bands never run past 24.
MAX_LADDER_MONTHS = 24
_MONTHS_RE = re.compile(r"^\s*(\d+)\s*months?\s*$", re.I)


def _cell(value):
    """A table cell that cannot break the Markdown table it sits in."""
    return str(value).replace("|", "/").replace("\n", " ").strip()


def _check_meta(meta, noun):
    """The same `meta` convention `render_policy.build_policy` uses (Task 7's
    `REQUIRED_META`), so a board document can never be produced with a blank
    title, company, version, date or classification."""
    missing = [key for key in REQUIRED_META if not str(meta.get(key, "")).strip()]
    if missing:
        raise ToolkitError([(key, f"the {noun} needs a '{key}' value in meta") for key in missing])


def _front(meta, title):
    lines = ["---", f"title: {title}"]
    for key in ("company", "subtitle", "version", "date", "classification"):
        if meta.get(key):
            lines.append(f"{key}: {meta[key]}")
    return lines + ["---", ""]


def _parse_range(text):
    """A typical value as (min, max) for the Hedj tab, or None when it cannot be
    read -- which then becomes an empty pair with a warning, never a guess. The
    grammar is `cfo.policy.percent`'s, shared with the facility comparison."""
    return percent.parse_range(text)


def build_decisions(selection, out_path, meta, brand=None):
    _check_meta(meta, "board decisions")
    lines = _front(meta, "Board decisions")
    lines += ["# Decisions for the board",
              "",
              "This policy is complete once the board has taken the decisions below. Each shows "
              "what is typical for a company of this size; what is right for this company is the "
              "board's judgement, not a default.",
              ""]
    # M3: sys-annexes says this table "shows the current, board-approved
    # figure for each" decision, but the tool only ever knows the typical
    # value -- an empty column is left for the board to complete, matching
    # the same fix in cfo.policy.render_policy._decisions_table.
    lines += ["| Clause | Decision | Typical for this size | Board-approved figure |",
              "|---|---|---|---|"]
    for decision in selection.decisions:
        lines.append(f"| {_cell(decision['clause_title'])} | {_cell(decision['label'])} "
                     f"| {_cell(typical_cell(decision))} | |")
    # Facility requirements bearing on a clause the library gives no board
    # parameter of its own -- cfo.policy.obligations.Context.clause_constraint
    # -- have no decision row to sit beside, so they get a row of their own,
    # in the same table, with "the clause as a whole" standing in for a
    # decision label and no typical value or board-approved figure to show.
    clause_constraints = meta.get("clause_constraints") or []
    for constraint in clause_constraints:
        text = str(constraint.get("text") or "").strip()
        if text:
            lines.append(f"| {_cell(constraint.get('clause_title'))} | the clause as a whole "
                         f"| {_cell(text)} | |")
    lines += ["", "Record each decision in the minutes, then set it in the policy before adoption."]
    if any(decision.get("constraints") for decision in selection.decisions) or clause_constraints:
        lines += ["", "Where a facility agreement already requires something of a decision, its "
                      "requirement is shown beside the typical value: the board's choice must "
                      "stay within it for as long as that facility is outstanding."]
    result = build_docx("\n".join(lines), out_path, template="report", brand=brand)
    return {"document": result["document"], "decisions": len(selection.decisions),
            "_warnings": result.get("_warnings", [])}


# Final gate review item 7: severity alone left two ties within the sample's
# 31 Highs to Python's stable sort, which then kept section order -- so the
# board paper led with "Purpose and objectives is only partly covered" and
# "Approval and review cycle is only partly covered", ahead of no hedge
# ratio bands, no counterparty limits and no payment fraud controls. Within
# a severity, a contradiction (cites both documents) outranks a reviewer's
# own concern, which outranks a missing clause (nothing at all on the
# point), which outranks a partial one (something, just not enough) --
# "parameter" (a board decision the existing policy never set a number for)
# is procedural rather than a gap in the policy's substance, so it ranks
# last among the four.
_KIND_PRIORITY = {"contradiction": 0, "reviewer": 1, "missing": 2, "partial": 3, "parameter": 4}


def _worst_findings(found, limit=3):
    """The `limit` highest-severity findings for the board paper's "What the
    review found" bullets.

    Ranked by true severity order (`findings.SEVERITIES`) rather than by
    whatever order the caller's `found` happens to be in -- a board paper
    must lead with the worst findings even when the finder did not sort
    them. The lowest severity is never board business, so it is excluded
    before ranking, not merely truncated away.

    The fix built and read against the sample (final gate review item 7):
    `_KIND_PRIORITY` alone -- a contradiction, then a reviewer's own
    concern, then a missing clause over a partial one -- resolves a tie
    between *different* kinds, but two ties of the *same* kind (the
    library's sample has dozens of plain "missing" Highs) still fall back
    to section order, which `findings.build`'s own sort has already grouped
    them by. Read for real: with kind-priority alone, a run against the
    sample company picked two "missing" Highs from the same early section
    (01-purpose) as its second and third bullets -- better than the
    original bug, but still one section's trivia crowding out every other
    section's. Capped at one finding per section as well, in the same rank
    order, so a second slot only repeats a section once every other section
    with a qualifying finding has had its turn."""
    ranked = sorted((f for f in found if f.severity != "low"),
                    key=lambda f: (findings_module.SEVERITIES.index(f.severity),
                                   _KIND_PRIORITY.get(f.kind, len(_KIND_PRIORITY))))
    picked, used_sections, deferred = [], set(), []
    for finding in ranked:
        if len(picked) >= limit:
            break
        if finding.section and finding.section in used_sections:
            deferred.append(finding)
            continue
        picked.append(finding)
        if finding.section:
            used_sections.add(finding.section)
    for finding in deferred:
        if len(picked) >= limit:
            break
        picked.append(finding)
    return picked


def build_board_paper(selection, found, out_path, meta, brand=None):
    _check_meta(meta, "board paper")
    worst = _worst_findings(found)
    treasury = selection.scope == "treasury"
    # M4: the title and the "what is proposed" wording used to say "treasury
    # policy" and "reviewed against the company's existing arrangements" on
    # every run, whatever scope and depth actually ran -- wrong for a
    # hedging-only scope, and false on a quiz-only run with no documents to
    # review against (meta["existing_policy"], set by cli.cmd_build the same
    # way render_report reads it).
    title_word = "treasury policy" if treasury else "hedging policy"
    scope_words = "treasury policy covering cash, funding and hedging" if treasury else "hedging policy"
    reviewed = (" and reviewed against the company's existing arrangements"
               if meta.get("existing_policy") else "")
    lines = _front(meta, f"Board paper: {title_word}")
    lines += [f"# {meta.get('company', 'The company')}: {title_word} for approval",
              "",
              "## What is proposed",
              "",
              f"The board is asked to approve a {scope_words}, drafted to suit a company of this "
              f"size{reviewed}.",
              "",
              "## What the board must decide",
              "",
              f"There are {len(selection.decisions)} decisions for the board, set out in the "
              "decisions paper. They cover the limits and ratios the policy leaves open, because "
              "they are matters of appetite rather than practice.",
              ""]
    if worst:
        lines += ["## What the review found", ""]
        for item in worst:
            lines.append(f"- **{item.severity.title()}.** {item.summary}")
        lines.append("")
    lines += ["## Recommendation", "",
              "That the board approve the policy, having taken the decisions listed, and review "
              "it as often as it decides under the policy's approval and review cycle, and "
              "sooner if the company's exposures change materially."]
    # The board paper is short by design (two pages, not ten) but is not a
    # true one-pager, so it gets its own budget rather than the onepager
    # template's default "should fit on one page" check.
    result = build_docx("\n".join(lines), out_path, template="onepager", brand=brand, page_budget=2)
    return {"document": result["document"], "pages_estimate": result.get("pages_estimate", 1),
            "_warnings": result.get("_warnings", [])}


def _parse_months(text):
    """A typical horizon like "12 months" as an integer number of months, or
    None when it cannot be read that way -- the same "never guess" rule
    `_parse_range` applies to a percentage."""
    match = _MONTHS_RE.match(str(text or ""))
    return int(match.group(1)) if match else None


def _fx_ladder_rows(by_id, warnings):
    """One row per month, drawn from the three tenor-band decisions
    (`FX_TENOR_BANDS`): a row's Min/Max Hedge % is whichever band that
    month falls in, read once per band and reused for every month inside
    it -- never one row per band (a 6/12/24 ladder is ambiguous; a month is
    not). Every band is checked for a readable value, and warned about if
    not, but the ladder itself only ever extends as a contiguous run from
    month 1: a band that is missing or unreadable stops it there, since a
    month past that gap would be reporting a figure the board never gave a
    number for. In a real run the three parameters are one clause's
    (`04-fx/04-hedge-ratio-bands.md`), so they are always all selected
    together or all omitted together; checked one at a time here only
    because a hand-built selection in a test can still ask for one alone."""
    parsed = {}
    for decision_id, start, end in FX_TENOR_BANDS:
        decision = by_id.get(decision_id)
        if decision is None:
            continue
        band = _parse_range(decision["typical"])
        if band is None:
            warnings.append((decision_id, "its typical value is not a percentage range; "
                                          "no row was written for this band"))
            continue
        parsed[decision_id] = (start, end, band)
    rows = []
    for decision_id, _start, _end in FX_TENOR_BANDS:
        if decision_id not in parsed:
            break
        start, end, band = parsed[decision_id]
        for month in range(start, end + 1):
            rows.append({"month": month, "min_hedge": band[0], "max_hedge": band[1],
                        "asset_class": "FX", "note": PENDING_NOTE})
    return rows


def _commodity_ladder_rows(by_id, warnings):
    """One row per month for the company's primary commodity hedge ratio
    (`comm-hedge-ratio-min`), held for as many months as the horizon
    (`comm-horizon-max`) runs, capped at `MAX_LADDER_MONTHS` -- both
    parameters of the same clause, `06-commodity/02-comm-hedge-ratio-
    horizon.md`, so in a real run either both are selected or neither is.
    Written only when both read cleanly: Hedj's own `Asset Class` enum has
    offered "Commodity" since the first build of this sheet, but no
    selection with a commodity hedge-ratio decision ever produced a row
    for it before (final gate review item 16)."""
    ratio_decision = by_id.get(COMMODITY_RATIO_ID)
    horizon_decision = by_id.get(COMMODITY_HORIZON_ID)
    if ratio_decision is None or horizon_decision is None:
        return []
    band = _parse_range(ratio_decision["typical"])
    if band is None:
        warnings.append((COMMODITY_RATIO_ID, "its typical value is not a percentage range; "
                                             "no commodity row was written"))
        return []
    months = _parse_months(horizon_decision["typical"])
    if months is None:
        warnings.append((COMMODITY_HORIZON_ID, "its typical value is not a number of months; "
                                                "no commodity row was written"))
        return []
    capped = min(months, MAX_LADDER_MONTHS)
    return [{"month": month, "min_hedge": band[0], "max_hedge": band[1],
            "asset_class": "Commodity", "note": PENDING_NOTE} for month in range(1, capped + 1)]


def build_hedj_tab(selection, out_path, brand=None):
    """A `Hedging Policy` sheet in Hedj's import shape: one row per month of
    the board's horizon (capped at 24), each carrying the min and max of
    whichever band that month falls in -- for the FX tenor ladder
    (`_fx_ladder_rows`) and, when the selection carries one, the commodity
    hedge ratio (`_commodity_ladder_rows`). Every other decision (an IR mix,
    a counterparty rating string) is out of scope for this ladder and is
    never passed to a parser. Period Start and Period End are always left
    blank: Hedj's own contract accepts a month on its own, and the owner
    confirmed a hedging policy is more likely to speak in months or
    quarters than in dates."""
    by_id = {decision["id"]: decision for decision in selection.decisions}
    warnings = []
    rows = _fx_ladder_rows(by_id, warnings) + _commodity_ladder_rows(by_id, warnings)
    if not rows and not warnings:
        warnings.append(("hedj-tab", "the selection has no hedge-ratio decisions to write into "
                                     "the ladder; only the header row was written"))
    spec = {"sheets": [{
        "name": "Hedging Policy",
        "kind": "table",
        "columns": [{"key": "month", "label": "Month", "type": "number"},
                    {"key": "period_start", "label": "Period Start", "type": "date"},
                    {"key": "period_end", "label": "Period End", "type": "date"},
                    {"key": "min_hedge", "label": "Min Hedge %", "type": "number", "required": True},
                    {"key": "max_hedge", "label": "Max Hedge %", "type": "number", "required": True},
                    {"key": "asset_class", "label": "Asset Class", "type": "enum",
                     "options": ["FX", "Commodity", "Interest Rate"]},
                    {"key": "note", "label": "Note", "type": "text"}],
        "rows": rows}]}
    result = build_workbook(spec, out_path, brand=brand)
    return {"workbook": result.get("workbook", os.path.abspath(out_path)), "rows": len(rows),
            "_warnings": warnings + list(result.get("_warnings", []))}


def build_authority_matrix(selection, profile, out_path, brand=None):
    """An `Authority Matrix` sheet in Hedj's import shape: one row per role
    and limit type, currency taken from the company's reporting currency,
    amount and effective date left blank for the board to set.

    M7: a hedging-only policy carries only the hedging transaction limit
    (`gov-authority-limits`) -- payment approval and account-transfer limits
    belong to `pay-authority-limits`, a treasury-scope clause -- so the
    matrix offers only `trade.execute` for scope `hedging`, and all three
    limit types for `treasury` or `both`, matching what the rendered policy
    actually grants authority over."""
    currency = (profile.get("company", {}) or {}).get("reporting_currency") or "EUR"
    limit_types = LIMIT_TYPES[:1] if selection.scope == "hedging" else LIMIT_TYPES
    rows = []
    for role, note in LIMIT_ROLES:
        for limit_type in limit_types:
            rows.append({"role": role, "limit_type": limit_type, "currency": currency,
                         "amount": None, "effective_from": None,
                         "note": f"{note} {PENDING_AMOUNT_NOTE}"})
    spec = {"sheets": [{
        "name": "Authority Matrix",
        "kind": "table",
        "columns": [{"key": "role", "label": "Role", "type": "text"},
                    {"key": "limit_type", "label": "Limit type", "type": "enum",
                     "options": list(limit_types)},
                    {"key": "currency", "label": "Currency", "type": "text"},
                    {"key": "amount", "label": "Maximum amount", "type": "number"},
                    {"key": "effective_from", "label": "Effective from", "type": "date"},
                    {"key": "note", "label": "Note", "type": "text"}],
        "rows": rows}]}
    result = build_workbook(spec, out_path, brand=brand)
    return {"workbook": result.get("workbook", os.path.abspath(out_path)), "rows": len(rows),
            "_warnings": list(result.get("_warnings", []))}


def build_extras(names, context):
    unknown = [name for name in names if name not in EXTRAS]
    if unknown:
        raise ToolkitError([(name, f"is not one of {', '.join(EXTRAS)}") for name in unknown])
    # `policy build` (cli.cmd_build) passes its own file names, carrying the
    # company, version and date; a caller with nothing to say about naming
    # -- direct callers of this function -- gets the flat defaults, unchanged.
    filenames = dict(FILENAMES, **(context.get("filenames") or {}))
    out = {}
    for name in names:
        path = os.path.join(context["out_dir"], filenames[name])
        if name == "decisions":
            out[name] = build_decisions(context["selection"], path, context["meta"],
                                        context.get("brand"))
        elif name == "board-paper":
            out[name] = build_board_paper(context["selection"], context["found"], path,
                                          context["meta"], context.get("brand"))
        elif name == "hedj-tab":
            out[name] = build_hedj_tab(context["selection"], path, context.get("brand"))
        else:
            out[name] = build_authority_matrix(context["selection"], context["profile"], path,
                                               context.get("brand"))
    return out
