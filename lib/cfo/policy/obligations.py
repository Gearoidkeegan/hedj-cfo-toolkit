"""Obligations: what a facility agreement requires, compared with what the
company's own policy states.

`compare` takes the facilities (one merged object per document, as
`policy obligations-merge` writes them), the selection, the coverage dict
(each clause's verdict plus the board-parameter values and instruments the
existing policy states -- see cfo.policy.coverage.merge) and whether an
existing policy was supplied at all. It returns three lists:

- `contradictions`: Critical findings, each citing both documents -- the
  facility's quote and source (the source names the facility) and the
  policy's quote and source. One is raised when the company's policy states
  a minimum below the facility's -- the fixed proportion of drawn debt, the
  hedge ratio for the tenor band the exposure the facility covers falls in
  (every band, when it states no tenor), or the commodity hedge ratio -- or
  permits an instrument a facility that rules others out does not (one that
  merely names ways of complying rules nothing out: `_check_instruments`);
  when the policy's stated maximum hedging horizon is shorter than the tenor
  of the exposure the facility requires cover for;
  and when the facility requires something (a fixed-rate floor, a currency or
  commodity hedge) that the policy has no clause for at all -- either the
  company's own policy (coverage found nothing on it) or the redraft (the
  clause was not selected for this profile). Interest rate, foreign exchange
  and commodity are compared by the same code (`_compare_stated_minimum`), and
  the hedging-horizon comparison shares its own code
  (`_compare_stated_horizon`) across foreign exchange and commodity, so none
  of them can drift from another.
- `constraints`: each facility requirement set beside the board decision it
  bears on, for the decisions table at the front of the redraft and the board
  decisions document ("typical 40%; Term Loan A clause 22.3 requires at least
  60% of drawn debt at fixed rates"). Where the policy states nothing, or
  there is no existing policy, this is all a requirement becomes: never a
  finding. Most carry a `parameter_id` naming the board decision they sit
  beside (`Context.constraint`); a facility's `restrictions` (other than
  `indebtedness` and `security`, which raise nothing -- see
  `_check_restriction`) name who the company may hedge with, forbid
  speculation, or restrict its bank accounts or its investments, and three of
  those four kinds bear on a clause the library gives no board parameter of
  its own, so their constraint carries `parameter_id: ""` and attaches to the
  clause as a whole instead (`Context.clause_constraint`, the sibling of
  `Context.constraint` for exactly this case). Both shapes carry
  `clause_title`, so a renderer never has to look it up. A restriction is a
  prohibition, not something the policy can fall short of, and a policy
  silent on who the company hedges with does not conflict with a facility
  that names one: restrictions only ever become constraints, never a
  contradiction.
- `warnings`: (where, message) pairs for a stated value the comparison could
  not read.

Never compared with the company's actual hedge book, which the toolkit has
no visibility into at this stage.

A hedging requirement states two different periods, and they are never the
same number: `exposure_tenor_months` is how far out the exposure to be
covered falls due ("receipts falling due within the following twelve
months"), and `minimum_period_months` is how long the obligation itself runs
("maintained for not less than twenty-four months"). The tenor picks the
hedge ratio band a currency requirement is compared with (`_fx_band`), and
the tenor alone is compared with the policy's stated maximum hedging horizon
(`_check_period`). Reading either as the other put a facility's own words
into a finding that did not match them, so each is asked for separately by
the obligations task's prompt and read separately here.

How long an obligation runs can never outrun a horizon, so it is never
compared with one: a facility may require cover maintained for three years
over exposure falling due within six months, and a policy hedging twelve
months ahead meets that by rolling its hedges. The obligation's own term
still reaches the board, inside the constraint's sentence (`_period_of_cover`
takes the longer of the two for that wording) -- it simply cannot raise a
Critical by itself.

The tenor of the exposure to be covered is compared with the policy's stated
maximum hedging horizon for foreign exchange and commodity, which each have a
board decision for it (`fx-horizon-max`, `comm-horizon-max`). Interest rate has
none: its own fixed-and-floating mix clause commits the company to a
facility's own term instead of setting an independent ceiling of its own (see
the library's `ir-covenant-requirements` clause), so a period requirement
there stays part of the fixed-floor constraint's own sentence, as before --
it can never become a Critical without a board figure of its own to fall
short of.

A facility agreement is read in parts (MAX_PART_TOKENS each); `merge_parts`
puts each document's parts back together as one facility before `compare`
sees it, and says which parts, if any, were never read.
"""
import re

from cfo.policy import library, percent

