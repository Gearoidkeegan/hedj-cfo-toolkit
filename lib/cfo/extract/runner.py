"""C4 extraction runner: decides what to ask a model to find in a document
C1 has already extracted, and how to split the asking into groups that fit a
task's token cap.

cfo.extract (C1, this package's own __init__.extract) has already turned a
file into blocks with page/paragraph locations -- one JSON per document at
<run>/extract/docs/<doc_id>.json, shaped {"doc_id", "files", "type",
"blocks"}, each block {"id", "kind", "text" (or "rows" for a table), "loc"}.
This module never re-parses a file or reads bytes off disk to get text out of
it -- it only groups what C1 already produced. cfo.tasks (prepare/accept,
and the quote check in cfo.tasks.quotes) does the actual asking, caching and
quote verification; this module hands it groups, nothing more.

Two rules make this worth having over sending a whole document to a model
task, once per thing the caller wants to know:

1. Find first, then read (`field_groups`). A group carries only the passages
   the fields in it actually need -- matched by a caller-supplied hint
   against each block's own text -- never the whole document.
   cfo.policy.coverage's batching splits one long document into sequential
   slices and asks every clause about every slice, because a policy clause
   can be satisfied by wording anywhere in the document; a wanted field is
   usually answered in one paragraph or table cell, not a whole page, so
   this looks for that paragraph first rather than paying to re-read
   everything around it. The packing itself follows coverage's own pattern
   (`_pack` there, `_pack_fields` here): add units to a group greedily while
   they fit a token cap, close it and start another once they don't. A unit
   here is one whole block, already a paragraph or a table from C1, so a
   group boundary can never fall inside one -- nothing is ever cut
   mid-sentence.
2. A value with no quote is not a value (`collect`). cfo.tasks.quotes has
   already proven, in `task accept`, that every quote here is really in the
   group's own passages -- this never re-checks that; duplicating it would
   be the mistake cfo.policy.coverage's own docstring warns against with the
   same check. What this does still check is that a quote is *there at
   all*: a task's schema can let an empty one through, and a value with no
   quote is treated exactly as if no group had answered that field --
   reported as a gap, not silently kept.

Caching is by the file's content, not its path or name (`cache_key`): the
same invoice read from two folders, or under two names, is one file. A
caller keys its own extraction cache on this, the same way cfo.extract
itself keys a document's id on its sha256 (see cfo.extract.extract).
"""
from cfo.extract.sink import loc_label
from cfo.io import estimate_tokens, sha256_file

# Matches cfo.policy.coverage.MAX_BATCH_TOKENS -- there is nothing special
# about this component's own documents that would justify a different cap.
DEFAULT_MAX_TOKENS = 6000


def cache_key(path):
    """SHA-256 of `path`'s bytes (cfo.io.sha256_file). Two files with
    identical content share a key regardless of name or folder, so a
    caller's own cache of what has already been extracted or asked about --
    keyed on this, never on `path` -- treats them as the same file: it is
    never extracted, or asked about, twice."""
    return sha256_file(path)


def _field_name(field):
    return field["name"] if isinstance(field, dict) else str(field)


def _field_hints(field):
    """The phrases to look for in a block's text: a caller-supplied `hints`
    list, or -- when none is given -- the field's own name with underscores
    turned to spaces, so a plain field name still finds a passage that
    states it in words ("invoice_number" finds a block containing "Invoice
    Number")."""
    if isinstance(field, dict):
        hints = field.get("hints")
        if hints:
            return [str(h) for h in hints]
    return [_field_name(field).replace("_", " ")]


def _norm(text):
    return " ".join(str(text or "").split()).casefold()


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def _block_text(block):
    """The block's own text, for matching a field's hints against -- a
    table's cells joined into one line, since a hint such as "total due" is
    as likely to sit in a table row as in a paragraph."""
    if block.get("kind") == "table":
        return " ".join(_cell(c) for row in (block.get("rows") or []) for c in row)
    return str(block.get("text") or "")


def _render_passage(block):
    """This one block, marked with its location exactly as
    cfo.extract.sink.render_md marks it inside the whole document -- so a
    passage reads identically whether a model is handed the whole rendered
    document or just this excerpt, and a quote taken from it strips the same
    way in cfo.tasks.quotes.strip_markers (which only strips a leading
    `[location]` off the start of a line)."""
    label = loc_label(block.get("loc") or {})
    if block.get("kind") == "table":
        rows = block.get("rows") or []
        lines = [f"[{label}] (table)"]
        for i, row in enumerate(rows):
            lines.append("| " + " | ".join(_cell(c) for c in row) + " |")
            if i == 0:
                lines.append("|" + "---|" * max(len(row), 1))
        return "\n".join(lines)
    if block.get("kind") == "heading":
        hashes = "#" * min(block.get("level", 1) + 1, 6)
        return f"[{label}] {hashes} {block.get('text', '')}"
    prefix = f"[{label}] "
    return prefix + str(block.get("text") or "").replace("\n", "\n" + prefix)


