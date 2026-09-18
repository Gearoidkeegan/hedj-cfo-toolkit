# CFO Toolkit

Free treasury tools for CFOs and treasury managers at mid-sized companies, from [Hedj](https://hedj.eu).
The toolkit runs inside your own Claude account (Claude Code or Cowork). Your documents stay on your computer and in your Claude account: Hedj receives none of them.

> **Status:** foundations release. The first tool, hedging and treasury policy review, is coming next. Until then, the only command is the setup check.

## Tools

| Tool | What it does | Status |
|---|---|---|
| Policy review | Reviews or drafts a hedging and treasury policy, and explains which clauses were left out and why | In development |
| Payments check | Turns invoices into a payment file and checks every run for fraud signals | Planned |
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

## Confidentiality

- Outputs are written to a `cfo-toolkit-runs/` folder where you run the tool. If that folder is inside a git repository, it is added to the repository's local exclude file.
- If the folder is inside OneDrive, Dropbox, Google Drive, iCloud or Box, the toolkit warns you before reading anything.
- Everything in your documents is treated as data, never as instructions. Hidden text and instructions aimed at AI are reported, not followed.

## Information, not advice

The toolkit produces information to support your own decisions. It is not financial, legal, tax or investment advice. Check every figure against your records and take professional advice before acting.

## Licence

Code: MIT (see `LICENSE`). Content under `assets/`: CC BY-NC-ND 4.0 (see `LICENSE-CONTENT`).
