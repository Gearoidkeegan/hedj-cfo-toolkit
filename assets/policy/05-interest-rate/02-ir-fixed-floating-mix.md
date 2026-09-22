---
{
  "id": "ir-fixed-floating-mix",
  "section": "05-interest-rate",
  "title": "Fixed and floating mix",
  "scope": "both",
  "tier": "lite",
  "applies_if": {"all": [{"field": "debt.has_debt", "eq": true}]},
  "omit_reason": "The company carries no external borrowings, so the fixed and floating mix does not apply.",
  "parameters": [
    {"id": "min-fixed-proportion", "label": "Minimum proportion of drawn debt at fixed rates",
     "typical": {"small": "40%", "mid": "50%"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

At least {{min-fixed-proportion}} of the company's drawn debt is held or hedged at a fixed rate
at all times; the balance may float, giving some benefit from a fall in rates.

## Full

The company keeps at least {{min-fixed-proportion}} of its drawn debt at a fixed rate of
interest, whether because the facility itself is drawn at a fixed rate or because a floating
rate drawing is hedged to a fixed rate using an instrument permitted under the permitted
instruments clause. The proportion is measured against total drawn debt, not against total
facility capacity, and is checked whenever a facility is drawn, repaid or refinanced.

The remaining, unfixed proportion may float, so that the company retains some benefit if rates
fall. Where a facility agreement itself sets a higher minimum fixed proportion, the higher
figure applies for as long as that facility is outstanding.