FX_SECTION_ID = "04-fx"
IR_FIXED_FLOATING_CLAUSE_ID = "ir-fixed-floating-mix"
MIN_FIXED_PARAMETER_ID = "min-fixed-proportion"
# The clause and board parameter for a commodity hedge ratio, the direct
# counterpart of IR_FIXED_FLOATING_CLAUSE_ID/MIN_FIXED_PARAMETER_ID: one
# clause, one figure, no per-band split the way foreign exchange has.
COMMODITY_CLAUSE_ID = "comm-hedge-ratio-horizon"
MIN_COMMODITY_PARAMETER_ID = "comm-hedge-ratio-min"
PERMITTED_INSTRUMENTS_CLAUSE_ID = {"interest_rate": "ir-permitted-instruments",
                                    "currency": "fx-permitted-instruments",
                                    "commodity": "comm-permitted-instruments"}
# The board parameter, on each permitted-instruments clause, that records which
# of the candidate instruments the board permits.
PERMITTED_PRODUCTS_PARAMETER_ID = {"interest_rate": "ir-permitted-products",
                                    "currency": "fx-permitted-products",
                                    "commodity": "comm-permitted-products"}
# Where a facility's restriction (other than indebtedness or security, which
# raise nothing -- see `_check_restriction`) bears on the library: the clause
# it constrains. `hedging_counterparty`, `speculation` and `bank_accounts`
# each land on a clause with no board parameter of its own, so all three are
# attached to the clause as a whole (`Context.clause_constraint`); only
# `investments` has one, named in RESTRICTION_PARAMETER_ID below.
RESTRICTION_CLAUSE_ID = {"hedging_counterparty": "cp-approved-counterparties",
                         "speculation": "fx-prohibited-instruments",
                         "bank_accounts": "liq-bank-account-changes",
                         "investments": "inv-permitted-instruments"}
RESTRICTION_PARAMETER_ID = {"investments": "inv-permitted-products"}
# A facility agreement is read in parts of at most this many tokens, split at
# headings and then lines (cfo.policy.coverage.split_text), so a long agreement
# stays under the obligations task's 8,000-token input cap without a passage
# ever being cut.
MAX_PART_TOKENS = 6000
# The lists a facility carries, merged part by part.
FACILITY_LISTS = ("hedging_requirements", "covenants", "reporting_duties", "restrictions")
# The clause whose board decisions a facility's requirement to hedge a currency
# bears on, and its bands: (the last month the band covers, the board parameter,
# how the band reads in a sentence). A facility that states a period is compared
# with the band covering it; one that states none is compared with every band.
FX_RATIO_CLAUSE_ID = "fx-hedge-ratio-bands"
FX_RATIO_BANDS = ((6, "ratio-0-6m", "0 to 6 months"),
                  (12, "ratio-7-12m", "7 to 12 months"),
                  (24, "ratio-13-24m", "13 to 24 months"))
# The nearest band: where a facility names a currency but no proportion, its
# requirement is set beside this one decision rather than all three.
FX_RATIO_PARAMETER_ID = FX_RATIO_BANDS[0][1]
# The clause and board parameter that records the maximum horizon the company
# hedges without specific board approval, per kind -- the ceiling a facility's
# own period of cover is compared with (`_check_period`). Interest rate has no
# entry: see the module docstring.
HORIZON_CLAUSE_ID = {"currency": "fx-hedging-horizon", "commodity": COMMODITY_CLAUSE_ID}
HORIZON_PARAMETER_ID = {"currency": "fx-horizon-max", "commodity": "comm-horizon-max"}
# Recognised (plurals matched) in an instrument the policy permits and in the
# facility's own list, so "FX forward contracts" and "forwards" are the same
# instrument. An instrument naming none of these is compared by its words.
INSTRUMENT_KEYWORDS = ("forward", "swap", "cap", "collar", "option", "swaption")
# An instrument may be named with a qualifier that rules part of it out
# ("purchased (never written) options", "swaps, but not swaptions"). The text is
# split into statements -- at sentence punctuation, at a parenthesis, and before
# a ", but", ", not" or ", never" that turns a list around -- and a keyword named
# only in a statement that negates does not count.
_STATEMENT_SPLIT_RE = re.compile(r"[.;:()\n]|(?=,?\s+but\b)|(?=,\s*(?:not|never)\b)", re.I)
_NEGATION_RE = re.compile(r"\b(?:not|never|nor|neither)\b|\bprohibit|\bexclud|\bforbid|^\s*no\b",
                          re.I)
# Whether a facility's own quote makes its list of instruments exclusive, on
# top of the negations above: "by interest rate swap only", "no instrument
# other than a forward". A facility that merely names ways of complying --
# "whether by fixed rate loan, interest rate swap or interest rate cap" --
# forbids nothing, so a policy that also permits a collar contradicts nothing
# there (see `_check_instruments`). Kept apart from _NEGATION_RE, which reads
# a policy's own wording for the instruments it permits and must go on
# treating "swaps only" as permitting swaps.
_EXCLUSIVE_RE = re.compile(r"\bonly\b|\bsolely\b|\bexclusively\b|\bother than\b|\bno other\b", re.I)
_WORD_RE_CACHE = {}
# Reading a stated minimum: "75%", "at least 75 per cent", "not less than 75%",
# "between 60% and 80%" (the lower figure). A value that only states a ceiling
# ("no more than 50%") is not a minimum, and a value with no percentage at all
# ("a majority") cannot be compared: both are left for the board to check by hand.
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|per\s*cent\b|percent\b)", re.I)
_FLOOR_RE = re.compile(r"\b(?:at least|not less than|no less than|a minimum of|minimum)\b", re.I)
_CEILING_RE = re.compile(r"\b(?:at most|not more than|no more than|up to|a maximum of|maximum|"
                         r"below|less than|under)\b", re.I)


