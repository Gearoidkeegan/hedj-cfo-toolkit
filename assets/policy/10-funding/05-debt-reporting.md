---
{
  "id": "fund-debt-reporting",
  "section": "10-funding",
  "title": "Debt reporting",
  "scope": "treasury",
  "tier": "lite",
  "applies_if": {"all": [{"field": "debt.has_debt", "eq": true}]},
  "omit_reason": "The company has no external borrowings.",
  "parameters": [
    {"id": "fund-reporting-frequency", "label": "Frequency of debt and covenant reporting to the board",
     "typical": {"small": "quarterly", "mid": "monthly"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

The finance director reports outstanding debt, facility headroom, covenant compliance and
maturities to the board {{fund-reporting-frequency}}.

## Full

The finance director reports to the board {{fund-reporting-frequency}} on the company's
outstanding debt: the amount drawn and available under each facility, the interest rate or
margin applying, headroom against every financial covenant, and the maturity date of each
facility.

This report is distinct from the treasury report given under the reporting to the board clause,
and may be combined with it, but the frequency set here applies even where the general treasury
report is given less often. Where the finance director has already reported a specific matter to
the board separately, whether as part of covenant monitoring or under the refinancing window
clause, this report cross-refers to it rather than repeating it.

The finance director keeps the debt schedule underlying this report current and available to the
board between reports.
