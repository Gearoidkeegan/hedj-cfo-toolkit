---
{
  "id": "liq-intercompany-funding",
  "section": "08-liquidity",
  "title": "Intercompany funding",
  "scope": "treasury",
  "tier": "moderate",
  "applies_if": {"all": [{"field": "structure.intercompany_flows", "eq": true}]},
  "omit_reason": "The company reported no intercompany flows between group entities.",
  "parameters": [
    {"id": "liq-intercompany-limit", "label": "Maximum intercompany loan outstanding to a single group entity",
     "typical": {"small": "EUR 500,000", "mid": "EUR 2,500,000"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

Intercompany loans between group entities are approved by the finance director, documented and
interest-bearing, and no loan to a single group entity exceeds {{liq-intercompany-limit}}
outstanding at any time.

## Full

A loan or advance made by the company to another group entity, or received from one, is
documented in an intercompany loan agreement stating the amount, interest rate, term and
repayment terms, even where the entities share common ownership and control.

The amount outstanding to a single group entity under such agreements, including any balance
created by cash concentration and pooling, does not exceed {{liq-intercompany-limit}} without
board approval. Interest is charged at a rate that reflects the currency and term of the loan, so
that intercompany funding is not used to move profit between entities without commercial basis.

The finance director maintains a record of every intercompany loan outstanding and reports the
total and the largest balances to the board under the reporting to the board clause.
