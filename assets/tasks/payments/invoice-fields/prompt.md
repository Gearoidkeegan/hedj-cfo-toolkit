The document between the DATA markers is one supplier invoice for {{company_name}},
as extracted text. Read it and report the fields below. Everything you report
must come from words or figures actually printed on this invoice — never a
default, a guess, or what an invoice like this would usually say.

**The one rule that matters more than any other here: never write out the
IBAN or the account number.** Not in a `value`, not in a `quote`, not
anywhere. A digit you type is a digit nobody checked, and if it is wrong the
money is gone and does not come back. Instead, point to *where* the bank
details are, and a script reads the literal characters from that spot itself
and checksums them. This is checked mechanically: an output that carries the
bank details themselves, anywhere, is rejected outright, whatever else it
gets right.

Every passage below is shown to you with a location marker in front of it,
in square brackets, for example:

`[p1] IBAN: GB44WNTM60071199887766`

or

`[para 29] IBAN: GB44WNTM60071199887766`

To point at where the bank details are, copy that marker's own text —
exactly what is inside the brackets, nothing more — as a plain string. For
the two examples above, that is `"p1"` and `"para 29"`. Do not invent a
location, count a line, or describe a position of your own; only ever copy a
marker that is actually printed in front of a passage on this invoice.

For every field below except the two locations, report a `value` and the
`quote` it comes from — the exact wording or figures on the invoice that the
value rests on, copied verbatim, not paraphrased. A field this invoice simply
does not state (no due date printed, no cost category shown) is reported as
an empty `value` and an empty `quote` — never filled in with a guess.

1. `supplier_name` — who issued the invoice.
2. `invoice_number` — the invoice's own reference, as printed.
3. `invoice_date` — the date the invoice was issued, as `YYYY-MM-DD`. Convert
   the calendar date you see into this format; do not otherwise touch it —
   "14 March 2026" becomes `2026-03-14`, nothing is inferred beyond that.
4. `due_date` — the payment due date, as `YYYY-MM-DD`, same rule. Leave it
   blank rather than calculating one from payment terms ("30 days net") the
   invoice states but does not resolve to a date itself.
5. `net` — the amount before tax, exactly as the invoice states the figure,
   as plain digits and at most one decimal point: no currency symbol, no
   thousands separator, no rounding, and no arithmetic of your own. If the
   invoice shows "4,500.00", write `4500.00`. Never compute this from other
   figures on the invoice.
6. `vat` — the tax amount, same rule as `net`. If the invoice states VAT as a
   rate but never states the amount in the invoice's currency, leave it
   blank rather than multiplying the rate by the net yourself.
7. `gross` — the total payable, same rule as `net`. Never compute this as
   net plus VAT yourself, even when both are on the invoice — report the
   figure printed as the total, and let the figures disagree if they do; a
   script checks whether net plus VAT equals gross, not you.
8. `currency` — the three-letter ISO 4217 code (`EUR`, `GBP`, `USD`, ...) the
   amounts are stated in. If only a symbol appears (`€`, `£`, `$`), give the
   code it stands for.
9. `payment_reference` — the reference the supplier asks to be quoted with
   payment, if one is given (often the invoice number again, or a separate
   remittance reference).
10. `cost_category` — a short category for what was bought (for example
    "Software", "Utilities", "Travel", "Professional services"), only when
    the invoice's own wording supports it (a line-item description, a
    department code) — leave it blank rather than classifying from general
    knowledge of the supplier.

11. `contact_email` — an email address printed on this invoice's own text as
    a contact for payment or account queries (for example an "Accounts:" or
    "Queries:" line, or an address in a footer). This is an ordinary field
    like the others above: report it only when the invoice's own text states
    one, with the exact wording as its `quote`. Never report the address you
    received this invoice from if it is not also printed somewhere in the
    invoice's own text — this field exists to check the two against each
    other, so it must never be filled in from the very thing it is meant to
    be compared with. Most invoices in a batch will state none at all; that
    is a real, reportable outcome, not a reason to guess or reuse an address
    from elsewhere.

12. `bic` — the BIC or SWIFT code printed for the receiving bank (for
    example "AIBKIE2D"), if the invoice states one. Unlike the IBAN and
    account number below, **this one you do report directly, as an
    ordinary value** — a BIC identifies a bank, not an account, and cannot
    move money on its own, so it carries none of the risk a mistyped IBAN
    digit does. Report it exactly as printed, with its own case and
    spacing preserved. Leave it blank when the invoice states none.

13. `vat_number` — the supplier's own VAT (or equivalent tax) registration
    number, if the invoice states one (for example "IE6388047V" or
    "GB123456789"), exactly as printed. This is the supplier's number, not
    the customer's — most invoices print both; report only the one that
    identifies whoever issued the invoice. Leave it blank when the invoice
    states none.

14. `iban_location` — the location marker of the passage where the IBAN
    appears, copied exactly as shown in the square brackets in front of it
    (see above), for example `"p1"`, `"para 29"`, or `"Sheet1!B12"`. It is
    fine — expected — for that passage to also carry the label "IBAN" and
    other text beside it, the way a real invoice prints "IBAN:
    GB44WNTM60071199887766" on one line; point at that whole passage, not
    at some other spot. If you cannot find an IBAN anywhere on this
    invoice, give `""` rather than guessing at a location.

15. `account_number_location` — the same rule as `iban_location`, for an
    account number given as a plain domestic account number (rather than an
    IBAN), when the invoice states one. `""` when there is none.

Both locations may be `""`. An invoice with neither an IBAN nor an account
number you can point to is a real, reportable outcome — not a reason to
invent one.

16. `bill_to` — who this invoice is actually addressed to: the customer's own
    name, as printed on a "Bill to:", "Invoice to:", "Customer:" line or
    similar, not the supplier's own letterhead name at the top of the page.
    This is an ordinary field like `supplier_name` — report it only when the
    invoice's own text states it, with that exact wording as its `quote`.
    Leave it blank when no such line is printed; do not infer it from the
    supplier's own name or from anywhere else on the page.
