---
{
  "id": "fx-hedge-ratio-bands",
  "section": "04-fx",
  "title": "Hedge ratio bands by tenor",
  "scope": "both",
  "tier": "lite",
  "applies_if": {"all": [{"field": "exposures.currencies", "exists": true}]},
  "omit_reason": "The company reported no foreign currency exposure, so hedge ratio bands by tenor do not apply.",
  "parameters": [
    {"id": "ratio-0-6m", "label": "Minimum hedge ratio, 0 to 6 months",
     "typical": {"small": "60-80%", "mid": "70-90%"}, "decision": "board"},
    {"id": "ratio-7-12m", "label": "Minimum hedge ratio, 7 to 12 months",
     "typical": {"small": "40-60%", "mid": "50-70%"}, "decision": "board"},
    {"id": "ratio-13-24m", "label": "Minimum hedge ratio, 13 to 24 months",
     "typical": {"small": "20-40%", "mid": "30-50%"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

The company hedges forecast currency exposure within minimum ratio bands that step down the
further ahead the exposure falls: at least {{ratio-0-6m}} for the next six months,
{{ratio-7-12m}} out to twelve months, and {{ratio-13-24m}} out to twenty-four months.

## Full

The company hedges each layer of its forecast currency exposure within minimum ratio bands set
by the board, so that certainty increases as the exposure draws nearer. For exposure falling due
in the next six months the company hedges at least {{ratio-0-6m}}; for exposure falling due
between seven and twelve months, at least {{ratio-7-12m}}; and, for the part of thirteen to
twenty-four months that falls within the maximum hedging horizon set under the hedging horizon
and layering clause, at least {{ratio-13-24m}}. A band beyond that horizon does not apply,
because the company does not hedge exposure that far out.

These are minimums, not targets, and the company may hedge above them within the instruments
permitted elsewhere in this policy. Bands are measured against the rolling forecast at each
review, not against a single forecast made in the past. The board reviews the bands as part of
each review of this policy under the approval and review cycle clause.
