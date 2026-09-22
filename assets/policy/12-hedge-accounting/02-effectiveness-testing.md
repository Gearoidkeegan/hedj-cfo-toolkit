---
{
  "id": "ha-effectiveness-testing",
  "section": "12-hedge-accounting",
  "title": "Effectiveness testing",
  "scope": "both",
  "tier": "moderate",
  "applies_if": {"any": [
    {"field": "exposures.currencies", "exists": true},
    {"field": "exposures.commodities", "exists": true},
    {"field": "exposures.floating_rate_debt", "eq": true}
  ]},
  "omit_reason": "The company hedges no exposure under this policy, so hedge accounting does not arise.",
  "parameters": [],
  "regulation": [],
  "adaptable": false
}
---

## Short

Where hedge accounting is applied, the company tests at each reporting date whether the hedge
relationship remains effective against the standard set by its reporting framework, and stops
applying hedge accounting to a relationship that fails that test.

## Full

Where the company applies hedge accounting to a hedge relationship under the framework and
designation clause, it tests, at each reporting date and for as long as the relationship
continues, whether the hedge remains effective: whether changes in the value or cash flows of
the hedging instrument continue to offset changes in the hedged item to the extent, and by the
method, its reporting framework requires.

The test uses the method, frequency and threshold set by the company's own reporting framework,
not a method this policy chooses on the company's behalf. Where a hedge relationship fails the
test, the company stops applying hedge accounting to it from that point, accounts for the
hedging instrument on the basis its reporting framework otherwise prescribes, and records why
the relationship failed.

Testing, and its result at each reporting date, is documented and kept with the designation made
under the framework and designation clause, so that the company's auditors can review the full
history of a hedge relationship, not only its position at the current reporting date.
