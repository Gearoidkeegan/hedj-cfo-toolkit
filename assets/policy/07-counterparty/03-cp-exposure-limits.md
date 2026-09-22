---
{
  "id": "cp-exposure-limits",
  "section": "07-counterparty",
  "title": "Exposure limits and concentration",
  "scope": "both",
  "tier": "moderate",
  "applies_if": "always",
  "omit_reason": "",
  "parameters": [
    {"id": "cp-concentration-limit",
     "label": "Maximum exposure to a single counterparty, % of cash and settlement exposure",
     "typical": {"small": "40%", "mid": "25%"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

No single counterparty holds more than {{cp-concentration-limit}} of the company's cash and
settlement exposure, measured across deposits, undrawn facilities relied on for liquidity, and
the value of open hedges.

## Full

The company limits its exposure to any single counterparty, so that the failure of one does not
put a disproportionate share of the company's cash or open positions at risk. Exposure to a
counterparty is measured as the sum of cash and short-term deposits held with it, the
mark-to-market value in the company's favour of any open hedge with it, and the amount of any
undrawn facility the company relies on for its liquidity plan.

No single counterparty's exposure, measured this way, exceeds {{cp-concentration-limit}} of the
company's total exposure across all counterparties. The finance director monitors this limit as
balances and positions change during the period, not only at the point a transaction is placed,
and reports a breach under the breaches and escalation clause.
