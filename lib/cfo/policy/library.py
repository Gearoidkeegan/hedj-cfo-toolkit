"""The clause library: one Markdown file per clause, validated as a whole.

Each file is a JSON front-matter block between `---` fences, then the wording
under `## Short` and `## Full` headings. JSON rather than YAML because the
toolkit has no YAML parser and the metadata nests.
"""
import dataclasses
import json
import os
import re

from cfo.console import ToolkitError
from cfo.paths import asset
from cfo.profile.conditions import validate_condition
from cfo.profile.schema import FIELDS

SECTIONS = (
    ("01-purpose", "Purpose, scope and review cycle"),
    ("02-governance", "Governance"),
    ("03-risk", "Risk identification and measurement"),
    ("04-fx", "Foreign exchange risk"),
    ("05-interest-rate", "Interest rate risk"),
    ("06-commodity", "Commodity and energy risk"),
    ("07-counterparty", "Counterparty risk"),
    ("08-liquidity", "Liquidity and cash management"),
    ("09-investment", "Investment of surplus cash"),
    ("10-funding", "Funding, debt and covenants"),
    ("11-payments", "Payments and bank mandates"),
    ("12-hedge-accounting", "Hedge accounting and reporting"),
    ("13-regulatory", "Regulatory"),
    ("14-systems", "Systems, records and annexes"),
)
SECTION_IDS = tuple(section for section, _ in SECTIONS)
SECTION_TITLES = dict(SECTIONS)
TIERS = ("lite", "moderate", "comprehensive")
SCOPES = ("hedging", "treasury", "both")
SIZE_BANDS = ("small", "mid")
# The countries the library's regulatory clauses (EMIR, MiFID, hedge accounting
# under IFRS/FRS 102) are framed around: Ireland, the United Kingdom, and the
# rest of the European Union (M1, spec S11). Shared by render_report.py (the
# gap report's note) and render_policy.py (the same note, carried into the
# redraft's Regulatory section -- final gate review item 5): a company
# registered anywhere else still gets the clauses, since the toolkit never
# asserts a regime applies (see the skill's own ground rule), but both
# documents say the framing assumes one of these jurisdictions.
_EU27 = frozenset(("AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR",
                  "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI",
                  "SK"))
REGULATORY_ASSUMED_COUNTRIES = _EU27 | {"GB"}
_FENCE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.S)
# A bare "## Short" / "## Full" heading, or the same with a scope in
# parentheses -- "## Short (hedging)" -- carrying that scope's override
# text instead of the plain wording above it (see `Clause.wording`).
_HEADING = re.compile(r"^##\s+(Short|Full)(?:\s*\((hedging|treasury)\))?\s*$", re.M)


@dataclasses.dataclass
class Clause:
    id: str
    section: str
    title: str
    scope: str
    tier: str
    applies_if: object
    omit_reason: str
    parameters: list
    regulation: list
    adaptable: bool
    short: str
    full: str
    path: str = ""
    # A clause whose `scope` is "both" may read differently depending on
    # which other sections the run actually has behind it -- a hedging-only
    # policy has no liquidity, funding or payments section, so a clause that
    # says "each of the company's risks is covered in its own section" is
    # only true for a treasury run. `## Short (hedging)` / `## Full
    # (treasury)` headings (see `_split_wording`) carry the override text for
    # one scope; a scope with no override keeps reading the plain `## Short`
    # / `## Full` above. Keyed by scope ("hedging" or "treasury"); almost
    # always empty, since only a handful of clauses need this.
    short_by_scope: dict = dataclasses.field(default_factory=dict)
    full_by_scope: dict = dataclasses.field(default_factory=dict)

    def wording(self, tier, scope=None):
        """The wording a policy of this length uses, overridden for `scope`
        when this clause carries one (see `short_by_scope`/`full_by_scope`).
        Falls back to the plain wording when there is no override for this
        scope, or when no scope is given at all."""
        base = self.short if tier == "lite" else self.full
        overrides = self.short_by_scope if tier == "lite" else self.full_by_scope
        return overrides.get(scope, base) if scope else base

    def board_parameters(self):
        return [p for p in self.parameters if p.get("decision") == "board"]


def tier_rank(tier):
    return TIERS.index(tier)


def decapitalize(text):
    """`text` (a clause title or a parameter label) fit to sit mid-sentence
    ("The policy has no clause covering <text>."): its leading word is
    lower-cased, unless that word looks like an acronym -- two or more
    uppercase letters, as in "EMIR" or "MiFID" -- in which case `text` is
    returned exactly as written.

    Only ever the first character changes: every title and label in this
    library is already sentence case (one capital, then lower-case, aside
    from an acronym), so nothing later in the string needs touching -- and,
    unlike a blanket `.lower()`, nothing later in the string is ever forced
    to lower-case either, so an acronym elsewhere in the text (not just as
    the leading word) keeps its case too."""
    text = str(text or "")
    if not text:
        return text
    first_word = text.split(None, 1)[0]
    if sum(1 for ch in first_word if ch.isupper()) >= 2:
        return text
    return text[0].lower() + text[1:]


