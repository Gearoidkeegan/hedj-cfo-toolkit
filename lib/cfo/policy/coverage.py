"""Coverage: does the company's existing policy already say this, for each
clause the selection calls for -- and what does it state for the board's
decisions?

A model task judges each batch of the policy text against the full list of
target clause ids; this module builds those batches (splitting only at
headings, or -- when a single section is still too long -- at line
boundaries, never inside a line) and merges the model's per-batch verdicts
into the single coverage dict cfo.policy.findings.build consumes.

Besides a verdict per clause, each batch reports the board-parameter values
the policy states in words (`parameters`) and, for the permitted-instruments
clauses, the instruments it permits (`instruments`), each with a verbatim
quote that `task accept` checks against the batch. The merge carries them
into each clause's entry as `stated` and `instruments`, which
cfo.policy.obligations compares with the facility agreements and
cfo.policy.findings.parameter_gaps reads to know what the policy already
sets.
"""
import re

from cfo.console import ToolkitError
from cfo.io import estimate_tokens
from cfo.policy import library
from cfo.policy.obligations import (INSTRUMENT_KEYWORDS, PERMITTED_INSTRUMENTS_CLAUSE_ID,
                                    allows as _allows, mentions as _mentions)

MAX_BATCH_TOKENS = 6000
# How many clauses a "never answered" warning names before it says how many
# more there are: enough to act on, short enough to read.
MAX_NAMED_UNANSWERED = 8
STATUSES = ("missing", "partial", "covered")
_RANK = {status: index for index, status in enumerate(STATUSES)}
# The clauses whose policy wording the model also reads for the instruments it
# permits: the ones the facility comparison checks a facility's list against.
INSTRUMENT_CLAUSE_IDS = tuple(PERMITTED_INSTRUMENTS_CLAUSE_ID.values())
# A rendered document line always starts with a `[location]` marker (see
# cfo.extract.sink.render_md); a heading line additionally has 1-6 `#`s
# straight after it. This matches that shape whether the heading came from a
# real Word style or from `#` syntax in a .txt/.md source -- handle_text
# never strips markdown, so both render identically.
_HEADING_LINE_RE = re.compile(r"^\[[^\]\n]{1,80}\]\s*#{1,6}\s+\S.*$")


def render_clause_list(clauses):
    """The text for the coverage task's `clause_list` input: one `- id: title`
    line per clause, then, indented beneath it, one `- parameter id: label`
    line per board parameter whose value the model should report if the
    policy states one, and an `- instruments:` line on the clauses whose
    permitted instruments it should list."""
    lines = []
    for clause in clauses:
        lines.append(f"- {clause.id}: {clause.title}")
        for parameter in clause.board_parameters():
            lines.append(f"  - parameter {parameter['id']}: {parameter['label']}")
        if clause.id in INSTRUMENT_CLAUSE_IDS:
            lines.append("  - instruments: each hedging instrument the policy permits")
    return "\n".join(lines)


def _sections(text):
    """[[line, ...]], split at each heading line. Text before the first
    heading, or the whole text when there is no heading at all, is one
    section on its own."""
    sections, current = [], []
    for line in text.splitlines():
        if _HEADING_LINE_RE.match(line) and current:
            sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)
    return sections or [[]]


def _pack(units, max_tokens):
    """Greedily group `units` (each a list of lines) into batches that stay
    within max_tokens, splitting only between units. A single unit that is
    already over max_tokens on its own is passed through alone rather than
    broken up further."""
    batches, current = [], []
    for unit in units:
        candidate = current + unit
        if current and estimate_tokens("\n".join(candidate)) > max_tokens:
            batches.append(current)
            current = list(unit)
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def split_text(text, max_tokens=MAX_BATCH_TOKENS):
    """`text` as a list of consecutive parts that each fit max_tokens. Splits
    at heading lines; a section still over the cap on its own is split
    further at line boundaries, never mid-line. The parts, joined with a
    newline, are the whole text: nothing is ever truncated. Shared by the
    coverage batches and the facility agreements' parts."""
    pieces = []
    for lines in _sections(text):
        if estimate_tokens("\n".join(lines)) <= max_tokens:
            pieces.append(lines)
        else:
            pieces.extend(_pack([[line] for line in lines], max_tokens))
    return ["\n".join(lines) for lines in _pack(pieces, max_tokens)]


