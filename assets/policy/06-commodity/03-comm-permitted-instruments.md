---
{
  "id": "comm-permitted-instruments",
  "section": "06-commodity",
  "title": "Permitted instruments",
  "scope": "both",
  "tier": "lite",
  "applies_if": {"all": [{"field": "exposures.commodities", "exists": true}]},
  "omit_reason": "The company reported no commodity or energy exposure, so permitted instruments do not apply.",
  "parameters": [
    {"id": "comm-permitted-products", "label": "Commodity instruments the board permits",
     "typical": {"small": "forwards", "mid": "forwards and swaps"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

The board chooses which of forwards and swaps the company uses, matched to an underlying
purchase or sale, recorded as {{comm-permitted-products}}. Options are not among these
instruments, and leveraged structures are not used.

## Full

Commodity price risk may be hedged using forwards and swaps, matched to an underlying purchase
or sale: these are the candidate instruments from which the board chooses when it approves this
policy. Which of them the company uses is the board's decision, recorded as
{{comm-permitted-products}}.

Options are not among the candidate instruments for commodity price risk, and any leveraged,
knock-in or knock-out structure is not used. Every instrument used under this clause is matched
to an underlying commodity purchase or sale, in the same commodity and broadly the same volume as
the exposure being hedged, and is never entered into as a speculative position.