def _split_wording(body, where, problems):
    """The clause's plain Short/Full wording, plus any scope-specific
    overrides of either, keyed (name, scope) -- `("short", None)` is the
    plain `## Short` every clause needs; `("short", "hedging")` is an
    optional `## Short (hedging)` override.

    Uses `finditer`, not `_HEADING.split`: with two capture groups (the
    heading name and its optional scope) a plain `re.split` interleaves
    `None`s for the scope whenever it is absent, which the single-capture
    version this replaced never had to handle."""
    matches = list(_HEADING.finditer(body))
    sections, seen = {}, set()
    for index, match in enumerate(matches):
        name, scope = match.group(1).lower(), match.group(2)
        key = (name, scope)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        if key in seen:
            label = "## " + match.group(1) + (f" ({scope})" if scope else "")
            problems.append((where, f"contains a duplicate '{label}' heading"))
            continue
        seen.add(key)
        sections[key] = body[match.end():end].strip()
    out = {"short": sections.get(("short", None), ""), "full": sections.get(("full", None), "")}
    for name in ("short", "full"):
        if not out[name]:
            problems.append((where, f"needs a '## {name.capitalize()}' section with wording"))
        by_scope = {scope: sections[(name, scope)] for scope in ("hedging", "treasury")
                   if sections.get((name, scope))}
        out[f"{name}_by_scope"] = by_scope
    return out


def _check_parameters(meta, where, problems):
    parameters = meta.get("parameters", [])
    if not isinstance(parameters, list):
        problems.append((where, "parameters must be a list"))
        return []
    for index, parameter in enumerate(parameters):
        at = f"{where} parameter {index}"
        if not isinstance(parameter, dict) or not parameter.get("id") or not parameter.get("label"):
            problems.append((at, "needs an id and a label"))
            continue
        if parameter.get("decision") not in ("board", None):
            problems.append((at, "decision must be 'board' when present"))
        typical = parameter.get("typical")
        if not isinstance(typical, dict):
            problems.append((at, "needs typical values by size band"))
            continue
        for band in SIZE_BANDS:
            if not typical.get(band):
                problems.append((at, f"needs a typical value for the '{band}' size band"))
        for band in typical:
            if band not in SIZE_BANDS:
                problems.append((at, f"unknown size band '{band}'"))
    return parameters


def parse_clause(text, where):
    problems = []
    text = text.lstrip('﻿').replace('\r\n', '\n').replace('\r', '\n')
    match = _FENCE.match(text)
    if not match:
        return None, [(where, "needs JSON front matter between --- fences")]
    try:
        meta = json.loads(match.group(1))
    except ValueError as exc:
        return None, [(where, f"front matter is not valid JSON: {exc}")]
    if not isinstance(meta, dict):
        return None, [(where, "front matter must be a JSON object")]

    for key in ("id", "section", "title"):
        if not meta.get(key):
            problems.append((where, f"needs {key}"))
    if meta.get("section") and meta["section"] not in SECTION_IDS:
        problems.append((where, f"unknown section '{meta['section']}'"))
    if meta.get("scope") not in SCOPES:
        problems.append((where, f"scope must be one of {', '.join(SCOPES)}"))
    if meta.get("tier") not in TIERS:
        problems.append((where, f"tier must be one of {', '.join(TIERS)}"))
    if meta.get("adaptable", False) is not False:
        problems.append((where, "adaptable must be false in this version"))
    if not isinstance(meta.get("regulation", []), list):
        problems.append((where, "regulation must be a list"))

    applies_if = meta.get("applies_if", "always")
    for message in validate_condition(applies_if, FIELDS):
        problems.append((where, message.replace("ask_if", "applies_if")))
    if applies_if != "always" and not str(meta.get("omit_reason", "")).strip():
        problems.append((where, "a conditional clause needs an omit_reason"))

    parameters = _check_parameters(meta, where, problems)
    wording = _split_wording(match.group(2), where, problems)
    if problems:
        return None, problems
    return Clause(id=meta["id"], section=meta["section"], title=meta["title"],
                  scope=meta["scope"], tier=meta["tier"], applies_if=applies_if,
                  omit_reason=str(meta.get("omit_reason", "")).strip(), parameters=parameters,
                  regulation=list(meta.get("regulation", [])), adaptable=False,
                  short=wording["short"], full=wording["full"],
                  short_by_scope=wording["short_by_scope"],
                  full_by_scope=wording["full_by_scope"]), []


def load_library(root=None):
    root = root or asset("policy")
    clauses, problems, seen = [], [], {}
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if os.path.isfile(path) and name.lower().endswith(".md"):
                problems.append((name, "is a .md file in the root, should be in a section folder"))
    for section in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        folder = os.path.join(root, section)
        if not os.path.isdir(folder):
            continue
        if section not in SECTION_IDS:
            problems.append((section, "is not a known section folder"))
            continue
        try:
            names = sorted(os.listdir(folder))
        except PermissionError:
            problems.append((section, "cannot read folder (permission denied)"))
            continue
        for name in names:
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                if name.lower().endswith(".md"):
                    where = f"{section}/{name}"
                    problems.append((where, "is a directory, not a file"))
                continue
            if not name.lower().endswith(".md"):
                continue
            where = f"{section}/{name}"
            try:
                with open(path, encoding="utf-8-sig") as fh:
                    clause, found = parse_clause(fh.read(), where)
            except UnicodeDecodeError as exc:
                problems.append((where, f"cannot read file: invalid UTF-8 at byte {exc.start}"))
                continue
            except (PermissionError, OSError) as exc:
                problems.append((where, f"cannot read file: {exc}"))
                continue
            problems.extend(found)
            if clause is None:
                continue
            if clause.section != section:
                problems.append((where, f"says section '{clause.section}' but sits in '{section}'"))
            clause_id_lower = clause.id.lower()
            if clause_id_lower in seen:
                problems.append((where, f"id '{clause.id}' is already used by {seen[clause_id_lower][0]} (ids are case-insensitive)"))
            seen[clause_id_lower] = (where, clause.id)
            clause.path = path
            clauses.append(clause)
    if problems:
        raise ToolkitError(problems)
    clauses.sort(key=lambda c: (SECTION_IDS.index(c.section), c.path))
    return clauses
