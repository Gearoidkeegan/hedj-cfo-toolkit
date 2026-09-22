The DATA block holds every fraud flag {{company_name}}'s scripted checks
raised for this batch of invoices, and a short summary of the batch itself.
You are not looking at the invoices, the supplier master, or anything else
that produced these flags — only what the scripts already found and
compared.

For each flag, decide whether a finance manager should act on it, or whether
the comparison itself points to an innocent explanation. Judge only the
`check`, `where`, `summary`, `compared` values and `source` already given for
that flag, and the batch summary alongside it.

Return one judgement per flag, identified by its own `check` and `where`
exactly as given in the DATA block — never a check id or a location that does
not appear there.

For each judgement, give:

- `check` — the flag's own check id, copied exactly.
- `where` — the flag's own location, copied exactly.
- `matters` — `true` if a finance manager should act on this flag, `false` if
  the comparison itself reads as an innocent explanation (a supplier's own
  numbering habit, a round contract renewal figure, a genuine first order).
  A flag being `low` or `medium` severity is not itself a reason to say
  `false`: judge the comparison, not the severity the scripts already set.
- `why` — one or two sentences a finance manager can read and act on, naming
  what about the comparison supports your view.
- `group` — optional. Give two or more flags the same short label only when
  they are genuinely one story a reader should see together — for example,
  several invoices that are really one split payment, or a changed bank
  detail and a lookalike sending domain on the same invoice that are really
  one attempted takeover. Leave it out for a flag that stands on its own.

You may not invent a flag that is not in the DATA block. You may not change a
severity: severities are the scripts' own and are not yours to raise or
lower. You may not contradict a `compared` value the scripts already found —
your job is to judge what it means, not to dispute what it is.
