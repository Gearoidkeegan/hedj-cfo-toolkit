---
{
  "id": "fund-strategy-diversification",
  "section": "10-funding",
  "title": "Funding strategy and diversification",
  "scope": "treasury",
  "tier": "moderate",
  "applies_if": {"all": [{"field": "debt.has_debt", "eq": true}]},
  "omit_reason": "The company has no external borrowings.",
  "parameters": [
    {"id": "fund-single-lender-limit", "label": "Maximum proportion of committed facilities with a single lender",
     "typical": {"small": "70%", "mid": "50%"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

The company maintains more than one source of committed funding where its scale allows, and no
single lender provides more than {{fund-single-lender-limit}} of total committed facilities.

## Full

The company funds itself from more than one source and more than one lender where its scale and
the facilities available to it make this practical, so that the loss or withdrawal of a single
lender does not put the company's funding at risk. Funding sources may include committed bank
facilities, asset-based or receivables finance, and other structures the board approves.

No single lender provides more than {{fund-single-lender-limit}} of the company's total committed
facilities, measured by commitment rather than amount drawn. Where the company's scale means a
single facility from a single lender is unavoidable, the finance director records the
concentration this creates and the company's plan to diversify as it grows.

The finance director reviews the funding structure and its diversification against this limit as
part of the debt reporting clause.
