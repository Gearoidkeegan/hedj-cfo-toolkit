---
{
  "id": "reg-emir-reporting-risk-mitigation",
  "section": "13-regulatory",
  "title": "EMIR reporting and risk mitigation",
  "scope": "both",
  "tier": "moderate",
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

The company confirms, for each counterparty, who reports its derivative transactions and that
its own identifier for that purpose is current, and it applies the risk mitigation techniques
EMIR requires for uncleared transactions. Whether a transaction is subject to the clearing
obligation or to margin exchange follows the classification confirmed under the EMIR
classification and clearing threshold clause.

## Full

Reporting applies to the company's derivative transactions generally, whatever its
classification under EMIR: what varies is who submits the report, not whether a report is made.
Where the company's own classification means it is not required to report an over-the-counter
derivative itself, and its counterparty for that transaction is a financial counterparty, the
financial counterparty generally reports it on the company's behalf instead; that arrangement does
not extend to exchange-traded derivatives, which the company reports itself or through its broker.
Either way, the finance
director confirms with each counterparty, before dealing with it and at each review, who reports
the transaction and, where the company must report it itself, how that is done. The company
keeps its own legal entity identifier current at all times, because neither the company nor a
counterparty can report a transaction against an identifier that has lapsed.

Risk mitigation for a derivative that is not centrally cleared also applies generally, whatever
the company's classification: the finance director confirms with each counterparty the
arrangements for timely trade confirmation, portfolio reconciliation and dispute resolution, at
a frequency for reconciliation that depends on the company's classification and the size of its
portfolio with that counterparty.

What does depend on the company's classification and clearing-threshold position, confirmed
under the EMIR classification and clearing threshold clause, is whether a derivative is subject
to the clearing obligation and whether margin must be exchanged on it. The finance director
confirms the position on both alongside that classification, and reports it to the board.