def _pack_fields(matches, passages, doc_id, max_tokens):
    """`matches` (`[(field, [block index, ...]), ...]`, one entry per field
    that matched at least one block in this document) packed into groups
    that fit `max_tokens`, the same greedy way cfo.policy.coverage._pack
    packs sections: add a field to the current group while the passages it
    still needs (beyond what the group already carries) fit; once they
    don't, close the group and start another with that field alone. A field
    whose own needed passages are already over the cap is passed through
    alone rather than broken up further -- a passage is always one whole
    block, from C1, never split mid-block, mirroring _pack's own rule for a
    single oversized unit."""
    groups, fields, indexes = [], [], set()
    for field, matched in matches:
        added = set(matched) - indexes
        added_tokens = estimate_tokens("\n\n".join(passages[i] for i in sorted(added)))
        current_tokens = estimate_tokens("\n\n".join(passages[i] for i in sorted(indexes)))
        if fields and current_tokens + added_tokens > max_tokens:
            groups.append({"fields": fields, "passages": [passages[i] for i in sorted(indexes)],
                          "source": doc_id})
            fields, indexes = [], set()
        fields.append(field)
        indexes |= set(matched)
    if fields:
        groups.append({"fields": fields, "passages": [passages[i] for i in sorted(indexes)],
                      "source": doc_id})
    return groups


def field_groups(fields, extracted, max_tokens=DEFAULT_MAX_TOKENS):
    """Split `fields` (each either a plain field name, or a dict with `name`
    and an optional `hints` list) and the passages they need out of
    `extracted` -- one document in C1's own docs/<doc_id>.json shape, or a
    list of several -- into groups that each fit `max_tokens`.

    Each group: `{"fields": [the field entries -- exactly as given -- that
    this group's passages might answer], "passages": [rendered block
    excerpts, in document order], "source": <doc_id>}`.

    A field matches a block when one of its hints appears in that block's
    text, case-insensitively and with whitespace folded. A field that
    matches no block in a document gets no group from it there -- there
    being nothing to give a model about it is not this function's problem to
    solve; `collect` is what reports a field no group ever answered. A field
    matching blocks in more than one document (`extracted` given as a list)
    gets one group from each document it matches in.
    """
    docs = extracted if isinstance(extracted, list) else [extracted]
    groups = []
    for doc in docs:
        blocks = [b for b in (doc.get("blocks") or []) if _block_text(b).strip()]
        passages = [_render_passage(b) for b in blocks]
        haystacks = [_norm(_block_text(b)) for b in blocks]
        matches = []
        for field in fields:
            hints = [_norm(h) for h in _field_hints(field)]
            matched = [i for i, hay in enumerate(haystacks) if any(h in hay for h in hints)]
            if matched:
                matches.append((field, matched))
        groups.extend(_pack_fields(matches, passages, doc.get("doc_id"), max_tokens))
    return groups


def collect(results, fields):
    """Merge accepted group outputs into `{field_name: {"value", "quote",
    "location", "source"}}`.

    `results` is one entry per group `task accept` has already accepted:
    `{"source": <doc id -- the same one that group's own entry from
    field_groups carried>, "output": {"fields": [{"name", "value", "quote",
    "location"}, ...]}}` -- `output` is the model's own accepted JSON for
    that group; `source` is not the model's to state, so it travels
    alongside rather than being asked for.

    cfo.tasks.quotes has already proven, in `task accept`, that every quote
    reaching here is really present in that group's passages -- this never
    re-checks that (see this module's docstring). What it does check: a
    value with no quote -- a schema can still let one through blank -- is
    not accepted, exactly as if no group had answered that field. The first
    group to answer a field wins; a later, different value for the same
    field is kept out and named in `_warnings` rather than silently
    overwriting the first.

    Every name in `fields` (the same shape `field_groups` takes -- a plain
    name or a dict with `name`) that ends up with no accepted answer at all
    is named in `_warnings`, as `(field_name, "no group answered this
    field")` -- a field no group answered is a gap to act on, never a
    silence that looks like an answer."""
    wanted = [_field_name(f) for f in fields]
    collected, warnings = {}, []
    for result in results:
        source = result.get("source")
        for item in ((result.get("output") or {}).get("fields") or []):
            name = item.get("name")
            if name not in wanted:
                warnings.append((name or "fields",
                                 "a value was reported for a field this run is not looking for; "
                                 "it was ignored"))
                continue
            value = str(item.get("value") or "").strip()
            quote = str(item.get("quote") or "").strip()
            if not value or not quote:
                warnings.append((name, "a value was reported with no quote; it was ignored"))
                continue
            if name in collected:
                if collected[name]["value"] != value:
                    warnings.append((name, "more than one value was found for this field "
                                            f"('{collected[name]['value']}' and '{value}'); "
                                            "the first found is kept"))
                continue
            collected[name] = {"value": value, "quote": quote,
                              "location": str(item.get("location") or ""), "source": source}
    for name in wanted:
        if name not in collected:
            warnings.append((name, "no group answered this field"))
    collected["_warnings"] = warnings
    return collected