def _word_re(keyword):
    if keyword not in _WORD_RE_CACHE:
        _WORD_RE_CACHE[keyword] = re.compile(r"\b" + re.escape(keyword) + r"s?\b", re.I)
    return _WORD_RE_CACHE[keyword]


def mentions(text, keyword):
    """True when `text` names `keyword` (plural matched), ruled out or not."""
    return bool(_word_re(keyword).search(text or ""))


def allows(wording, keyword):
    """True when `wording` names `keyword` in a statement that does not negate.
    Shared with cfo.policy.coverage, which reads a quote for the instruments it
    permits with the same eye."""
    return any(mentions(part, keyword) and not _NEGATION_RE.search(part)
               for part in _STATEMENT_SPLIT_RE.split(wording or ""))


# The names this module used before the two readers above were shared.
_mentions = mentions
_allows = allows


def _norm(text):
    return " ".join(str(text or "").split()).casefold()


def stated_pct(value):
    """The minimum percentage a value stated in a policy sets, or None when it
    cannot be read as one (see the note on _PERCENT_RE)."""
    parsed = percent.parse_range(value)
    if parsed:
        return float(parsed[0])
    text = str(value or "")
    numbers = [float(n) for n in _PERCENT_RE.findall(text) if float(n) <= 100]
    if not numbers:
        return None
    if not _FLOOR_RE.search(text) and _CEILING_RE.search(text):
        return None
    return min(numbers)


# Reading a stated maximum horizon: "12 months", "2 years", "18 months",
# "6-12 months" (the lower end). Read the same conservative way as
# stated_pct's minimum -- when more than one figure is stated, the lower
# (shorter) one is the one compared, so a longer figure stated elsewhere
# cannot hide a real shortfall.
_MONTHS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*months?\b", re.I)
_YEARS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*years?\b", re.I)
_PERIOD_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|" + chr(0x2013) + r"|to)\s*"
                              r"(\d+(?:\.\d+)?)\s*(months?|years?)\b", re.I)
_NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
                 "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
                 "twelve": "12", "eighteen": "18", "twenty-four": "24", "thirty-six": "36"}


def _in_figures(text):
    """Policy wording spells small numbers out ("twelve months", "three years"),
    so the readers below see a figure either way. A number already in digits is
    left alone."""
    # Longest first: a hyphen is a word boundary, so "four" would otherwise match
    # inside "twenty-four" and leave "twenty-4".
    for word in sorted(_NUMBER_WORDS, key=len, reverse=True):
        text = re.sub(r"\b" + word + r"\b", _NUMBER_WORDS[word], text, flags=re.I)
    return text


def stated_months(value):
    """The number of months a value states -- "12 months", "twelve months",
    "2 years" -- or None when it states no period at all (see the note above)."""
    text = _in_figures(str(value or ""))
    found = [float(n) for n in _MONTHS_RE.findall(text)] + [float(n) * 12
                                                             for n in _YEARS_RE.findall(text)]
    for low, _high, unit in _PERIOD_RANGE_RE.findall(text):
        found.append(float(low) * (12 if unit.lower().startswith("year") else 1))
    return min(found) if found else None


def as_list(value):
    """A facility's `instruments_permitted` as a list of non-empty strings. A
    bare string is one instrument, never a list of its characters; anything
    else is no list at all."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _forbids(quote):
    """True when the facility's own words rule anything out -- a negation or
    an exclusivity -- rather than naming ways of complying with it."""
    text = str(quote or "")
    return bool(_NEGATION_RE.search(text) or _EXCLUSIVE_RE.search(text))


def _plural(instrument):
    """"interest rate swap" -> "interest rate swaps": a list of instruments
    reads as a list of kinds of thing, not one of each. Anything already
    plural, or not ending in a word, is left as it is."""
    text = str(instrument or "").strip()
    last = text.rpartition(" ")[2]
    if not last or not last[-1:].isalpha() or last.lower().endswith("s"):
        return text
    return f"{text}es" if last.lower().endswith(("ch", "sh", "x", "z")) else f"{text}s"


def _instrument_list(permitted):
    """"forwards, interest rate swaps and interest rate caps" -- the facility's
    own list, as a sentence rather than as data."""
    names = [_plural(item) for item in permitted]
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _within(instrument, permitted):
    """True when the facility's `permitted` list covers `instrument`."""
    keywords = [kw for kw in INSTRUMENT_KEYWORDS if _allows(instrument, kw)]
    if keywords:
        return all(any(_mentions(item, kw) for item in permitted) for kw in keywords)
    wanted = _norm(instrument)
    return any(wanted in _norm(item) or _norm(item) in wanted for item in permitted)


