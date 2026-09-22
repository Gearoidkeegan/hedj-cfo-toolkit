---
{
  "id": "reg-emir-classification",
  "section": "13-regulatory",
  "title": "EMIR classification and clearing threshold",
  "scope": "both",
  "tier": "lite",
  "applies_if": {"any": [
    {"field": "exposures.currencies", "exists": true},
    {"field": "exposures.commodities", "exists": true},
    {"field": "exposures.floating_rate_debt", "eq": true}
  ]},
  "omit_reason": "The company enters into no derivative transactions under this policy, so its EMIR classification does not arise.",
  "parameters": [],
  "regulation": ["EMIR"],
  "adaptable": false
}
---

## Short

The company confirms its classification and clearing-threshold position under EMIR at least
annually, and whenever its hedging activity changes materially.

## Full

The company confirms, at least annually and whenever its hedging activity changes materially,
its classification under EMIR as a financial counterparty or a non-financial counterparty, and
whether its derivative activity remains below the clearing threshold that applies to its
counterparty type. The clearing threshold is assessed against the company's derivative activity
over a rolling twelve-month period, which is why the confirmation is repeated at least annually
rather than made once and left unchanged. This is a classification to be confirmed against the
company's own facts and current guidance, not a status this policy assumes or asserts, and it
applies equally to a company established in the EU or in the UK, each of which maintains its own
version of this regime.

Where the finance director has any doubt about the company's classification or its threshold
calculation, the position is confirmed with a professional adviser before the company relies on
it. The confirmation, and the calculation behind it, is recorded and reported to the board
alongside the other regulatory confirmations in this policy, and the company acts on the
outcome, including under the EMIR reporting and risk mitigation clause, rather than treating the
classification as fixed once made.
