---
{
  "id": "pay-dual-authorisation-limits",
  "section": "11-payments",
  "title": "Dual authorisation and limits",
  "scope": "treasury",
  "tier": "lite",
  "applies_if": "always",
  "omit_reason": "",
  "parameters": [
    {"id": "pay-dual-auth-threshold", "label": "Payments above this value require two authorised signatories",
     "typical": {"small": "EUR 10,000", "mid": "EUR 50,000"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

Any payment above {{pay-dual-auth-threshold}} requires release by two authorised signatories; no
single signatory releases a payment above this value alone.

## Full

A payment above {{pay-dual-auth-threshold}} is released only once two authorised signatories have
each independently reviewed and approved it in the banking system; the same person cannot enter
and release such a payment alone, whatever their individual approval authority under the
payment and transfer limits clause. This dual authorisation applies to a single payment and to a
batch, measured by the value of the largest payment in the batch.

The dual authorisation requirement is separate from, and in addition to, the maximum single
payment approval limit in the payment and transfer limits clause: a payment within that approval
limit still requires two signatories to release it once its value passes
{{pay-dual-auth-threshold}}.

A payment below {{pay-dual-auth-threshold}} may be released by a single authorised signatory,
provided it is within their own approval authority.
