---
{
  "id": "fx-hedging-horizon",
  "section": "04-fx",
  "title": "Hedging horizon and layering",
  "scope": "both",
  "tier": "moderate",
  "applies_if": {"all": [{"field": "exposures.currencies", "exists": true}]},
  "omit_reason": "The company reported no foreign currency exposure, so hedging horizon and layering do not apply.",
  "parameters": [
    {"id": "fx-horizon-max", "label": "Maximum hedging horizon",
     "typical": {"small": "12 months", "mid": "24 months"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

The company builds up its hedge cover in layers as an exposure moves closer, rather than
hedging it all at once, and does not hedge beyond {{fx-horizon-max}} without specific board
approval.

## Full

The company hedges its forecast currency exposure by layering: cover is added progressively as
an exposure moves nearer and the forecast becomes more reliable, rather than hedged in a single
transaction at the point the forecast is first made. Layering is applied within the minimum
ratio bands set out in the hedge ratio bands by tenor clause.

The company does not hedge exposure falling due more than {{fx-horizon-max}} ahead without
specific board approval, because a forecast that far out is less reliable and a hedge entered
into too early may need to be unwound. Where a firm commitment, such as a signed contract,
extends beyond that horizon, the board may approve hedging it as a specific exception under the
exceptions and waivers clause.
