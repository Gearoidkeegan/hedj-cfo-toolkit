"""The review panel: what each reviewer is shown, and how their output merges.

Reviewers see the findings, the clause list and the obligations — never the
company's documents, so a reviewer cannot quote something the task layer has
not checked. The critic rules on what the reviewers disputed or added.

Calling order: merge_reviews() must be called before critic_input() or apply_critic(),
because reviewer-added findings do not have an id until merge_reviews() assigns it
(id = "reviewer:{viewpoint}:{clause_id}:{index}"). The critic reads ids off the
merged findings list and uses them to key verdicts, so the flow is always:
  merged = merge_reviews(found, reviews, selection)
  critic_data = critic_input(merged, reviews)  # Now the findings have ids
  verdicts = model(critic_data)  # Critic returns verdicts keyed by those ids
  final = apply_critic(merged, verdicts)  # Apply verdicts to the merged list
"""
from cfo.policy import findings as findings_module
from cfo.policy import library
from cfo.policy.obligations import FACILITY_LISTS
from cfo.profile.store import for_task

VIEWPOINTS = ("lender", "auditor", "ned")
VIEWPOINT_LABELS = {"lender": "A lender", "auditor": "An auditor",
                    "ned": "A non-executive director"}
REVIEWER_SEVERITIES = ("high", "medium", "low")
# I3: a single facility read in many parts can merge into far more obligations
# than any one part's own schema cap -- cfo.policy.obligations.merge_parts
# de-duplicates on (quote, source) but caps nothing. Capped again here, per
# list, so the panel's own input never depends on how many pages a facility
# runs to. What a contradiction rests on is always kept ahead of the rest;
# how many were left out is said plainly (_obligation_summary).
MAX_ITEMS_PER_LIST = 6


def _finding_row(finding):
    return {"id": finding.id, "severity": finding.severity, "kind": finding.kind,
            "clause_id": finding.clause_id, "clause_title": finding.clause_title,
            "section": finding.section_title, "summary": finding.summary,
            "detail": finding.detail}


def _spread(indexes, count):
    """`count` of `indexes`, evenly spaced across them, the first and the last
    always among them. A facility read in many parts lists its covenants in
    document order, so keeping the first few would show a reviewer only the
    opening pages of a hundred-page agreement; an even spread shows the shape
    of the whole of it.

    Spaced between the two ends, not by a stride: a stride of len/count never
    reaches the tail -- 12 covenants capped at 6 gave the 1st, 3rd, 5th, 7th,
    9th and 11th and never the 12th, and 120 stopped at index 100, so the last
    sixth of a long agreement could not reach a reviewer at all."""
    if count <= 0 or not indexes:
        return []
    if len(indexes) <= count:
        return list(indexes)
    if count == 1:
        return [indexes[0]]
    last = len(indexes) - 1
    return [indexes[round(i * last / (count - 1))] for i in range(count)]


def _keep(items, priority_quotes, count):
    """Which of `items` the panel is shown: everything a contradiction rests on
    -- never drop the evidence behind a finding the panel is about to see --
    then an even spread of the rest, all in document order. Only a hedging
    requirement ever carries a contradiction's quote today, so for covenants,
    reporting duties and restrictions the spread is what decides, which is why
    it is a spread and not simply the first few."""
    cited = [i for i, item in enumerate(items) if item.get("quote") in priority_quotes]
    keep = set(cited[:count])
    others = [i for i in range(len(items)) if i not in keep]
    keep.update(_spread(others, count - len(keep)))
    return [items[i] for i in sorted(keep)]


def _obligation_summary(obligations, found=()):
    """The merged facilities for the review panel, without the quotes and
    sources behind each obligation (I1): a reviewer judges what a facility
    requires, not whether the toolkit read it correctly -- that check is
    cfo.policy.obligations.compare's, already done before the panel runs.
    Dropping them is most of what keeps a comprehensive run's panel input
    under its cap when a long facility carries many obligations.

    Each list is also capped at MAX_ITEMS_PER_LIST (I3): a facility read in
    many parts can merge into far more than that -- obligations.merge_parts
    de-duplicates but does not cap -- and nothing here truncates the
    comparison itself, only what the panel is shown. What survives is chosen by
    `_keep`; a `<key>_note` says plainly how many were left out, so a reviewer
    is never shown a short list that looks complete when it is not."""
    priority_quotes = {f.quote for f in found if f.kind == "contradiction" and f.quote}
    out = []
    for facility in obligations or []:
        row = {"facility_name": facility.get("facility_name", ""),
               "document": facility.get("document", "")}
        for key in FACILITY_LISTS:
            items = [item for item in facility.get(key, []) or [] if isinstance(item, dict)]
            kept = _keep(items, priority_quotes, MAX_ITEMS_PER_LIST)
            row[key] = [{field: value for field, value in item.items()
                        if field not in ("quote", "source")} for item in kept]
            if len(items) > len(kept):
                row[f"{key}_note"] = (f"+{len(items) - len(kept)} more, not shown; those shown are "
                                      "spaced across the agreement")
        out.append(row)
    return out


DECISIONS_NOTE = ("Every decision below is the board's to make and none has been made: "
                  "`typical` is what companies of this size commonly choose, shown for "
                  "reference only. It is not a limit this company has set.")