def _facility_name(facility):
    return (str(facility.get("facility_name") or "").strip()
            or str(facility.get("document") or "").strip() or "The facility")


def _clause_ref(source):
    source = str(source or "").strip()
    return f"clause {source}" if source[:1].isdigit() else source


def _cite(name, source):
    """The facility's citation for a finding: "Term Loan A, clause 22.3"."""
    ref = _clause_ref(source)
    return f"{name}, {ref}" if ref else name


def _where(name, source):
    """The facility as the subject of a constraint: "Term Loan A clause 22.3"."""
    ref = _clause_ref(source)
    return f"{name} {ref}" if ref else name


def _months_phrase(months):
    """"3 years" / "18 months" -- a number of months as a policy reads it."""
    months = int(months)
    if months % 12 == 0:
        years = months // 12
        return f"{years} year" + ("s" if years != 1 else "")
    return f"{months} months"


def _period_phrase(months):
    """"at least 3 years" / "at least 18 months" -- the facility's own period
    requirement, worded on its own rather than trailing another sentence."""
    return f"at least {_months_phrase(months)}"


def _period(months):
    if not _number(months) or months <= 0:
        return ""
    return f" for {_period_phrase(months)}"


class _Context:
    def __init__(self, selection, coverage, existing_policy, policy_document):
        self.selection = selection
        self.by_id = {clause.id: clause for clause in selection.selected}
        self.coverage = coverage or {}
        self.existing = bool(existing_policy)
        self.document = str(policy_document or "").strip()
        self.contradictions, self.constraints, self.warnings = [], [], []

    def clause(self, clause_id):
        return self.by_id.get(clause_id)

    def decision(self, clause_id, parameter_id):
        return next((d for d in self.selection.decisions
                     if d["clause_id"] == clause_id and d["id"] == parameter_id), None)

    def decision_for(self, parameter_id):
        """The board decision with this parameter id, whichever clause carries
        it (board parameter ids are unique across the library -- a test in
        tests/test_policy_coverage.py holds them to it)."""
        return next((d for d in self.selection.decisions if d["id"] == parameter_id), None)

    def _entry(self, clause_id):
        entry = self.coverage.get(clause_id)
        return entry if isinstance(entry, dict) else {}

    def status(self, clause_id):
        return self._entry(clause_id).get("status")

    def stated(self, clause_id, parameter_id):
        return [item for item in self._entry(clause_id).get("stated", []) or []
                if isinstance(item, dict) and item.get("parameter_id") == parameter_id]

    def instruments(self, clause_id):
        return [item for item in self._entry(clause_id).get("instruments", []) or []
                if isinstance(item, dict) and str(item.get("instrument") or "").strip()]

    def has_nothing_on(self, clause_id):
        """True when coverage looked for this clause in the company's policy and
        found no wording, no stated value and no instrument for it."""
        entry = self._entry(clause_id)
        return (entry.get("status") == "missing" and not entry.get("stated")
                and not entry.get("instruments"))

    def policy_source(self, source=""):
        source = str(source or "").strip()
        if self.document and source:
            return f"{self.document}, {source}"
        return self.document or source

    def contradiction(self, clause_id, facility, item, summary, policy_quote="", policy_source=""):
        name = _facility_name(facility)
        self.contradictions.append({
            "clause_id": clause_id, "summary": summary,
            "detail": str(item.get("description") or "").strip(),
            "quote": str(item.get("quote") or ""), "source": _cite(name, item.get("source")),
            "facility": name, "policy_quote": policy_quote, "policy_source": policy_source})

    def constraint(self, decision, facility, item, requirement, separator=" "):
        if decision is None:
            return
        name = _facility_name(facility)
        text = f"{_where(name, item.get('source'))}{separator}{requirement}"
        if any(c["clause_id"] == decision["clause_id"] and c["parameter_id"] == decision["id"]
               and c["text"] == text for c in self.constraints):
            return
        self.constraints.append({"clause_id": decision["clause_id"], "parameter_id": decision["id"],
                                 "clause_title": decision["clause_title"], "facility": name,
                                 "source": str(item.get("source") or ""),
                                 "quote": str(item.get("quote") or ""), "text": text})

    def clause_constraint(self, clause, facility, item, requirement, separator=" "):
        """`constraint`'s sibling for a facility requirement that bears on a
        clause with no board parameter to attach to: `parameter_id` is
        always `""`, so this can never collide with, or be mistaken for, a
        parameter-level constraint on the same clause. Never raised when
        `clause` is None -- the clause was not selected for this run, so
        there is nowhere for the requirement to go (see the module
        docstring, and `constraint`'s own decision-is-None guard)."""
        if clause is None:
            return
        name = _facility_name(facility)
        text = f"{_where(name, item.get('source'))}{separator}{requirement}"
        if any(c["clause_id"] == clause.id and c["parameter_id"] == "" and c["text"] == text
               for c in self.constraints):
            return
        self.constraints.append({"clause_id": clause.id, "parameter_id": "",
                                 "clause_title": clause.title, "facility": name,
                                 "source": str(item.get("source") or ""),
                                 "quote": str(item.get("quote") or ""), "text": text})


