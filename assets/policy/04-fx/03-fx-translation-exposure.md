---
{
  "id": "fx-translation-exposure",
  "section": "04-fx",
  "title": "Translation exposure",
  "scope": "both",
  "tier": "moderate",
  "applies_if": {"all": [{"field": "exposures.currencies", "exists": true}, {"field": "structure.entities", "gte": 2}]},
  "omit_reason": "The company has a single entity, so no foreign-currency balance sheet is translated into the reporting currency.",
  "parameters": [],
  "regulation": [],
  "adaptable": false
}
---

## Short

Translation exposure arises when a controlled entity's results or net assets, reported in a
currency other than the company's reporting currency, are translated for consolidation. The
company monitors this exposure but does not usually hedge it with financial instruments.

## Full

Translation exposure arises when the company consolidates the results and net assets of a
controlled entity that reports in a currency other than the company's own reporting currency.
Movements in the exchange rate between the two currencies change the reported value of that
entity's results and net assets on consolidation, without any cash flow arising from the
movement itself.

Because translation exposure does not directly affect the company's cash flow, the company
monitors it and reports its effect to the board, but does not hedge it with financial
instruments unless the board specifically approves a hedge, for example a foreign-currency
borrowing used to offset the net assets of a foreign entity. Where translation exposure and
transaction exposure arise from the same underlying currency, the company manages the
transaction exposure first, under the rest of this section, before considering any separate
hedge of the translation effect.
