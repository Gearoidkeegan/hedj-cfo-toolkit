---
{
  "id": "comm-hedge-ratio-horizon",
  "section": "06-commodity",
  "title": "Hedge ratio and horizon",
  "scope": "both",
  "tier": "moderate",
  "applies_if": {"all": [{"field": "exposures.commodities", "exists": true}]},
  "omit_reason": "The company reported no commodity or energy exposure, so hedge ratio and horizon do not apply.",
  "parameters": [
    {"id": "comm-hedge-ratio-min", "label": "Minimum hedge ratio, primary commodity exposure",
     "typical": {"small": "30-50%", "mid": "40-60%"}, "decision": "board"},
    {"id": "comm-horizon-max", "label": "Maximum hedging horizon",
     "typical": {"small": "6 months", "mid": "12 months"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

The company hedges at least {{comm-hedge-ratio-min}} of its forecast exposure to its primary
commodity, and does not hedge beyond {{comm-horizon-max}} ahead without specific board approval.

## Full

The company hedges at least {{comm-hedge-ratio-min}} of its forecast exposure to its primary
commodity over the horizon covered by this clause, measured against the rolling forecast at each
review rather than a forecast made in the past. Where the company has more than one material
commodity exposure, the board may set a different ratio for each, recorded alongside this
clause.

The company does not hedge commodity exposure falling due more than {{comm-horizon-max}} ahead
without specific board approval, because a forecast that far out is less reliable for a
commodity price than for a committed foreign currency flow. A firm, signed purchase or sales
contract that extends beyond that horizon may be hedged as a specific exception under the
exceptions and waivers clause.
