---
{
  "id": "liq-minimum-buffer",
  "section": "08-liquidity",
  "title": "Minimum liquidity buffer",
  "scope": "treasury",
  "tier": "lite",
  "applies_if": "always",
  "omit_reason": "",
  "parameters": [
    {"id": "liq-buffer-days", "label": "Minimum liquidity buffer, days of forecast operating outflows",
     "typical": {"small": "30 days", "mid": "45 days"}, "decision": "board"},
    {"id": "liq-buffer-minimum-eur", "label": "Minimum liquidity buffer, absolute floor",
     "typical": {"small": "EUR 500,000", "mid": "EUR 2,000,000"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

The company holds a minimum liquidity buffer of cash and undrawn facilities equal to the greater
of {{liq-buffer-days}} of forecast outflows or {{liq-buffer-minimum-eur}}, and does not let it
fall below this without board approval.

## Full

The company maintains, at all times, a minimum liquidity buffer of unrestricted cash and undrawn
committed facilities equal to the greater of {{liq-buffer-days}} of forecast operating outflows
or {{liq-buffer-minimum-eur}}. The buffer is measured against the rolling cash flow forecast, not
against a forecast made at the start of the year, and excludes any facility that is uncommitted
or subject to a material adverse change clause the lender has invoked.

A breach of the buffer, whether actual or forecast within the planning horizon, is reported to
the board without delay, together with the action proposed to restore it. The buffer is a minimum
the company does not plan to test: it is set so that a single forecasting error or a delayed
receipt does not put the company's ability to pay its obligations at risk.
