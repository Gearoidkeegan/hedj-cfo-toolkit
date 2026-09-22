# Sample payment run — the answer key

Twenty invoices making up a plausible month for a mid-sized company, plus the
supplier master they are checked against. **Twelve are ordinary.** Six issues are planted,
across eight files.

That ratio is the point. A pack where everything is suspicious teaches nothing
and makes the tool look as though it flags for the sake of flagging; the
twelve clean invoices are what show a CFO it does not. If the tool ever
raises something against one of those twelve, that is a defect worth
chasing, not a discovery.

Every company, bank, person, address, VAT number and IBAN here is invented.
The IBANs are checksum-valid so that validation passes on the clean ones.

Rebuild the pack with `python tests/tools/make_payments_samples.py`.

## What is planted, and where

| File | Issue | The check that should catch it |
|---|---|---|
| `13-ossington-valve-works.pdf` | The IBAN differs from the one on file for this supplier | `changed_bank_details` |
| `14-pentangle-strategic-consulting.pdf` | A supplier never seen before, invoicing far more than any other line in the run | `new_payee_large_amount` |
| `15-acrne-ltd.docx` | "Acrne Ltd" — an *rn* where the master has an *m* in "Acme Ltd" | `lookalike_supplier_name` |
| `16-ilkeston-freight-phishing.eml` | Sent from a near-miss of the real supplier's domain, with the invoice attached | `lookalike_sender_domain` |
| `17-wrenfield-managed-it.docx` | Hidden text addressed to an automated reader, telling it the invoice is pre-approved | `hidden_instructions` |
| `18-pemberton-cladding-roofing-1.pdf`, `19-pemberton-cladding-roofing-2.pdf`, `20-pemberton-cladding-roofing-3.pdf` | Three invoices, one supplier, one day, each below the approval limit but over it together | `split_to_stay_under` |

Six issues, eight files: the split pattern needs three invoices to exist at
all, which is exactly what makes it hard for a person to spot.

## Two files worth knowing about separately

**`12-quillon-fabrication-scanned.pdf` is image-only** and carries no text
layer. It is not a planted fraud — it is there because the tool must say it
could not read the document rather than skipping it in silence. A file that
vanishes from a payment run without comment is its own kind of failure.

**`17-wrenfield-managed-it.docx` is the one to show a room.** The hidden note
tells any automated processing system that the invoice has already been
reviewed and approved. It is the attack that exists because tools like this
one exist, and it is invisible to someone reading the invoice normally.

## The supplier master

`supplier-master.csv` holds the fourteen suppliers already known, with the
bank details each has used before. It is what `changed_bank_details` and
`lookalike_supplier_name` compare against.

Two suppliers are deliberately **absent** from it: Pentangle Strategic
Consulting, because `new_payee_large_amount` is about a payee nobody has seen
before, and "Acrne Ltd", because a lookalike is only a lookalike if it is not
itself a known supplier.

## The approval matrix

`approval-matrix.csv` holds one row: a Finance Manager limit of GBP 25,000.
Supply it as the run's approval matrix and the Pemberton split becomes
detectable out of the box — the three invoices total GBP 28,440, over the
limit, with none of them alone reaching it.

**A deliberate choice, not an oversight.** A realistic approval matrix
usually carries more than one tier: a lower one for routine spend and a
higher one, like this, for larger jobs. Invoice `01-acme-ltd.pdf` (clean,
GBP 4,980 gross) sits inside `just_under_a_limit`'s own 10% margin below a
plausible lower tier of GBP 5,000 — a matrix built with that second tier
would raise `just_under_a_limit` against a clean invoice purely because a
fictional round-number amount happened to land inside a fictional
round-number margin. That flag would be defensible in general (a payment
sitting just under a real limit is worth a second look, whoever sends it),
but it would break this pack's own promise, below, that none of its twelve
clean invoices ever raises anything. So `approval-matrix.csv` ships with a
single tier, chosen specifically to make the split detectable without
creating that collision. A customer's own matrix will very likely carry
more tiers than this one and may well produce that exact `just_under_a_limit`
reading on an invoice that turns out to be perfectly innocent — that is the
check doing its job on real data, not a defect to chase the way a flag
against one of *this pack's* twelve would be.

## What a good run looks like

- Six issues raised across eight invoices, each quoting both the value found
  and the value expected.
- One document reported as unreadable.
- Nothing raised against the other twelve, one of which is the unreadable
  scan above — not even with `approval-matrix.csv` supplied (see "The
  approval matrix" above for why its one tier was chosen deliberately).
- Any check the run could not perform named explicitly, with the reason — for
  example, if no approval matrix is supplied, `just_under_a_limit` and
  `split_to_stay_under` must say they were not performed rather than passing
  quietly. **Without a matrix, the Pemberton split will not be detected.**
  Supply `approval-matrix.csv` to make it detectable: expect
  `split_to_stay_under` to fire once, on the Pemberton trio, and
  `just_under_a_limit` to stay silent across all twenty invoices in this
  pack.
