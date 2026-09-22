---
{
  "id": "fund-refinancing-window",
  "section": "10-funding",
  "title": "Refinancing window",
  "scope": "treasury",
  "tier": "moderate",
  "applies_if": {"all": [{"field": "debt.has_debt", "eq": true}]},
  "omit_reason": "The company has no external borrowings.",
  "parameters": [
    {"id": "fund-refi-window-months", "label": "Refinancing discussions begin this many months before facility maturity",
     "typical": {"small": "6 months", "mid": "12 months"}, "decision": "board"}
  ],
  "regulation": [],
  "adaptable": false
}
---

## Short

The finance director begins refinancing discussions at least {{fund-refi-window-months}} before a
committed facility's maturity date, so a replacement or extension is in place before it expires.

## Full

The finance director tracks the maturity date of every committed facility and begins discussions
on its renewal, extension or replacement at least {{fund-refi-window-months}} before that date.
This window is measured from the facility's maturity, not from the date a lender first raises the
subject.

Where more than one facility falls due within the same period, the finance director sequences the
discussions so the company is not seeking to refinance a disproportionate share of its total
committed facilities at the same time, and reports the refinancing plan and its progress to the
board.

If a facility cannot be renewed, extended or replaced within the window, the finance director
reports this to the board without delay, together with the options available to the company.
