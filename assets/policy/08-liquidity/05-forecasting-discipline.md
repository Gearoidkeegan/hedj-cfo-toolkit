---
{
  "id": "liq-forecasting-discipline",
  "section": "08-liquidity",
  "title": "Forecasting discipline",
  "scope": "treasury",
  "tier": "lite",
  "applies_if": "always",
  "omit_reason": "",
  "parameters": [
    {"id": "liq-forecast-horizon", "label": "Minimum rolling cash flow forecast horizon",
     "typical": {"small": "13 weeks", "mid": "26 weeks"}, "decision": "board"},
    {"id": "liq-forecast-update-frequency", "label": "Minimum frequency the cash flow forecast is updated",
     "typical": {"small": "weekly", "mid": "daily"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

The finance director maintains a rolling cash flow forecast covering at least
{{liq-forecast-horizon}}, updated {{liq-forecast-update-frequency}} and checked against actual
cash flow.

## Full

The finance director maintains a rolling cash flow forecast covering at least
{{liq-forecast-horizon}}, broken down by week for the near term and by month beyond that, and
updates it at least {{liq-forecast-update-frequency}}, or more often if the company's cash
position is volatile.

Each forecast is compared against the actual cash flow it predicted, and a material variance is
investigated so that the method and assumptions behind the forecast improve over time. Where the
forecast reveals a breach, actual or expected, of the minimum liquidity buffer clause, the finance
director reports it under that clause without waiting for the next scheduled report to the board.

The finance director keeps the forecasting model and its assumptions available for review by the
board or its auditors.