def _compare_stated_floor(ctx, facility, requirement, clause_id, parameter_id, required, reader,
                          summary, label, unit):
    """The policy's stated value for one board parameter, against the figure
    the facility requires -- shared by the percentage comparison
    (`_compare_stated_minimum`) and the hedging-horizon comparison
    (`_compare_stated_horizon`): a stated value below `required` is a Critical
    citing both documents; one that meets it is nothing; one `reader` cannot
    read at all is a warning, never a guess. Returns True when the policy
    stated anything at all for that parameter, readable or not -- the caller
    then knows the policy is not silent on it.

    `reader` turns a stated value into a comparable number, or None when it
    cannot (`stated_pct`, `stated_months`). `summary` is called with the
    stated value to word the finding, so every side differs only in its
    sentence, never in the comparison itself."""
    stated = ctx.stated(clause_id, parameter_id)
    if not stated:
        return False
    readable = [(reader(item.get("value")), item) for item in stated]
    below = sorted(((value, item) for value, item in readable
                    if value is not None and value < required), key=lambda pair: pair[0])
    if below:
        item = below[0][1]
        ctx.contradiction(clause_id, facility, requirement, summary(item.get("value")),
                          policy_quote=str(item.get("quote") or ""),
                          policy_source=ctx.policy_source(item.get("source")))
    for value, item in readable:
        if value is None:
            ctx.warnings.append((parameter_id,
                                 f"the policy states '{item.get('value')}' for {label}, which "
                                 f"could not be read as {unit}; compare it with "
                                 f"{_facility_name(facility)} by hand"))
    return True


def _compare_stated_minimum(ctx, facility, requirement, clause_id, parameter_id, required,
                            summary, label):
    """The policy's stated minimum proportion for one board parameter, against
    the figure the facility requires -- see `_compare_stated_floor`."""
    return _compare_stated_floor(ctx, facility, requirement, clause_id, parameter_id, required,
                                 stated_pct, summary, label, "a percentage")


def _compare_stated_horizon(ctx, facility, requirement, clause_id, parameter_id, required_months,
                            summary, label):
    """The policy's stated maximum hedging horizon, against the minimum period
    of cover the facility requires -- exactly `_compare_stated_floor`'s
    comparison, read in months rather than per cent: a horizon shorter than
    what the facility requires is exactly as much a shortfall as a ratio
    below it (see the module docstring)."""
    return _compare_stated_floor(ctx, facility, requirement, clause_id, parameter_id,
                                 required_months, stated_months, summary, label, "a period")


def _check_fixed_floor(ctx, facility, requirement):
    required = requirement.get("proportion_pct_min")
    # No figure, or a floor of nothing: there is nothing to fall short of. A
    # requirement that states only a term ("hedging stays in place for the life
    # of the facility") still binds the board's decision, so it is shown beside
    # it rather than dropped -- interest rate has no horizon parameter of its
    # own to compare a term against (see _check_period).
    if not _number(required) or required <= 0:
        months = requirement.get("minimum_period_months")
        clause = ctx.clause(IR_FIXED_FLOATING_CLAUSE_ID)
        if _number(months) and months > 0 and clause is not None:
            ctx.constraint(ctx.decision(clause.id, MIN_FIXED_PARAMETER_ID), facility, requirement,
                           "requires hedging in place" + _period(months))
        return
    need = f"at least {required:g}% of drawn debt at fixed rates" + _period(
        requirement.get("minimum_period_months"))
    clause = ctx.clause(IR_FIXED_FLOATING_CLAUSE_ID)
    if clause is None:
        ctx.contradiction(IR_FIXED_FLOATING_CLAUSE_ID, facility, requirement,
                          f"The facility requires {need}, but the redrafted policy has no clause "
                          "on the fixed and floating mix: it was left out for this company's "
                          "profile.")
        return
    ctx.constraint(ctx.decision(clause.id, MIN_FIXED_PARAMETER_ID), facility, requirement,
                   f"requires {need}")
    if not ctx.existing:
        return
    stated = _compare_stated_minimum(
        ctx, facility, requirement, clause.id, MIN_FIXED_PARAMETER_ID, required,
        lambda value: f"The facility requires {need}, but the company's policy sets a minimum "
                      f"of {value}.",
        "the minimum fixed proportion")
    if stated:
        return
    if ctx.has_nothing_on(clause.id):
        ctx.contradiction(clause.id, facility, requirement,
                          f"The facility requires {need}, but the company's policy has no clause "
                          "on the fixed and floating mix of its debt.",
                          policy_source=ctx.policy_source())


