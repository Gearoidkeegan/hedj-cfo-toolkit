---
{
  "id": "fx-netting",
  "section": "04-fx",
  "title": "Netting and internal hedging",
  "scope": "both",
  "tier": "moderate",
  "applies_if": {"all": [{"field": "exposures.currencies", "exists": true}, {"field": "structure.intercompany_flows", "eq": true}]},
  "omit_reason": "The company has no intercompany flows to net.",
  "parameters": [
    {"id": "fx-netting-threshold", "label": "Minimum flow value eligible for netting",
     "typical": {"small": "EUR 50,000", "mid": "EUR 250,000"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

Before hedging externally, the company nets intercompany flows in the same currency due on the
same date against each other, where each flow is at least {{fx-netting-threshold}}. Only the
residual, unmatched exposure is hedged with an external counterparty.

## Full

Where two or more controlled entities have offsetting foreign currency flows due to or from each
other on or around the same date, the company nets them against each other before arranging any
external hedge, so long as each flow being netted is at least {{fx-netting-threshold}}; flows
below that value are not worth the administrative cost of netting separately and are included in
the wider exposure instead.

Only the exposure that remains after netting, described as the residual exposure, is hedged with
an external counterparty under the rest of this section. Netting does not remove the underlying
exposure from the company's forecast; it changes only how much of it needs an external
instrument. Where entities cannot net directly because of a legal, tax or regulatory
restriction, the company hedges the gross flows instead and records the restriction that
prevented netting.