def plan_batches(policy_text, clauses, max_tokens=MAX_BATCH_TOKENS):
    """The policy text and the clauses being looked for, split into batches
    that each fit max_tokens (see `split_text`)."""
    clause_list = render_clause_list(clauses)
    clause_ids = [clause.id for clause in clauses]
    return [{"policy_text": part, "clause_list": clause_list, "clause_ids": clause_ids}
            for part in split_text(policy_text, max_tokens)]


def _norm(text):
    return " ".join(str(text or "").split()).casefold()


# A stated value has to be supported by the quote it was given, not merely
# accompanied by one. `task accept` proves the quote is in the policy (see
# cfo.tasks.quotes.check_quotes) and nothing more, so a real sentence from the
# policy could be attached to a figure the policy never states -- and since a
# stated value decides a Critical contradiction against a mere constraint
# (cfo.policy.obligations), an unsupported one could manufacture a Critical or
# bury a real one. Everything below answers one question: does this quote say
# this?
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d\d\d\b)")
_DIGITS_RE = re.compile(r"\d+(?:\.\d+)?")
# "50-80%", "50 to 80 per cent", "between 50 and 80%": a figure inside a range
# the quote states is one the quote supports.
_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|per\s*cent|percent)?\s*"
                       r"(?:-|" + chr(0x2013) + r"|to|and)\s*(\d+(?:\.\d+)?)", re.I)
_WORD_RE = re.compile(r"[a-z]{3,}")
_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
          "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
          "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
# Words too common to prove anything when a value is not a number.
_COMMON = {"the", "and", "for", "per", "cent", "percent", "all", "any", "with", "from", "this",
           "that", "its", "their", "are", "not", "less", "than", "least", "most", "company",
           "policy", "board", "such", "each", "own", "has", "have", "been", "will", "shall"}


def _plain(text):
    return _THOUSANDS_RE.sub("", str(text or "")).replace(chr(0x2011), "-").lower()


def _numbers(text):
    """Every number the text states, in digits ("60%", "5,000") or in words
    ("sixty per cent", "twelve months", "twenty-five")."""
    text = _plain(text)
    found = {float(match) for match in _DIGITS_RE.findall(text)}
    tokens = re.split(r"[^a-z]+", text.replace("-", " "))
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _TENS:
            value = _TENS[token]
            if index + 1 < len(tokens) and 1 <= _UNITS.get(tokens[index + 1], 0) <= 9:
                value += _UNITS[tokens[index + 1]]
                index += 1
            found.add(float(value))
        elif token in _UNITS:
            found.add(float(_UNITS[token]))
        index += 1
    return found


def _ranges(text):
    return [(float(low), float(high)) for low, high in _RANGE_RE.findall(_plain(text))]


def _words(text, common=frozenset()):
    return {word for word in _WORD_RE.findall(_plain(text)) if word not in common}


