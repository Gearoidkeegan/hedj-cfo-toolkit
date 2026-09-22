---
{
  "id": "inv-permitted-instruments",
  "section": "09-investment",
  "title": "Permitted instruments",
  "scope": "treasury",
  "tier": "lite",
  "applies_if": {"all": [{"field": "cash.holds_investments", "eq": true}]},
  "omit_reason": "The company holds its surplus cash on deposit and makes no investment beyond that, so this clause does not apply.",
  "parameters": [
    {"id": "inv-permitted-products", "label": "Surplus cash investment instruments the board permits",
     "typical": {"small": "bank deposits", "mid": "bank deposits and money-market funds"},
     "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

The board chooses which of bank deposits, money-market funds and short-dated government paper the
company uses, recorded as {{inv-permitted-products}}. Equities and structured deposits are never
permitted.

## Full

Surplus cash may be invested in bank deposits, money-market funds and short-dated government or
supranational paper: these are the candidate instruments from which the board chooses when it
approves this policy. Which of them the company uses, and in what combination, is the board's
decision, recorded as {{inv-permitted-products}}.

Every instrument used is capital-guaranteed or, in the case of a money-market fund, managed to
preserve capital and maintain same-day or next-day liquidity. Equities, structured deposits, and
any instrument that is not capital-guaranteed or does not meet this liquidity standard are never
permitted, whatever the board's choice of instrument under this clause.

An investment made under this clause is denominated in a currency the company holds an operating
need for, or is otherwise addressed under the foreign exchange risk section of this policy.
