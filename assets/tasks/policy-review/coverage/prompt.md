The first DATA block is one batch of {{company_name}}'s existing treasury or
hedging policy, as extracted text. The second DATA block lists the clauses
this run is looking for, one per line as `- id: title`. Beneath a clause, an
indented `- parameter id: label` line names a board decision that clause
carries, and an indented `- instruments:` line marks a clause whose permitted
instruments you should list.

For each clause in the list, judge only what the policy text in this batch
actually says -- never what a section heading implies, and never what such a
policy would typically contain.

1. `covered`: the text sets out this clause's substance in the policy's own
   words, even if phrased differently from the clause library.
2. `partial`: the text touches on the topic but leaves out a material part of
   it (for example, it states a hedging objective but sets no ratio, or names
   a reporting duty but not to whom or how often).
3. `missing`: the text says nothing on this clause, including when a section
   heading alone suggests it should. A heading is not evidence; only the
   wording under it is. This batch may simply not contain that clause's
   section -- do not guess from what is absent.

For `covered` and `partial`, copy the exact wording the judgement rests on
into `quote`, verbatim, from this batch only, and cite its location (the
`[...]` marker at the start of the line it came from) in `source`. Never
invent or paraphrase a quote. Add one sentence of `detail` for a `partial` or
`missing` verdict, saying what is missing.

Write that `detail` as a plain statement about the policy, never about this
batch of text: the reader of the final report sees only the merged verdict
across the whole document, not which slice of it you were shown, so a phrase
such as "not in this batch", "not covered in this excerpt" or "not addressed
here" means nothing to them and must never appear. Say what the policy itself
does not do -- for example "the policy sets a hedging objective but no
minimum ratio" -- not where you looked for it.

Return exactly one entry in `results` per clause id listed, even when the
verdict is `missing`.

Then report what the policy itself states for the board's decisions:

- `parameters`: one entry for each `parameter` line whose value this batch
  states in words -- the figure, ratio, frequency, rating, horizon or list the
  policy sets, for example `{"parameter_id": "min-fixed-proportion", "value":
  "75%", "quote": "...", "source": "[...]"}`. `value` is that value, briefly,
  as the policy states it; `quote` is the exact wording that states it,
  verbatim from this batch; `source` is its `[...]` marker. Report only a
  value the text states outright. Never infer one from other figures, never
  calculate one, never fill in what is typical, and leave a parameter out
  altogether when the text does not state it -- a vague word such as
  "periodically" or "a proportion" is not a value.
- `instruments`: for each clause with an `instruments:` line, one entry per
  hedging instrument this batch permits the company to use, with that
  `clause_id`, the `instrument` as the policy names it, and the verbatim
  `quote` and `source` that permit it. Leave out an instrument the text names
  only to rule it out.

Both lists may be empty, and often will be; an empty list is a complete
answer, not a failure.
