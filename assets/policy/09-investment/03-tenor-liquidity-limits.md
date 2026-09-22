---
{
  "id": "inv-tenor-liquidity-limits",
  "section": "09-investment",
  "title": "Tenor and liquidity limits",
  "scope": "treasury",
  "tier": "moderate",
  "applies_if": {"all": [{"field": "cash.holds_investments", "eq": true}]},
  "omit_reason": "The company holds its surplus cash on deposit and makes no investment beyond that, so this clause does not apply.",
  "parameters": [
    {"id": "inv-max-tenor", "label": "Maximum tenor of a single investment",
     "typical": {"small": "3 months", "mid": "12 months"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

No single investment of surplus cash has a tenor longer than {{inv-max-tenor}}, and investments
are staggered so that a portion matures regularly to meet the company's liquidity plan.

## Full

No single investment made under the permitted instruments clause has a tenor, at the time it is
made, longer than {{inv-max-tenor}}. Tenor is measured to the earliest date the company can
realise the investment at its invested value, not to a later final maturity date.

Investments are staggered across their permitted tenors so that a portion matures within each
period of the rolling cash flow forecast, and the company is not exposed to needing to realise an
investment early, at a loss or a reduced return, to meet a cash requirement. The finance director
sets the staggering within the maximum tenor set by the board.

An investment is realised before its maturity only where the forecast shows the cash is needed
sooner than expected, and the realisation is reported to the board under the reporting to the
board clause.
