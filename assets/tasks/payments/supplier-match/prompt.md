The DATA block holds one JSON object with two parts: `invoice_supplier` --
the name, and any other details given, of the supplier named on the
invoice -- and `candidates`, a shortlist of supplier master records that
`cfo.payments.suppliers.match` could not tell apart from it with certainty,
each carrying its own `supplier_id`, `name` and any known `aliases`.

Decide whether `invoice_supplier` is the same real company as one of the
candidates, using everything in the block: the name, and country, VAT
number or bank details when they are given. Two names that merely sound
alike, share a word, or belong to the same corporate family are not
evidence they are the same company -- "Acme Holdings Ltd" is not "Acme
Ltd", and a shared trading name is not a shared legal entity. When you are
not genuinely confident which candidate is the same company as
`invoice_supplier` -- including when none of them plausibly are -- answer
with `supplier_id` set to `null` and say why in `reason`.

Choose `supplier_id` only from a `supplier_id` value actually present in
`candidates`. Never invent one, never return an id that looks plausible but
was not supplied, and never return more than one.

`reason` is one short sentence: which candidate matched and what the
decision rests on, or why none of them did.