def _check_commodity(ctx, facility, requirement):
    """The commodity hedge ratio, compared exactly as the interest-rate fixed
    floor is (`_check_fixed_floor`): one clause, one board figure, no
    per-band split the way foreign exchange has."""
    required = requirement.get("proportion_pct_min")
    # No figure, or a floor of nothing: there is nothing to fall short of.
    if not _number(required) or required <= 0:
        return
    need = f"at least {required:g}% of its commodity exposure hedged" + _period(
        requirement.get("minimum_period_months"))
    clause = ctx.clause(COMMODITY_CLAUSE_ID)
    if clause is None:
        ctx.contradiction(COMMODITY_CLAUSE_ID, facility, requirement,
                          f"The facility requires {need}, but the redrafted policy has no clause "
                          "on commodity hedging: it was left out for this company's profile.")
        return
    ctx.constraint(ctx.decision(clause.id, MIN_COMMODITY_PARAMETER_ID), facility, requirement,
                   f"requires {need}")
    if not ctx.existing:
        return
    stated = _compare_stated_minimum(
        ctx, facility, requirement, clause.id, MIN_COMMODITY_PARAMETER_ID, required,
        lambda value: f"The facility requires {need}, but the company's policy sets a minimum "
                      f"of {value}.",
        "the minimum commodity hedge ratio")
    if stated:
        return
    if ctx.has_nothing_on(clause.id):
        ctx.contradiction(clause.id, facility, requirement,
                          f"The facility requires {need}, but the company's policy has no clause "
                          "on commodity hedging.", policy_source=ctx.policy_source())


def _period_of_cover(requirement):
    """(months, how it reads in a sentence) for the longest period of cover a
    facility requires, or (None, "") when it requires none.

    Two different periods reach the same ceiling, and a requirement may state
    either or both (see the module docstring): how long the obligation runs
    (`minimum_period_months`) and how far out the exposure it covers falls due
    (`exposure_tenor_months`). The policy's maximum hedging horizon has to
    reach whichever is longer, so that is the one compared -- and each is
    worded as what it is, so a finding never describes one as the other. The
    obligation's own duration wins a tie, keeping the wording a facility
    stating only a term has always had."""
    stated = [(requirement.get("minimum_period_months"),
               lambda m: f"cover for {_period_phrase(m)}"),
              (requirement.get("exposure_tenor_months"),
               lambda m: f"cover for exposure falling due within {_months_phrase(m)}")]
    usable = [(months, wording) for months, wording in stated if _number(months) and months > 0]
    if not usable:
        return None, ""
    months, wording = max(usable, key=lambda pair: pair[0])
    return months, wording(months)


def _check_period(ctx, facility, requirement):
    """The period of cover the facility requires (`_period_of_cover`), against
    the policy's stated maximum hedging horizon, for whichever kind has a
    board decision for one (see HORIZON_CLAUSE_ID/HORIZON_PARAMETER_ID and the
    module docstring -- interest rate has none, so this is a permanent no-op
    for it under the current library). Safe to call for every requirement
    regardless of kind: `ctx.decision`/`ctx.stated` already degrade to nothing
    when the clause is not one this run is looking for."""
    months, need = _period_of_cover(requirement)
    if months is None:
        return
    kind = requirement.get("kind")
    clause_id, parameter_id = HORIZON_CLAUSE_ID.get(kind), HORIZON_PARAMETER_ID.get(kind)
    if clause_id is None:
        return
    ctx.constraint(ctx.decision(clause_id, parameter_id), facility, requirement, f"requires {need}")
    if not ctx.existing:
        return
    # Only the tenor of the exposure can outrun a hedging horizon. How long the
    # obligation itself runs cannot: a facility may require cover maintained for
    # three years over exposure that falls due within six months, and a policy
    # that hedges twelve months ahead meets that by rolling its hedges. The
    # obligation's own term still reaches the board, as the constraint above.
    tenor = requirement.get("exposure_tenor_months")
    if not _number(tenor) or tenor <= 0:
        return
    _compare_stated_horizon(
        ctx, facility, requirement, clause_id, parameter_id, tenor,
        lambda value: (f"The facility requires cover for exposure falling due within "
                       f"{_months_phrase(tenor)}, but the company's policy sets a maximum "
                       f"hedging horizon of {value}."),
        "the maximum hedging horizon")


