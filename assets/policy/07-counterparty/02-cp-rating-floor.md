---
{
  "id": "cp-rating-floor",
  "section": "07-counterparty",
  "title": "Credit quality and rating floors",
  "scope": "both",
  "tier": "moderate",
  "applies_if": {"all": [{"field": "cash.rated_counterparty_access", "eq": "broad"}]},
  "omit_reason": "The company's banking relationships are not usually with counterparties that carry a public credit rating, so requiring a minimum rating would rule out the banks that already lend to a company of this size.",
  "parameters": [
    {"id": "cp-min-rating", "label": "Minimum long-term credit rating",
     "typical": {"small": "BBB-/Baa3 (investment grade)", "mid": "A-/A3 (upper investment grade)"},
     "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

Where the company has access to counterparties that carry a public credit rating, it deals only
with one rated at least {{cp-min-rating}} long-term, and confirms the rating before dealing and
again at each review.

## Full

Where the company has access to a range of counterparties that carry a public long-term credit
rating, the company deals only with a counterparty rated at least {{cp-min-rating}}, confirmed
from a published source before the counterparty is first approved and again at each review of
the approved counterparty list.

Where a counterparty's rating falls below the floor, the finance director reports this to the
board and the counterparty is not used for new transactions until the board decides whether to
keep it on the approved list, reduce the company's exposure to it, or accept the position as an
exception under the exceptions and waivers clause. A rating floor is not applied where the
company's realistic choice of counterparties does not itself meet it.
