The text between the DATA markers is one facility agreement, term sheet or
commitment letter for {{company_name}} -- or one part of one: a long agreement
is read in consecutive parts, each on its own, and the parts are put back
together afterwards. Extract only what this text itself states -- never a
market-standard term that is not written down, and never something you expect
another part of the agreement to say.

1. `facility_name`: how the document identifies itself or the lender (for
   example "Revolving Credit Facility Agreement", or the document's own
   title). When this part does not name the facility, leave it empty (`""`)
   rather than guessing.
2. `hedging_requirements`: each obligation to hedge. `kind` is
   `interest_rate` for a requirement on the proportion of drawn debt to be
   fixed, `currency` for a requirement to hedge a named foreign currency, or
   `commodity` for a requirement to hedge a proportion of a commodity or
   energy price exposure. Give every one of these fields the requirement
   itself states, whatever its kind, and leave out any it does not:
   - `proportion_pct_min`: the smallest proportion that must be hedged or
     fixed, as a percentage -- "not less than 60 per cent" is `60`.
   - `exposure_tenor_months`: how far ahead the exposure to be covered falls
     due -- "receipts falling due within the following twelve months" is `12`.
   - `minimum_period_months`: how long the obligation itself runs -- "such
     cover shall be maintained for not less than twenty-four months" is `24`.
   - `currency`: the three-letter code of the currency a currency requirement
     names.
   - `instruments_permitted`: the instruments the document names as ways of
     meeting the requirement.

   The two periods are different things and are often both stated in one
   requirement: never put one in the other's field, and never repeat a figure
   in a field the document does not state. "The Borrower shall hedge not less
   than 60 per cent of its forecast United States dollar receipts falling due
   within the following twelve months, and shall maintain such cover for not
   less than twenty-four months" is a single `currency` requirement with
   `proportion_pct_min` 60, `currency` "USD", `exposure_tenor_months` 12 and
   `minimum_period_months` 24.
3. `covenants`: each financial covenant test, with its definition as the
   document states it.
4. `reporting_duties`: each duty to report to the lender, with what must be
   reported and how often, when stated.
5. `restrictions`: each prohibition the facility places on the company,
   whether on further indebtedness, on granting security, or on how the
   company may run its treasury -- a restriction belongs here even when it
   has nothing to do with borrowing or security. `kind` is `indebtedness`
   for a restriction on further borrowing, `security` for a restriction on
   granting security, `hedging_counterparty` for a restriction on who the
   company may hedge with (for example, counterparties limited to those the
   lender approves), `speculation` for a derivative or position the
   facility forbids outright (for example, no position other than one
   matched to an underlying exposure), `bank_accounts` for a restriction on
   where cash may be held or which banks accounts may be opened with (for
   example, accounts opened only with banks on an approved list), or
   `investments` for a restriction on what surplus cash may be invested in
   (for example, surplus cash placed only in bank deposits).
   Write each `description` as one complete sentence saying what the facility
   forbids or requires, in the facility's own terms: "The Borrower may not
   enter into any derivative transaction for speculative purposes." It is
   printed after the clause number in a paper the board reads, so a sentence
   is what it has to be, not a heading and not a fragment.

Every entry needs a `quote`: the exact words it rests on, copied verbatim
from this text, and a `source`: the clause number or page it came from.
Do not include an entry you cannot quote. Leave a list empty when this text
has nothing of that kind. A part that holds no obligations at all -- the
definitions, say, or the signature pages -- is answered with all four lists
empty: that is a complete answer, not a failure.