def _check_instruments(ctx, facility, requirement):
    """The instruments a facility names, against those the policy permits.

    Only a facility whose own quote rules something out (`_forbids`) can be
    contradicted by a policy permitting something else. A clause that says a
    fixed rate may be achieved "whether by fixed rate loan, interest rate swap
    or interest rate cap" lists ways of complying, not a prohibition: it is
    set beside the board's decision as what the facility contemplates, and a
    policy that also permits collars conflicts with nothing in it."""
    permitted = as_list(requirement.get("instruments_permitted"))
    kind = requirement.get("kind")
    clause = ctx.clause(PERMITTED_INSTRUMENTS_CLAUSE_ID.get(kind))
    # With no permitted-instruments clause in the redraft there is no decision to
    # set the facility's list beside, and a policy silent on instruments permits
    # nothing the facility forbids: not a contradiction.
    if not permitted or clause is None:
        return
    listed = _instrument_list(permitted)
    exclusive = _forbids(requirement.get("quote"))
    ctx.constraint(ctx.decision(clause.id, PERMITTED_PRODUCTS_PARAMETER_ID[kind]), facility,
                   requirement, f"permits only {listed}" if exclusive
                   else f"names {listed} as the instruments it contemplates")
    if not ctx.existing or not exclusive:
        return
    by_quote = {}
    for item in ctx.instruments(clause.id):
        if not _within(item["instrument"], permitted):
            key = (str(item.get("quote") or ""), str(item.get("source") or ""))
            by_quote.setdefault(key, []).append(item["instrument"])
    for (quote, source), extra in by_quote.items():
        ctx.contradiction(clause.id, facility, requirement,
                          f"The facility permits only {listed}, but the company's policy also "
                          f"permits {_instrument_list(extra)}.",
                          policy_quote=quote, policy_source=ctx.policy_source(source))


def _fx_band(months):
    """The hedge ratio band covering exposure that falls due within `months`
    (the requirement's `exposure_tenor_months`, never how long the obligation
    runs -- see the module docstring), or None when the facility states no
    tenor. A tenor beyond the longest band is compared with that longest band
    -- the policy has nothing further out."""
    if not _number(months) or months <= 0:
        return None
    for limit, parameter_id, band in FX_RATIO_BANDS:
        if months <= limit:
            return parameter_id, band
    return FX_RATIO_BANDS[-1][1], FX_RATIO_BANDS[-1][2]


def _check_currency(ctx, facility, requirement):
    currency = str(requirement.get("currency") or "").strip().upper()
    if not currency:
        return
    share = requirement.get("proportion_pct_min")
    required = share if _number(share) and share > 0 else None
    months = requirement.get("exposure_tenor_months")
    covered = _fx_band(months)
    need = (f"at least {required:g}% of {currency} exposure to be hedged" if required is not None
            else f"{currency} exposure to be hedged")
    if covered is not None:
        need += f", for exposure falling due within {int(months)} months"
    fx_clauses = [c for c in ctx.selection.selected if c.section == FX_SECTION_ID]
    if not fx_clauses:
        # No single clause names the whole FX section; findings.build numbers
        # this finding itself (facility-<n>) rather than mis-attributing it.
        ctx.contradiction(None, facility, requirement,
                          f"The facility requires {need}, but the redrafted policy has no foreign "
                          "exchange section: it was left out for this company's profile.")
        return
    # Which bands the requirement bears on: the one covering the period it
    # states; every band when it states none and a proportion to compare;
    # otherwise just the nearest, since there is no figure to compare at all.
    if covered is not None:
        bands = [covered]
    elif required is not None:
        bands = [(parameter_id, band) for _, parameter_id, band in FX_RATIO_BANDS]
    else:
        bands = [(FX_RATIO_BANDS[0][1], FX_RATIO_BANDS[0][2])]
    for parameter_id, _band in bands:
        ctx.constraint(ctx.decision_for(parameter_id), facility, requirement, f"requires {need}")
    if ctx.existing and required is not None:
        for parameter_id, band in bands:
            decision = ctx.decision_for(parameter_id)
            clause_id = decision["clause_id"] if decision else FX_RATIO_CLAUSE_ID
            _compare_stated_minimum(
                ctx, facility, requirement, clause_id, parameter_id, required,
                lambda value, band=band: f"The facility requires {need}, but the company's policy "
                                         f"sets a minimum of {value} for exposure falling due in "
                                         f"{band}.",
                f"the minimum hedge ratio for {band}")
    if ctx.existing and all(ctx.has_nothing_on(c.id) for c in fx_clauses):
        ctx.contradiction(None, facility, requirement,
                          f"The facility requires {need}, but the company's policy has no clause "
                          "on foreign exchange hedging.",
                          policy_source=ctx.policy_source())