# A cadence a policy states in one wording and the model reports in another.
# "annually", quoted as "The board reviews this policy each year.", shares no
# four-letter stem with its own quote, so the stem rule below dropped the
# stated value and the gap report then said the review frequency "is not set"
# about a policy that sets it. One entry per cadence, with the wordings this
# library and real policies use; the half-yearly forms are read first so that
# "semi-annually" is never also read as "annually". A hyphen in a wording
# matches a space as well, and a cadence stated as a number of months
# ("every twelve months") is read by the numeric path above, not here.
_CADENCES = (
    ("half-yearly", ("semi-annually", "semi-annual", "semiannually", "semiannual", "half-yearly",
                     "twice a year", "twice yearly", "every six months", "each six months",
                     "every half year", "each half year")),
    ("annual", ("annually", "annual", "yearly", "each year", "every year", "per year",
                "per annum", "every twelve months", "each twelve months", "every 12 months")),
    ("quarterly", ("quarterly", "each quarter", "every quarter", "per quarter")),
    ("monthly", ("monthly", "each month", "every month", "per month")),
    ("weekly", ("weekly", "each week", "every week", "per week")),
    ("daily", ("daily", "each day", "every day", "per day")),
)
_CADENCE_RES = tuple((name, tuple(re.compile(r"\b" + re.escape(wording).replace("\\-", "[- ]")
                                             + r"\b", re.I)
                                  for wording in wordings))
                     for name, wordings in _CADENCES)


def _cadences(text):
    """Every cadence `text` names (see _CADENCES). Each wording found is
    blanked out before the next cadence is looked for, so the "annual" forms
    can never match inside a half-yearly one."""
    text = _plain(text)
    found = set()
    for name, patterns in _CADENCE_RES:
        for pattern in patterns:
            if pattern.search(text):
                found.add(name)
                text = pattern.sub(" ", text)
    return found


def _shares_a_word(value, quote):
    """True when a word of `value` long enough to mean something also appears in
    the quote (on its first four letters, so "quarterly" matches "quarter"), or
    when the two name the same cadence in different words ("annually" against
    "each year"), which no stem in common could tell."""
    named = _cadences(value)
    if named and named & _cadences(quote):
        return True
    words = _words(value, _COMMON)
    if not words:
        return _norm(value) in _norm(quote)
    stems = {word[:4] for word in _words(quote)}
    return any(word[:4] in stems for word in words)


def quote_states_value(value, quote):
    """True when `quote` states `value`: every figure in the value appears in
    the quote -- in digits or in words, or inside a range the quote states --
    and, for a value that is no figure at all, at least one of its words does."""
    wanted = _numbers(value)
    if wanted:
        given, ranges = _numbers(quote), _ranges(quote)
        return all(number in given or any(low <= number <= high for low, high in ranges)
                   for number in wanted)
    return _shares_a_word(value, quote)


def quote_permits_instrument(instrument, quote):
    """True when `quote` permits `instrument`: it names the instrument, in a
    statement that does not rule it out -- so "swaptions are not used" never
    counts as permitting swaptions (cfo.policy.obligations.allows)."""
    keywords = [keyword for keyword in INSTRUMENT_KEYWORDS if _mentions(instrument, keyword)]
    if keywords:
        return all(_allows(quote, keyword) for keyword in keywords)
    return _shares_a_word(instrument, quote)


def _text(item, key):
    return str(item.get(key) or "").strip()


def _merge_stated(coverage, owner, labels, items, warnings):
    for item in items:
        if not isinstance(item, dict):
            continue
        parameter_id = _text(item, "parameter_id")
        clause_id = owner.get(parameter_id)
        if clause_id is None:
            warnings.append((parameter_id or "parameters",
                             "a value was reported for a parameter this run is not looking "
                             "for; it was ignored"))
            continue
        value, quote = _text(item, "value"), _text(item, "quote")
        if not value or not quote:
            warnings.append((parameter_id, "a value was reported with no quote from the policy; "
                                           "it was ignored"))
            continue
        if not quote_states_value(value, quote):
            label = library.decapitalize(labels.get(parameter_id, parameter_id))
            warnings.append((clause_id, f"the policy was said to state '{value}' for "
                                        f"{label}, but "
                                        f"the wording quoted for it does not say so: the value "
                                        f"was ignored, and the clause's own verdict stands"))
            continue
        stated = coverage[clause_id]["stated"]
        if any(entry["parameter_id"] == parameter_id and _norm(entry["value"]) == _norm(value)
               for entry in stated):
            continue
        stated.append({"parameter_id": parameter_id, "value": value, "quote": quote,
                       "source": _text(item, "source")})
    for clause_id in coverage:
        by_parameter = {}
        for entry in coverage[clause_id]["stated"]:
            by_parameter.setdefault(entry["parameter_id"], []).append(entry)
        for parameter_id, entries in by_parameter.items():
            if len(entries) > 1:
                values = "; ".join(f"'{e['value']}' ({e['source'] or 'no location'})"
                                   for e in entries)
                label = library.decapitalize(labels.get(parameter_id, parameter_id))
                warnings.append((parameter_id,
                                 f"the policy states more than one value for "
                                 f"{label}: {values}; "
                                 "all are kept, and the facility is compared with each"))


