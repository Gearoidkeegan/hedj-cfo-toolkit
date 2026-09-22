---
{
  "id": "inv-issuer-fund-limits",
  "section": "09-investment",
  "title": "Issuer and fund limits",
  "scope": "treasury",
  "tier": "moderate",
  "applies_if": {"all": [{"field": "cash.holds_investments", "eq": true}]},
  "omit_reason": "The company holds its surplus cash on deposit and makes no investment beyond that, so this clause does not apply.",
  "parameters": [
    {"id": "inv-issuer-limit", "label": "Maximum investment with a single issuer or fund, % of surplus cash invested",
     "typical": {"small": "25%", "mid": "15%"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

No more than {{inv-issuer-limit}} of the company's surplus cash invested under this section is
placed with a single issuer, deposit-taker or fund, so the loss of one does not put a
disproportionate share at risk.

## Full

The company limits its exposure to any single issuer, deposit-taker or money-market fund, so
that the failure of one does not put a disproportionate share of the company's invested surplus
cash at risk. Exposure to an issuer or fund is measured as the total of every deposit and
investment held with it or in it, across every currency and tenor.

No single issuer, deposit-taker or fund's exposure, measured this way, exceeds {{inv-issuer-limit}}
of the company's total surplus cash invested under this section. A government or supranational
issuer permitted under the permitted instruments clause is exempt from this limit, reflecting its
different risk profile from a bank deposit or a fund.

The finance director monitors this limit as balances change during the period, not only when an
investment is placed, and reports a breach under the breaches and escalation clause.
