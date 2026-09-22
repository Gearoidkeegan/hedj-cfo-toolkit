---
{
  "id": "ir-permitted-instruments",
  "section": "05-interest-rate",
  "title": "Permitted instruments",
  "scope": "both",
  "tier": "lite",
  "applies_if": {"all": [{"field": "debt.has_debt", "eq": true}]},
  "omit_reason": "The company carries no external borrowings, so permitted instruments do not apply.",
  "parameters": [
    {"id": "ir-permitted-products", "label": "Interest rate instruments the board permits",
     "typical": {"small": "interest rate swaps", "mid": "interest rate swaps and caps"},
     "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

The board chooses which of interest rate swaps, caps and collars the company uses, matched to
an underlying borrowing, recorded as {{ir-permitted-products}}. Swaptions and leveraged
structures are not used.

## Full

Interest rate risk may be hedged using interest rate swaps, caps and collars, each matched to an
underlying borrowing: these are the candidate instruments from which the board chooses when it
approves this policy. Which of them the company uses is the board's decision, recorded as
{{ir-permitted-products}}.

Swaptions, and any leveraged, knock-in or knock-out structure, are not used. Every instrument
used under this clause is matched to an underlying borrowing in the same currency and broadly
the same amortisation profile as the instrument, and is never entered into as a speculative
position or to hedge a borrowing the company does not have.