def reviewer_input(selection, found, profile, obligations, existing_policy=True):
    """What a reviewer sees. With an existing policy the findings describe
    its gaps, so the clause list is enough. Without one (`existing_policy`
    False) the redraft is the only policy there is, and each clause carries
    its drafted text -- otherwise reviewers see titles only and raise
    "the policy does not state X" about clauses the draft contains."""
    company = for_task(profile).get("company", {})
    if existing_policy:
        clauses = [{"id": c.id, "title": c.title, "section": c.section}
                   for c in selection.selected]
    else:
        from cfo.policy.render_policy import drafted_clauses
        clauses = drafted_clauses(selection)
    return {"company": company,
            "policy": {"scope": selection.scope, "tier": selection.tier,
                       "size_band": selection.size_band,
                       "basis": ("the company's existing policy" if existing_policy else
                                 "no existing policy was supplied: the clauses below are the "
                                 "toolkit's new draft, and their text is what you are reviewing")},
            "clauses": clauses,
            "decisions_note": DECISIONS_NOTE,
            "decisions": selection.decisions,
            "findings": [_finding_row(f) for f in found],
            "obligations": _obligation_summary(obligations, found)}


def critic_input(found, reviews):
    """The critic's input: every finding (including what the reviewers added,
    already carrying the id `merge_reviews` assigned it -- see this module's
    docstring), plus what each reviewer endorsed or disputed.

    `added` is read off `found` itself (the findings with `kind == "reviewer"`),
    not off `reviews`' own raw `added` lists: `found` is the *merged* list, so
    its reviewer-added findings already carry their assigned id, which the
    critic needs to key a verdict to one (I6) -- rebuilding `added` from the
    unmerged review output never has an id to give it."""
    endorsements, disputes = {}, []
    for viewpoint in VIEWPOINTS:
        review = reviews.get(viewpoint) or {}
        for finding_id in review.get("endorsed", []):
            endorsements.setdefault(finding_id, []).append(viewpoint)
        for dispute in review.get("disputed", []):
            disputes.append({"viewpoint": viewpoint, "id": dispute.get("id"),
                             "why": dispute.get("why", "")})
    added = [dict(_finding_row(f), viewpoint=f.viewpoint) for f in found if f.kind == "reviewer"]
    return {"findings": [_finding_row(f) for f in found], "endorsements": endorsements,
            "disputes": disputes, "added": added}


def merge_reviews(found, reviews, selection):
    by_id = {finding.id: finding for finding in found}
    clauses = {clause.id: clause for clause in selection.selected}
    out = list(found)
    for viewpoint in VIEWPOINTS:
        review = reviews.get(viewpoint) or {}
        for finding_id in review.get("endorsed", []):
            finding = by_id.get(finding_id)
            if finding is None:
                continue
            seen = [part for part in finding.viewpoint.split(", ") if part]
            if viewpoint not in seen:
                seen.append(viewpoint)
            finding.viewpoint = ", ".join(seen)
        for addition in review.get("added", []):
            clause_id = addition.get("clause_id")
            clause = clauses.get(clause_id)
            if clause is None:
                continue   # a reviewer cannot invent a clause
            severity = addition.get("severity")
            if severity not in REVIEWER_SEVERITIES:
                severity = "high" if severity == "critical" else "medium"
            label = VIEWPOINT_LABELS[viewpoint]
            # I8, final gate review item 8: a reviewer's addition on a clause
            # that already has a non-contradiction finding -- the scripts'
            # own, or one an earlier reviewer already added in this same
            # merge -- is folded into it (both voices kept, in the detail;
            # severity raised to the higher of the two) rather than given a
            # second heading next to the first. Four clauses in the sample's
            # moderate gap report appeared twice, a few lines apart, before
            # this. A contradiction is never folded into: it already cites
            # both documents and outranks anything a reviewer adds, so it is
            # left exactly as found.
            existing = next((f for f in out if f.clause_id == clause_id and f.kind != "contradiction"),
                            None)
            if existing is not None:
                if (findings_module.SEVERITIES.index(severity)
                        < findings_module.SEVERITIES.index(existing.severity)):
                    existing.severity = severity
                voice = " ".join(part for part in
                                 (str(addition.get("summary", "") or "").strip(),
                                  str(addition.get("detail", "") or "").strip()) if part)
                note = ("{label} would also raise this: {voice}".format(label=label, voice=voice)
                       if voice else "{label} would also raise this.".format(label=label))
                existing.detail = " ".join(part for part in (existing.detail, note) if part).strip()
                seen = [part for part in existing.viewpoint.split(", ") if part]
                if viewpoint not in seen:
                    seen.append(viewpoint)
                existing.viewpoint = ", ".join(seen)
                continue
            detail = addition.get("detail") or ""   # null is "nothing to add"
            out.append(findings_module.Finding(
                id="reviewer:{viewpoint}:{clause_id}:{len_out}".format(
                    viewpoint=viewpoint, clause_id=clause_id, len_out=len(out)),
                severity=severity,
                kind="reviewer", clause_id=clause_id,
                clause_title=clause.title, section=clause.section,
                section_title=library.SECTION_TITLES.get(clause.section, ""),
                summary=addition.get("summary", ""),
                detail="{label} would raise this. {detail}".format(
                    label=label, detail=detail).strip(),
                viewpoint=viewpoint))
    severity_order = findings_module.SEVERITIES
    section_order = findings_module.section_order_key()
    clause_order = findings_module.clause_order_key(selection)
    out.sort(key=lambda f: (severity_order.index(f.severity), section_order.get(f.section, 99),
                            clause_order.get(f.clause_id, 99999)))
    return out


def apply_critic(found, verdicts):
    out = []
    for finding in found:
        verdict = (verdicts or {}).get(finding.id) or {}
        decision = verdict.get("verdict")
        if decision == "drop" and finding.kind != "contradiction":
            continue
        if decision == "disagreement":
            why = verdict.get("why", "")
            finding.detail = "{detail} Reviewers disagree: {why}".format(
                detail=finding.detail, why=why).strip()
        out.append(finding)
    return out
