---
{
  "id": "fund-covenant-headroom",
  "section": "10-funding",
  "title": "Covenant monitoring and headroom",
  "scope": "treasury",
  "tier": "moderate",
  "applies_if": {"all": [{"field": "debt.has_covenants", "eq": true}]},
  "omit_reason": "The company's borrowings carry no financial covenants to monitor.",
  "parameters": [
    {"id": "fund-headroom-threshold", "label": "Minimum covenant headroom that triggers reporting to the board",
     "typical": {"small": "15%", "mid": "20%"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

The finance director tests every financial covenant at each measurement date and reports to the
board immediately if headroom on any test falls below {{fund-headroom-threshold}}, ahead of any
actual breach.

## Full

The finance director calculates the company's position against every financial covenant in its
facility agreements at each measurement date required by those agreements, and also on a forecast
basis between measurement dates where a covenant is a material constraint on the company's plans.

Headroom is the margin between the company's actual or forecast position and the level at which a
covenant would be breached, expressed as a percentage of the covenant's permitted level. Where
headroom on any covenant test falls below {{fund-headroom-threshold}}, the finance director
reports it to the board immediately, without waiting for the next scheduled report, together with
the action available to restore headroom.

This clause is a forward-looking monitoring duty, separate from the breaches and escalation
clause, which applies once a covenant has actually been breached rather than while headroom is
merely narrow.
