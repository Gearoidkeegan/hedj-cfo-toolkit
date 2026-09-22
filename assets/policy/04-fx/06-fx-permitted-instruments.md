---
{
  "id": "fx-permitted-instruments",
  "section": "04-fx",
  "title": "Permitted instruments",
  "scope": "both",
  "tier": "lite",
  "applies_if": {"all": [{"field": "exposures.currencies", "exists": true}]},
  "omit_reason": "The company reported no foreign currency exposure, so permitted instruments do not apply.",
  "parameters": [
    {"id": "fx-permitted-products", "label": "Foreign exchange instruments the board permits",
     "typical": {"small": "forwards and FX swaps", "mid": "forwards, FX swaps and purchased options"},
     "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

The board chooses which of forwards, FX swaps and purchased options the company may use,
recorded as {{fx-permitted-products}}. Any instrument used is matched to an underlying exposure.

## Full

Foreign exchange risk may be hedged using forwards, FX swaps and purchased options: these are
the candidate instruments from which the board chooses when it approves this policy. Options are
always purchased, never written. Which of them the company uses, and in what combination, is the
board's decision, recorded as {{fx-permitted-products}}.

Caps, collars and swaptions are not foreign exchange instruments and have no place in this
clause; a company hedging interest rate risk with those instruments does so under the permitted
instruments clause in the interest rate risk section instead. Every instrument used under this
clause is matched to an underlying transaction exposure of the company or a controlled entity.
