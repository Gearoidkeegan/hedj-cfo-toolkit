---
{
  "id": "liq-concentration-pooling",
  "section": "08-liquidity",
  "title": "Cash concentration and pooling",
  "scope": "treasury",
  "tier": "moderate",
  "applies_if": {"all": [{"field": "cash.bank_count", "gte": 2}]},
  "omit_reason": "The company holds its cash with a single bank, so cash concentration and pooling arrangements do not arise.",
  "parameters": [
    {"id": "liq-pooling-sweep-threshold", "label": "Minimum balance retained in a subsidiary account before a sweep",
     "typical": {"small": "EUR 10,000", "mid": "EUR 50,000"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

The company concentrates surplus cash from its subsidiary and group accounts into a central
account by sweep, leaving at least {{liq-pooling-sweep-threshold}} in each account to meet its
own operating needs.

## Full

The company operates a cash concentration structure that sweeps surplus balances from subsidiary
and group accounts into a central account, automatically or on manual instruction, at the
frequency the finance director sets. Each account retains at least {{liq-pooling-sweep-threshold}}
for its own operating needs before a sweep is made from it.

Concentrated cash is applied to fund a deficit elsewhere in the group, to reduce external
borrowing, or, where the company invests surplus cash, in the order of priority the company sets
for that. A sweep between accounts held by different group entities creates a balance owed
between them: the finance director records it, reports it to the board with the treasury report,
and settles or formalises it rather than letting it stand indefinitely.

Before a new entity or account is added to the structure, the finance director confirms that
pooling is legally permitted in the jurisdictions involved and does not conflict with a
restriction on cash movement in a facility agreement.