def _check_restriction(ctx, facility, restriction):
    """A facility's restriction (other than `indebtedness` or `security`,
    which `RESTRICTION_CLAUSE_ID` does not name, so this is a permanent
    no-op for them, exactly as before this kind of restriction was read at
    all) beside the clause it bears on -- `investments` on the board
    parameter its clause carries (`Context.constraint`, unchanged), the
    other three on the clause as a whole (`Context.clause_constraint`),
    since the library gives none of them a board parameter of its own.

    A restriction is a prohibition the facility places on the company, never
    a minimum the policy can fall short of, so this never calls
    `ctx.contradiction`: a policy silent on who the company hedges with, or
    what it invests in, does not conflict with a facility that names one."""
    kind = restriction.get("kind")
    clause_id = RESTRICTION_CLAUSE_ID.get(kind)
    if clause_id is None:
        return
    # Joined with a colon, and the model's own capitalisation kept. Every other
    # constraint supplies its own verb, because the comparison writes it
    # ("clause 22.3 requires at least 60% ..."); a restriction's wording is
    # whatever the model wrote, and it writes a whole sentence at least as
    # often as a verb phrase. Space-joining a sentence lost the verb -- the
    # live run produced "Facility Agreement clause 22.2 hedging transactions
    # may be entered into only with the Lender" -- and no fixed connector fits
    # both shapes ("states that restricts hedging counterparties" is worse
    # still). A colon introduces either one, so the sentence cannot come out
    # malformed whichever the model chooses. The prompt asks for a complete
    # sentence, which is the shape that reads best here.
    requirement = str(restriction.get("description") or "").strip()
    parameter_id = RESTRICTION_PARAMETER_ID.get(kind)
    if parameter_id is not None:
        ctx.constraint(ctx.decision(clause_id, parameter_id), facility, restriction, requirement,
                       separator=": ")
    else:
        ctx.clause_constraint(ctx.clause(clause_id), facility, restriction, requirement,
                              separator=": ")


def merge_parts(parts, results):
    """The facilities, one per document, from the obligations task's output
    for each part of each document.

    `parts` is `policy obligations-input`'s listing (`obligations-parts.json`,
    in document and part order: each `{file, document, document_index, part,
    of}`); `results` maps a part's `file` to what `policy obligations-collect`
    stored for it (`{"result": <the task's output>, ...}`).

    Returns (facilities, documents). A facility is `{facility_name, document,
    hedging_requirements, covenants, reporting_duties, restrictions}`: its
    name is the first one any part gives (the document's file name when none
    does), and each list is its parts' entries in order, de-duplicated on
    (quote, source). A document none of whose parts was collected has no
    facility. `documents` is one `{document, index, facility, parts, read,
    unread}` per document -- `read` and `unread` list its part numbers -- so a
    document read only in part can be named as such."""
    order, by_index = [], {}
    for entry in parts:
        index = entry.get("document_index")
        if index not in by_index:
            by_index[index] = {"document": entry.get("document", ""), "index": index,
                               "facility": "", "parts": entry.get("of", 0), "read": [],
                               "unread": [], "_found": []}
            order.append(index)
        doc = by_index[index]
        stored = results.get(entry.get("file"))
        if isinstance(stored, dict) and isinstance(stored.get("result"), dict):
            doc["read"].append(entry.get("part"))
            doc["_found"].append(stored["result"])
        else:
            doc["unread"].append(entry.get("part"))
    facilities, documents = [], []
    for index in order:
        doc = by_index[index]
        found = doc.pop("_found")
        if found:
            name = next((str(r.get("facility_name") or "").strip() for r in found
                         if str(r.get("facility_name") or "").strip()), "") or doc["document"]
            facility = {"facility_name": name, "document": doc["document"]}
            for key in FACILITY_LISTS:
                seen, merged = set(), []
                for result in found:
                    for item in result.get(key, []) or []:
                        if not isinstance(item, dict):
                            continue
                        mark = (_norm(item.get("quote")), _norm(item.get("source")))
                        if mark in seen:
                            continue
                        seen.add(mark)
                        merged.append(item)
                facility[key] = merged
            facilities.append(facility)
            doc["facility"] = name
        documents.append(doc)
    return facilities, documents


def compare(facilities, selection, coverage=None, existing_policy=False, policy_document=""):
    """{"contradictions", "constraints", "warnings"} -- see the module docstring.

    `coverage` is the dict cfo.policy.coverage.merge returns (without its
    `_warnings`); `policy_document` names the company's policy in each
    contradiction's policy citation."""
    ctx = _Context(selection, coverage, existing_policy, policy_document)
    for facility in facilities or []:
        for requirement in facility.get("hedging_requirements", []) or []:
            if not isinstance(requirement, dict):
                continue
            kind = requirement.get("kind")
            if kind == "interest_rate":
                _check_fixed_floor(ctx, facility, requirement)
            if kind == "currency":
                _check_currency(ctx, facility, requirement)
            if kind == "commodity":
                _check_commodity(ctx, facility, requirement)
            _check_instruments(ctx, facility, requirement)
            _check_period(ctx, facility, requirement)
        for restriction in facility.get("restrictions", []) or []:
            if not isinstance(restriction, dict):
                continue
            _check_restriction(ctx, facility, restriction)
    return {"contradictions": ctx.contradictions, "constraints": ctx.constraints,
            "warnings": ctx.warnings}


def contradictions(facilities, selection, coverage=None, existing_policy=False, policy_document=""):
    """Only the contradictions `compare` finds."""
    return compare(facilities, selection, coverage, existing_policy, policy_document)["contradictions"]