def _merge_instruments(coverage, batch, warnings):
    for item in batch.get("instruments", []) or []:
        if not isinstance(item, dict):
            continue
        clause_id = _text(item, "clause_id")
        if clause_id not in coverage or clause_id not in INSTRUMENT_CLAUSE_IDS:
            warnings.append((clause_id or "instruments",
                             "an instrument was reported against a clause that does not list "
                             "permitted instruments in this run; it was ignored"))
            continue
        instrument, quote = _text(item, "instrument"), _text(item, "quote")
        if not instrument or not quote:
            warnings.append((clause_id, "an instrument was reported with no quote from the "
                                        "policy; it was ignored"))
            continue
        if not quote_permits_instrument(instrument, quote):
            warnings.append((clause_id, f"the policy was said to permit '{instrument}', but the "
                                        f"wording quoted for it does not permit it: the "
                                        f"instrument was ignored, and the clause's own verdict "
                                        f"stands"))
            continue
        listed = coverage[clause_id]["instruments"]
        if any(_norm(entry["instrument"]) == _norm(instrument) for entry in listed):
            continue
        listed.append({"instrument": instrument, "quote": quote, "source": _text(item, "source")})


def merge(results, clauses):
    """The coverage dict cfo.policy.findings.build consumes, from the
    coverage task's per-batch output.

    Per clause: `status`, `quote`, `source`, `detail` -- covered beats
    partial beats missing; a covered or partial verdict with no quote is
    downgraded to missing. A clause no batch answered at all is not a verdict
    but a model failure -- the prompt requires one entry per clause per batch
    -- so it is warned about by name, and refused with a ToolkitError when
    more than half the list went unanswered; a clause answered in any batch
    counts as answered, since a policy read in several batches is one answer
    between them.

    Two batches tied at the same status keep the first one that actually
    supplies a `detail` (M3): the initial state is already "missing" with no detail,
    so the first batch to explain why is kept, rather than every tied
    batch's explanation being discarded regardless of what it said -- plus
    `stated` (`[{parameter_id, value, quote, source}]`, the board-parameter
    values the policy states) and `instruments` (`[{instrument, quote,
    source}]`, on the permitted-instruments clauses). A value stated in two
    batches is kept once; two different values for one parameter are both
    kept, with a warning. A stated value or instrument whose own quote does not
    say so is dropped with a warning, leaving the clause's verdict and quote
    untouched (`quote_states_value`, `quote_permits_instrument`): `task accept`
    proves a quote is in the policy, not that it states the value hung on it.
    An unknown `clause_id` in a batch's results is ignored with a warning (I6); when most of one batch's results name an
    unknown id -- the model echoing the clause list instead of an id, say --
    that batch is refused with a ToolkitError rather than merged as though it
    had found nothing. Anything else ignored or doubtful is noted in
    `_warnings` as (where, message) pairs."""
    coverage = {clause.id: {"status": "missing", "quote": "", "source": "", "detail": "",
                            "stated": [], "instruments": []}
                for clause in clauses}
    owner, labels = {}, {}
    for clause in clauses:
        for parameter in clause.board_parameters():
            owner[parameter["id"]] = clause.id
            labels[parameter["id"]] = parameter["label"]
    warnings, answered = [], set()
    for batch in results:
        items = batch.get("results", []) or []
        unknown = [item for item in items if item.get("clause_id") not in coverage]
        if items and len(unknown) * 2 > len(items):
            examples = ", ".join(repr(item.get("clause_id")) for item in unknown[:5])
            raise ToolkitError(("results",
                                f"{len(unknown)} of {len(items)} clause ids in one batch's results "
                                f"are not in the clause list this run gave it (for example: "
                                f"{examples}): the model may have misunderstood the task, so this "
                                "batch was not merged"))
        for item in unknown:
            warnings.append((str(item.get("clause_id") or "results"),
                             f"'{item.get('clause_id')}' is not a clause id this run is looking "
                             "for; it was ignored"))
        for item in items:
            clause_id = item.get("clause_id")
            if clause_id not in coverage:
                continue
            status = item.get("status")
            quote = str(item.get("quote") or "").strip()
            if status in ("covered", "partial") and not quote:
                warnings.append((clause_id,
                                 f"reported '{status}' with no quote, treated as missing"))
                status = "missing"
            if status not in _RANK:
                continue
            answered.add(clause_id)
            entry = coverage[clause_id]
            new_rank, current_rank = _RANK[status], _RANK[entry["status"]]
            if new_rank < current_rank:
                continue
            detail = str(item.get("detail") or "")
            # M3: a batch that only ties the winning status can still be the
            # one that "decided" it, in the sense that matters to a reader --
            # the one that actually explained why -- when nothing has
            # explained it yet. Without this, the first batch to reach a
            # status (even one that left `detail` blank) locked it in forever,
            # and a later batch's own explanation for the very same verdict
            # was discarded. Once a detail is recorded, it is not replaced by
            # another batch's account of the same status.
            if new_rank == current_rank and (entry["detail"] or not detail):
                continue
            entry.update({"status": status, "quote": quote,
                         "source": str(item.get("source") or ""), "detail": detail})
    # Which clauses no batch answered at all -- the mirror of the unknown-id
    # check above. Every clause is seeded "missing", so one batch that answers
    # three of fifty clauses used to merge as forty-seven silent "missing"
    # verdicts and a gap report telling a finance director that a real policy
    # covers nothing. The prompt requires one entry per clause per batch, so
    # an absent clause is a model failure rather than a verdict on the policy.
    # The clause list goes to every batch and a clause is answered where its
    # own section falls, so a clause answered in any batch is answered: what
    # is counted is the whole clause list against the union of the answers,
    # never one batch's own share of it.
    unanswered = [clause_id for clause_id in coverage if clause_id not in answered]
    if unanswered:
        named = ", ".join(unanswered[:MAX_NAMED_UNANSWERED])
        if len(unanswered) > MAX_NAMED_UNANSWERED:
            named += f", and {len(unanswered) - MAX_NAMED_UNANSWERED} more"
        if len(unanswered) * 2 > len(coverage):
            raise ToolkitError(("results",
                                f"{len(unanswered)} of the {len(coverage)} clauses this run is "
                                f"looking for were never answered by any batch ({named}): the "
                                "model answered only part of the clause list, so nothing was "
                                "merged -- read the policy again"))
        warnings.append(("results",
                         f"{len(unanswered)} of the {len(coverage)} clauses this run is looking "
                         f"for were never answered by any batch ({named}); each stands as "
                         "'missing', which may understate what the policy covers, so check those "
                         "clauses against the policy by hand"))
    for batch in results:
        _merge_instruments(coverage, batch, warnings)
    # Stated values last, over every batch at once, so a conflict between two
    # batches is warned about once rather than once per batch.
    _merge_stated(coverage, owner, labels,
                  [item for batch in results for item in batch.get("parameters", []) or []], warnings)
    coverage["_warnings"] = warnings
    return coverage
