# CFO Toolkit

Free treasury tools for CFOs and treasury managers at mid-sized companies, from [Hedj](https://hedj.eu).
The toolkit runs inside your own Claude account (Claude Code or Cowork). Your documents stay on your computer and in your Claude account: Hedj receives none of them.

> **Status:** the first tool, hedging and treasury policy review, is available. See below.

## Tools

| Tool | What it does | Status |
|---|---|---|
| Policy review | Reviews or drafts a hedging and treasury policy, and explains which clauses were left out and why | Available |
| Payments check | Turns invoices into a payment file and checks every run for fraud signals | Available |
| Debt advisory | Assesses debt capacity and funding options, with a lender summary and deck | Planned |
| Terms register | Pulls key terms, covenants and deadlines from loan agreements and ISDAs into a workbook | Planned |
| Cash forecast | Builds or challenges a 13-week cash forecast, and tests covenant headroom | Planned |

## Install

**Cowork (Claude desktop app):** open Cowork, then add the marketplace and install the plugin:
```
/plugin marketplace add <repository>
/plugin install cfo-toolkit@cfo-toolkit
```

**Claude Code:** the same two commands. The plugin is usually active straight away; restart Claude Code if the command does not appear.

Then run `/cfo-toolkit:check-setup`. It checks Python and the libraries, reads the bundled sample company, runs one tiny AI task (about one US cent) and builds a one-page Word file.

Requirements:
- Python 3.11 or newer, with `openpyxl`, `python-docx` and `python-pptx` (Cowork already has them). The toolkit runs `python3`, or `python` on Windows, whichever your computer has.
- PDF support also needs `pymupdf`

The setup check prints the exact install command for anything missing.

## Policy review

`/cfo-toolkit:policy-review` reviews a treasury or hedging policy, or drafts one from scratch,
using a library of 64 clauses covering everything from purpose and governance to FX, interest
rate and commodity risk, counterparty risk, liquidity, funding and covenants, hedge accounting
and regulatory matters.

Give it whatever you have: your existing policy, facility agreements, a hedge report, board
minutes, management accounts. It reads them, and asks only what they do not already answer,
then produces:

- a **gap report** — what is missing or weak, ranked, with the wording from your own policy
  behind each finding that has any;
- a **redrafted policy** — in the toolkit's own style, or matching your existing Word document;
- an **appendix of what it left out**, and why — driven by what the company is actually
  exposed to, not simply its size.

You can also ask for a board decisions paper, a two-page board paper, a hedge ratio ladder in
Hedj's import format, or an authority matrix.

It reads your facility agreements in full, however long, and compares what each one requires
with what your own policy states — for example a loan that requires 60% of drawn debt at fixed
rates against a policy that fixes only 50%, or a policy that permits an instrument the loan
does not. Each such contradiction, and any requirement your policy has no clause on at all, is
reported first, citing both the facility clause and your policy. Where your policy states no
figure, or you have no policy yet, the loan's requirement is not a finding: it is shown beside
the board decision it bears on, so the board sets that figure with the loan in view. If you
give it no facility agreement, or one cannot be read in full, the gap report says plainly that
those covenants were not checked.

The policy is drafted in English. A translation — into German, say — is a later step, done
separately, not part of the run.

At the start you choose: a **full treasury policy** (cash, funding and all hedging) or a
**hedging policy** alone; a length of **lite** (a short policy — about four to five pages for
hedging alone, about six for a full treasury policy), **moderate** (fuller) or **comprehensive**
(every applicable section), with a suggestion based on your company's size and team; a **basic**
review (a lender's eye) or a **detailed** one (a lender, an auditor and a non-executive director,
then a critic); and, if you gave it your own Word document, whether the redraft should match its
style or use the toolkit's own. Beyond that, it only asks questions your documents have not
already answered.

Board decisions — hedge ratios, permitted products, the hedging horizon, limits and review
frequencies — are always left to your board, shown with what is typical for a company of your
size beside each one, and never filled in. Where a finding touches EMIR, MiFID or a
hedge-accounting framework, it is flagged for your own advisers to confirm, never asserted to
apply.

Try it on the sample company in `samples/policy-review/`.

**What it costs:** measured on a real run against the sample company, a detailed review (lender,
auditor and non-executive panel plus a final critic) cost about **$0.39** in model calls; the
critic was over half of that. A basic review, which skips all but the lender, came to about
**$0.09** on the same run's per-task figures. It runs in your own Claude account, so it draws on
your plan's allowance, or on API prices if you use a key.

The toolkit runs in your own Claude account. Your documents are not sent to Hedj.

## Payments and fraud checking

`/cfo-toolkit:payments` checks a batch of supplier invoices before you pay them, then builds
the payment file. Twelve checks run against every invoice: a changed bank detail, a lookalike
supplier name or sending domain, a first-time payee invoicing an unusually large amount, a
payment sitting just under an approval limit (or several that together clear one you never see
individually), a round or oddly-timed invoice, a near-duplicate, and hidden text addressed to
an AI reader rather than a person. A supplier master and an approval matrix sharpen several of
these; without them, the report says by name which checks could not run rather than passing
quietly.

Every exception is worked through one at a time — never in bulk — and recorded with who decided
it and why. A bank-detail change you accept updates the company's own supplier master, so the
same legitimate change is not flagged again on the next run; one you reject never touches it.
Nothing that moves money — the pain.001 file for your own bank, or a flat CSV — can be built
until a named person has signed off the batch. Sign-off is an attestation and an audit trail,
not dual authorisation: the tool cannot authenticate anyone, and says so plainly rather than
implying a control it does not have. A workbook and a fraud report can be built earlier, as a
clearly marked NOT-REVIEWED draft, so the reviewer has something to actually look at before they
sign off.

Try it on the sample pack in `samples/payments/`, whose `README.md` is the answer key to what is
planted where.

**What it costs:** measured on a real run against the 20-invoice sample pack (19 readable, one
scanned image with no text layer): about **$0.10** in model calls for the whole run —
`payments start` through the staged draft/amend/sign-off cycle to `payments final`. That covers
the per-invoice field extraction (Claude Haiku, roughly half a cent per invoice, ~19 invoices)
and the one fraud-judgement task per batch (Claude Sonnet, judging which flags matter and
grouping the ones that are a single story). Re-running `payments register` on the same invoices
in a separate run cost nothing further: the extraction cache is shared across runs by content,
so a second pass over the same documents is free. Figures exclude the optional red-team check.
It runs in your own Claude account, so it draws on your plan's allowance, or on API prices if you
use a key.

## Confidentiality

- Outputs are written to a `cfo-toolkit-runs/` folder where you run the tool. If that folder is inside a git repository, it is added to the repository's local exclude file.
- If the folder is inside OneDrive, Dropbox, Google Drive, iCloud or Box, the toolkit warns you before reading anything.
- Everything in your documents is treated as data, never as instructions. Hidden text and instructions aimed at AI are reported, not followed.

## Information, not advice

The toolkit produces information to support your own decisions. It is not financial, legal, tax or investment advice. Check every figure against your records and take professional advice before acting.

## Licence

Code: MIT (see `LICENSE`). Content under `assets/`: CC BY-NC-ND 4.0 (see `LICENSE-CONTENT`).
