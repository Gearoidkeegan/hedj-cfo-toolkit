---
{
  "id": "pay-authority-limits",
  "section": "11-payments",
  "title": "Payment and transfer limits",
  "scope": "treasury",
  "tier": "lite",
  "applies_if": "always",
  "omit_reason": "",
  "parameters": [
    {"id": "limit-payment-approve", "label": "Maximum single payment approval",
     "typical": {"small": "EUR 250,000", "mid": "EUR 1,000,000"}, "decision": "board"},
    {"id": "limit-account-move-funds", "label": "Maximum transfer between the company's own accounts",
     "typical": {"small": "EUR 1,000,000", "mid": "EUR 5,000,000"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

Payments are approved up to {{limit-payment-approve}}, and transfers between the company's own
accounts up to {{limit-account-move-funds}}.

## Full

Payments may be approved up to {{limit-payment-approve}}; transfers between the company's own
accounts may be made up to {{limit-account-move-funds}}. These limits sit alongside the hedging
transaction limit set under the delegated authority limits clause, and no limit may be exceeded
by splitting a transaction.

A payment or transfer above the limit that applies to it requires approval by the board or by
two directors. This limit is separate from, and in addition to, the dual authorisation threshold
in the dual authorisation and limits clause: a payment within this approval limit may still
require two signatories to release it once its value passes that threshold.

Limits are recorded in the authority matrix the board approves with this policy, together with
the person holding each delegation.
