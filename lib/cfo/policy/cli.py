"""The `policy` command group's bodies: one function per command.

Each reads the run's state, does one step, writes its result and returns a
small dict for the single JSON line the toolkit prints.
"""
# cfo.documents.docx_build (render_policy, render_report, extras) and
# cfo.documents.style_profile pull in python-docx at import time, and
# cfo.workbook.build (extras) pulls in openpyxl -- like cmd_workbook_build,
# cmd_doc_build and cmd_deck_build in toolkit.py, those four are imported
# lazily inside cmd_build so every other `policy` command still works on a
# machine missing those libraries, and so importing this module at all
# (toolkit.py imports it eagerly, the same way it imports the other command
# groups' modules) never requires them either.
import dataclasses
import datetime
import json
import os
import re

from cfo.console import ToolkitError
from cfo.extract import HANDLERS as _EXTRACT_HANDLERS, TYPES as _EXTRACT_TYPES
from cfo.extract.sink import MissingDependency, Sink, render_md
from cfo.io import sha256_bytes, write_json_atomic, write_json_compact_atomic
from cfo.policy import coverage, findings as findings_module, library, obligations, panel, select, state
from cfo.policy import version as policy_version
from cfo.profile.schema import get_path
from cfo.profile.store import load_profile
from cfo.runs import company_dir_of, load_run

CORE_OUTPUTS = ("gap", "policy")


def _profile(run_dir):
    return load_profile(company_dir_of(run_dir))


def _long_date(iso_date):
    """`iso_date` ("2026-09-21") written long form ("21 September 2026"), for
    the title block -- filenames keep the ISO form themselves. Given
    anything that is not a valid ISO date, returned unchanged rather than
    raising: a document must still build even if a run's own timestamp is
    somehow unreadable."""
    try:
        day = datetime.date.fromisoformat(iso_date)
    except (TypeError, ValueError):
        return iso_date
    return f"{day.day} {day.strftime('%B')} {day.year}"


def _meta(run, selection, profile, draft_version):
    company = (profile.get("company", {}) or {}).get("name") or "The company"
    title = ("Treasury policy" if selection["scope"] == "treasury" else "Hedging policy")
    # runs.init_run stores the run's timestamp as "started_at", not "started".
    iso_date = run.get("started_at", "")[:10]
    return {"title": title, "company": company, "subtitle": company,
            "version": f"v{draft_version}", "date": _long_date(iso_date),
            "classification": "Draft for board approval"}


_MAX_FILENAME_SLUG = 40


def _filename_slug(run):
    """`run`'s own company slug (`runs._capped_slug`, already capped at 60
    characters for the run folder itself), cut a little further for a
    document's file name -- built several folders deep from a slug that
    long, plus the document name, the version and the date, a path can
    still run long enough to trip Windows' limit. Cut at a hyphen boundary
    where possible, the same way the run folder's own cap does. Empty when
    the run somehow carries no slug at all (never in practice -- a run
    always has a company name), so the file name falls back to the
    document's own name alone."""
    slug = str(run.get("company_slug") or "").strip("-")
    if len(slug) <= _MAX_FILENAME_SLUG:
        return slug
    cut = slug[:_MAX_FILENAME_SLUG]
    boundary = cut.rfind("-")
    return cut[:boundary] if boundary > 0 else cut


def _output_namer(run, draft_version):
    """A `(document, ext) -> file name` function for every file `policy
    build` writes: `<slug>-<document>-v<version>-<date>.<ext>` -- the
    company slug the run already uses, the redraft's version with a
    leading `v` and a hyphen before "draft" (`v2.2-draft`: the dot stays
    inside the number, only the space before "draft" becomes a hyphen), and
    the run's own date, ISO. No slug falls back to the document's own name
    alone, with no leading hyphen."""
    slug = _filename_slug(run)
    iso_date = run.get("started_at", "")[:10]
    version_part = "v" + draft_version.replace(" ", "-")
    prefix = f"{slug}-" if slug else ""

    def name(document, ext):
        return f"{prefix}{document}-{version_part}-{iso_date}{ext}"
    return name


_PINNED = ("size_band", "clauses", "omitted", "decisions")


def _pinned_part(selection_dict):
    return {key: selection_dict.get(key) for key in _PINNED}


def _selection_object(run_dir, profile):
    """(saved selection, Selection): the selection `policy start` pinned,
    rebuilt from its saved clause ids, omissions and decisions. Every later
    command uses it rather than selecting afresh, so the clause list the
    coverage task was asked about is the one findings, the panel and the
    redraft use.

    Selecting again from the live profile must still give the same result: if
    an answer corrected after `policy start` (or a changed clause library)
    would change the clauses, the omissions or the decisions, that is an error
    telling the user to run `policy start` again -- never a silent switch
    between two clause lists halfway through a run."""
    saved = state.require(run_dir, "selection")
    clauses = library.load_library()
    live = select.select(clauses, profile, saved["scope"], saved["tier"])
    current = {"size_band": live.size_band, "clauses": [c.id for c in live.selected],
               "omitted": live.omitted, "decisions": live.decisions}
    if _pinned_part(saved) != json.loads(json.dumps(current)):
        raise ToolkitError(("selection", "the company profile has changed since `policy start` in "
                                         "a way that changes the clauses this policy needs: run "
                                         f"`policy start --scope {saved['scope']} --tier "
                                         f"{saved['tier']}` again, then repeat the steps after it"))
    by_id = {clause.id: clause for clause in clauses}
    selected = [by_id[clause_id] for clause_id in saved["clauses"]]
    return saved, select.Selection(scope=saved["scope"], tier=saved["tier"],
                                   size_band=saved["size_band"], selected=selected,
                                   omitted=saved["omitted"], decisions=saved["decisions"],
                                   sections=select.group_sections(selected))


# A line of real content in `render_md` output: a `[location]` marker followed by
# something other than whitespace. The header and the "material to analyse" notice
# that `render_md` always writes carry no marker, so a document with no readable text
# has no such line.
_BODY_LINE_RE = re.compile(r"^\[[^\]\n]{1,80}\][^\S\n]*\S", re.M)


def _readable_text(path):
    """`_extracted_text`, refusing a document with no readable text at all.

    A scanned PDF has no text layer. Sent on, it would reach the model as an empty
    document and come back as a gap report saying the company's policy covers
    nothing, or as a facility with no obligations, and neither would be true."""
    text = _extracted_text(path)
    if not _BODY_LINE_RE.search(text):
        raise ToolkitError((path, "no readable text was found in this document, so it cannot "
                                  "be reviewed; if it is a scanned PDF, supply a Word or text "
                                  "version instead"))
    return text


def _extracted_text(path):
    """One client document's text, `[location]`-marked exactly as
    `cfo.extract` renders it (see coverage.py's module docstring: a heading
    -- a real Word style, or a literal `#` in a .md/.txt source -- must
    render the same way for `coverage.plan_batches` to find it). Reuses the
    same per-extension handlers `cfo extract` uses, so a `.docx` (or `.pdf`,
    `.pptx`, ...) given to `--doc`/`--docs` is read properly rather than
    crashed on as raw bytes; `--doc` also doubles as `style_source` for
    `--style match`, so it must stay the caller's own file, not something
    pre-extracted elsewhere."""
    if not os.path.isfile(path):
        raise ToolkitError((path, "not found"))
    ext = os.path.splitext(path)[1].lower()
    handler = _EXTRACT_HANDLERS.get(ext)
    if handler is None:
        raise ToolkitError((path, f"'{ext}' is not a document type this tool can read "
                                  f"(supported: {', '.join(sorted(_EXTRACT_HANDLERS))})"))
    sink = Sink()
    sink.start("doc", os.path.basename(path), _EXTRACT_TYPES[ext], "")
    try:
        handler(path, os.path.basename(path), sink)
    except MissingDependency as exc:
        raise ToolkitError((path, str(exc)))
    except Exception as exc:
        raise ToolkitError((path, f"could not be read ({type(exc).__name__}: {exc})"))
    return render_md(sink.docs["doc"])


def cmd_recommend(args):
    """`policy recommend`: `select.recommend`'s scope/tier suggestion for the
    run's company profile, plus the profile facts it was based on.
    `select.recommend` itself returns no reasons (see select.py), so this
    rebuilds them from the same profile fields it reads -- turnover band
    (against the same thresholds `recommend` uses), whether there is debt, a
    dedicated treasury person, and the entity count -- so a skill can explain
    the suggestion in plain words rather than restating `recommend`'s
    thresholds itself."""
    profile = _profile(args.run)
    choice = select.recommend(profile)
    turnover = get_path(profile, "size.turnover_eur") or 0
    # Named after the turnover thresholds themselves, not "small"/"mid"/"large"
    # (a small item in the final review): `select.size_band` also has a "mid"
    # band, but split at a different threshold (>= EUR 40m, no upper bound),
    # so the same word meant two different things in two places.
    if turnover >= select.COMPREHENSIVE_TURNOVER:
        band = "100m-plus"
    elif turnover >= select.MID_BAND_TURNOVER:
        band = "40m-100m"
    else:
        band = "under-40m"
    reasons = {"turnover_eur": get_path(profile, "size.turnover_eur"), "turnover_band": band,
               "has_debt": bool(get_path(profile, "debt.has_debt")),
               "dedicated_treasury": bool(get_path(profile, "team.treasury_dedicated")),
               "entities": get_path(profile, "structure.entities") or 1}
    return {"scope": choice["scope"], "tier": choice["tier"], "reasons": reasons}


def cmd_start(args):
    """Selects the clauses and pins the selection for the rest of the run (see
    `_selection_object`). Running it again -- after correcting the profile,
    say -- selects afresh but keeps what `coverage-input` recorded about the
    company's existing policy (`existing_policy`, `style_source`,
    `policy_document`, `existing_version`); if the clause list changed, it
    warns that the existing policy must be read again, since the coverage
    task was asked about the old list."""
    profile = _profile(args.run)
    chosen = select.select(library.load_library(), profile, args.scope, args.tier)
    previous = state.read(args.run, "selection") or {}
    pinned = {"scope": chosen.scope, "tier": chosen.tier, "size_band": chosen.size_band,
              "existing_policy": bool(previous.get("existing_policy", False)),
              "style_source": previous.get("style_source"),
              "policy_document": previous.get("policy_document"),
              "existing_version": previous.get("existing_version"),
              "clauses": [c.id for c in chosen.selected],
              "omitted": chosen.omitted, "decisions": chosen.decisions}
    state.write(args.run, "selection", pinned)
    warnings = []
    batches = state.read(args.run, "coverage-batches")
    if batches and any(batch.get("clause_ids") != pinned["clauses"] for batch in batches):
        warnings.append(("policy start", "the clauses have changed since the existing policy was "
                                         "read: run `policy coverage-input` and the coverage steps "
                                         "again before `policy findings`"))
    return {"scope": chosen.scope, "tier": chosen.tier, "clauses": len(chosen.selected),
            "omitted": len(chosen.omitted), "decisions": len(chosen.decisions),
            "_warnings": warnings}


def _digest(text):
    return sha256_bytes(str(text).replace("\r\n", "\n").encode("utf-8"))


def _prepared_input(run_dir, task_id, name):
    """The text `task prepare` last gave a task as input `name` (its copy in the
    task's work folder), or None when the task has not been prepared. A task's
    accepted output always belongs to the input it was last prepared with:
    `task prepare` clears the folder before writing either."""
    path = os.path.join(run_dir, "tasks", task_id, "inputs", f"{name}.txt")
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def _keyed(results):
    """Collected results as {input file: entry}. Anything else -- nothing
    collected yet, or a list left by an older version of this command -- is
    treated as nothing collected."""
    return dict(results) if isinstance(results, dict) else {}


def _collect(run_dir, task_id, input_names, listing_name, results_name, what):
    """Store the accepted output of `task_id` against the listed input file it
    was prepared from -- found by the content of its prepared inputs
    (`input_names`, in order), which the listing's `sha256` was computed from
    -- replacing whatever was collected for that file before, so collecting
    the same batch twice changes nothing. Returns (entry from the listing,
    all results)."""
    data = state.accepted(run_dir, task_id)
    if data is None:
        raise ToolkitError((task_id, f"has no accepted output yet: run `task accept` for this "
                                     f"{what} first"))
    listing = state.require(run_dir, listing_name)
    texts = [_prepared_input(run_dir, task_id, name) for name in input_names]
    digest = _digest("\x00".join(texts)) if None not in texts else None
    entry = next((item for item in listing if item.get("sha256") == digest), None)
    if entry is None:
        raise ToolkitError((task_id, f"its accepted output is for a {what} the latest input step "
                                     "no longer lists: prepare the task again with one of the "
                                     "files that step returned, then accept and collect it"))
    results = _keyed(state.read(run_dir, results_name))
    # Everything the listing says about this input except the clause list it
    # repeats for every batch, then the accepted output itself.
    results[entry["file"]] = dict({key: value for key, value in entry.items()
                                   if key not in ("file", "clause_ids")}, result=data)
    state.write(run_dir, results_name, results)
    return entry, results


def _keep_listed(run_dir, results_name, listing):
    """Drop collected results for input files the new listing no longer holds,
    or whose text has changed; keep the rest, so re-running an input step on
    the same documents costs nothing to collect again."""
    wanted = {item["file"]: item["sha256"] for item in listing}
    results = _keyed(state.read(run_dir, results_name))
    kept = {path: entry for path, entry in results.items()
            if wanted.get(path) is not None and entry.get("sha256") == wanted[path]}
    state.write(run_dir, results_name, kept)
    return kept


def cmd_coverage_input(args):
    """Writes the coverage task's two declared inputs (see
    `assets/tasks/policy-review/coverage/task.json`) as separate files, since
    `task prepare` keys an input purely off its declared name:

    - each batch's own policy text, to `<run>/policy/coverage/batch-N.md`
      (1-indexed) -- this feeds `task prepare`'s `policy_batch` input.
    - the clause list -- identical for every batch, `coverage.plan_batches`
      embeds the same rendered text in each one -- written once, to
      `<run>/policy/coverage/clause-list.md`, rather than folded into every
      batch file. This feeds `task prepare`'s `clause_list` input.

    Result shape: `{"batches": int, "files": [<batch-N.md path>, ...],
    "clause_list": <clause-list.md path>}`, `files` in batch order. Task 17's
    skill runs, once per entry in `files`:
    `task prepare --task .../coverage --input policy_batch=<files[i]> --input clause_list=<clause_list>`.

    The listing is kept in run state (`coverage-batches.json`: each batch's
    `file`, `batch` number, `clause_ids` and the `sha256` of its text and the
    clause list together). Results already collected for a batch listed again
    unchanged are kept; every other collected result, and the previous
    merge, are cleared -- a corrected document is read afresh.
    """
    saved, chosen = _selection_object(args.run, _profile(args.run))
    text = _readable_text(args.doc)
    batches = coverage.plan_batches(text, chosen.selected)
    # batches[0]["clause_list"] rather than a fresh coverage.render_clause_list()
    # call: identical content (plan_batches computes it once and copies it into
    # every batch dict), so this is the same list, not just an equivalent one.
    clause_list_path = state.write_input(args.run, os.path.join("coverage", "clause-list.md"),
                                         batches[0]["clause_list"])
    files, listing = [], []
    for index, batch in enumerate(batches, 1):
        name = os.path.join("coverage", "batch-%d.md" % index)
        files.append(state.write_input(args.run, name, batch["policy_text"]))
        listing.append({"file": files[-1], "batch": index, "of": len(batches),
                        "clause_ids": batch["clause_ids"],
                        "sha256": _digest(batch["policy_text"] + "\x00" + batch["clause_list"])})
    state.write(args.run, "coverage-batches", listing)
    _keep_listed(args.run, "coverage-results", listing)
    state.remove(args.run, "coverage")
    saved["existing_policy"] = True
    # M8: the absolute path, not whatever the caller passed in -- a relative
    # path stored as given silently resolves against a different folder if
    # `policy build` is ever run from somewhere else, and falls back to the
    # standard style without saying why.
    saved["style_source"] = os.path.abspath(args.doc) if args.doc.lower().endswith(".docx") else None
    # Names the company's policy in a contradiction's second citation.
    saved["policy_document"] = os.path.basename(args.doc)
    # The version their own document states, if it says so explicitly
    # (policy_version.read_version), read from the same extracted text
    # rather than opening the document again -- kept beside style_source so
    # `policy build` can work out the redraft's version without reopening it.
    saved["existing_version"] = policy_version.read_version(text)
    state.write(args.run, "selection", saved)
    return {"batches": len(files), "files": files, "clause_list": clause_list_path}


def cmd_coverage_collect(args):
    """Records the accepted coverage output against the batch it was prepared
    from; collecting a batch again replaces its earlier result.

    Result shape: `{"collected": int (batches collected so far), "batch": int,
    "of": int}`."""
    entry, results = _collect(args.run, "policy-review.coverage", ["policy_batch", "clause_list"],
                              "coverage-batches", "coverage-results", "batch")
    return {"collected": len(results), "batch": entry["batch"], "of": entry["of"]}


def cmd_coverage_merge(args):
    saved, chosen = _selection_object(args.run, _profile(args.run))
    listing = state.require(args.run, "coverage-batches")
    if any(batch.get("clause_ids") != saved["clauses"] for batch in listing):
        raise ToolkitError(("coverage", "the clauses have changed since `policy coverage-input` "
                                        "read the policy: run it again, then the coverage steps"))
    collected = _keyed(state.read(args.run, "coverage-results"))
    results = [collected[batch["file"]]["result"] for batch in listing if batch["file"] in collected]
    if not results:
        raise ToolkitError(("coverage", "nothing collected: run `policy coverage-collect` "
                                        "after accepting each batch"))
    merged = coverage.merge(results, chosen.selected)
    warnings = merged.pop("_warnings", [])
    state.write(args.run, "coverage", merged)
    return {"clauses": len(merged), "_warnings": warnings}


def cmd_obligations_input(args):
    """Splits each facility document into parts that fit the obligations
    task's input cap -- at headings first, then at line boundaries, never
    inside a line (`coverage.split_text`) -- and writes each part to
    `<run>/policy/obligations/facility-D-part-P.md` (both 1-indexed). Every
    part feeds the task's single declared input, `facility` (see
    `assets/tasks/policy-review/obligations/task.json`), so an agreement of
    any length is read in full.

    Result shape: `{"documents": int, "batches": int, "files": [<part path>,
    ...], "parts": [{"file", "document", "part", "of"}, ...]}` -- `files` and
    `parts` in `--docs` order, then part order; `document` is the file name
    the part came from. The skill runs, once per entry in `files`, in order:
    `task prepare --task .../obligations --input facility=<files[i]>`, then
    `task accept` and `policy obligations-collect` before the next one -- the
    obligations task id is reused for every part, so only one part's output
    can be in flight at a time.

    The listing is kept in run state (`obligations-parts.json`, each entry
    also carrying `document_index` and the part's `sha256`). Results already
    collected for a part that is listed again unchanged are kept; any other
    collected result, and the previous merge, are cleared.
    """
    # Read every document before writing any, so one unreadable document leaves no
    # half-written set of facility files behind.
    texts = [_readable_text(path) for path in args.docs]
    folder = os.path.join(state.policy_dir(args.run), "obligations")
    parts = []
    for index, (path, text) in enumerate(zip(args.docs, texts), 1):
        pieces = coverage.split_text(text, obligations.MAX_PART_TOKENS)
        for number, piece in enumerate(pieces, 1):
            name = os.path.join("obligations", "facility-%d-part-%d.md" % (index, number))
            parts.append({"file": state.write_input(args.run, name, piece),
                          "document": os.path.basename(path), "document_index": index,
                          "part": number, "of": len(pieces), "sha256": _digest(piece)})
    listed = {os.path.normcase(os.path.abspath(item["file"])) for item in parts}
    for leftover in os.listdir(folder):
        stale = os.path.join(folder, leftover)
        if (leftover.startswith("facility-") and leftover.endswith(".md")
                and os.path.normcase(os.path.abspath(stale)) not in listed):
            os.remove(stale)
    state.write(args.run, "obligations-parts", parts)
    _keep_listed(args.run, "obligations-results", parts)
    for name in ("obligations", "contradictions", "constraints"):
        state.remove(args.run, name)
    # Whether the covenants were checked, for the gap report: listed but not yet
    # merged until `obligations-merge` says otherwise.
    documents = []
    for item in parts:
        if not documents or documents[-1]["index"] != item["document_index"]:
            documents.append({"document": item["document"], "index": item["document_index"],
                              "parts": item["of"]})
    state.write(args.run, "obligations-status", {"merged": False, "documents": documents})
    return {"documents": len(texts), "batches": len(parts), "files": [item["file"] for item in parts],
            "parts": [{"file": item["file"], "document": item["document"], "part": item["part"],
                       "of": item["of"]} for item in parts]}


def cmd_obligations_collect(args):
    """Records the accepted obligations output against the part it was
    prepared from; collecting a part again replaces its earlier result.

    Result shape: `{"collected": int (parts collected so far), "document":
    <file name>, "part": int, "of": int, "facility": <name this part gives,
    or "">}`."""
    entry, results = _collect(args.run, "policy-review.obligations", ["facility"],
                              "obligations-parts", "obligations-results", "document part")
    return {"collected": len(results), "document": entry["document"], "part": entry["part"],
            "of": entry["of"], "facility": results[entry["file"]]["result"].get("facility_name", "")}


def _facility_check(run_dir, saved, chosen):
    """Compare the merged facility obligations with the company's own policy
    (see cfo.policy.obligations.compare), and write the result to run state:
    `contradictions.json` for `policy findings`, `constraints.json` -- each
    facility requirement beside the board decision it bears on -- for
    `policy build`. Run by `obligations-merge` and again by `findings`, so the
    comparison always uses the latest coverage. Returns (result, warnings)."""
    facilities = state.read(run_dir, "obligations")
    existing = bool(saved.get("existing_policy", False))
    coverage_result = state.read(run_dir, "coverage", {}) or {}
    warnings = []
    if facilities is None:
        result = {"contradictions": [], "constraints": [], "warnings": []}
    else:
        if existing and not coverage_result:
            warnings.append(("coverage", "the existing policy's coverage has not been merged, so "
                                         "the facility agreements were compared with the redraft "
                                         "only; run `policy coverage-merge`, then `policy "
                                         "findings`, to compare them with the company's policy"))
        result = obligations.compare(facilities, chosen, coverage_result, existing,
                                     saved.get("policy_document") or "")
    state.write(run_dir, "contradictions", result["contradictions"])
    state.write(run_dir, "constraints", result["constraints"])
    return result, warnings + list(result["warnings"])


def cmd_obligations_merge(args):
    """Merges the collected parts into one facility per document
    (`obligations.merge_parts`: lists in part order, de-duplicated on quote
    and source), writes them to `obligations.json`, and compares them with the
    company's policy (`_facility_check`).

    Result shape: `{"documents": int, "obligations": int (facilities merged),
    "unread": [{"document", "parts": [int, ...], "of": int}, ...],
    "contradictions": int, "constraints": int}`, with a warning for every
    document part that was not collected -- one the skill marked failed, or
    never ran -- since its obligations were not compared with anything."""
    saved, chosen = _selection_object(args.run, _profile(args.run))
    listing = state.require(args.run, "obligations-parts")
    results = _keyed(state.read(args.run, "obligations-results"))
    facilities, documents = obligations.merge_parts(listing, results)
    state.write(args.run, "obligations", facilities)
    # The gap report reads this to say which agreements were checked, and which
    # could not be read in full (a part the skill marked failed is never collected).
    state.write(args.run, "obligations-status", {"merged": True, "documents": documents})
    result, warnings = _facility_check(args.run, saved, chosen)
    unread = [{"document": doc["document"], "parts": doc["unread"], "of": doc["parts"]}
              for doc in documents if doc["unread"]]
    for item in unread:
        where = ("none of its %d parts was" % item["of"] if len(item["parts"]) == item["of"]
                 else "part%s %s of %d %s" % ("s" if len(item["parts"]) > 1 else "",
                                              ", ".join(str(n) for n in item["parts"]), item["of"],
                                              "were" if len(item["parts"]) > 1 else "was"))
        warnings.append((item["document"], f"{where} collected, so the obligations in it were not "
                                           "compared with the policy"))
    return {"documents": len(documents), "obligations": len(facilities), "unread": unread,
            "contradictions": len(result["contradictions"]),
            "constraints": len(result["constraints"]), "_warnings": warnings}


def cmd_findings(args):
    profile = _profile(args.run)
    saved, chosen = _selection_object(args.run, profile)
    coverage_result = state.read(args.run, "coverage", {}) or {}
    if coverage_result and set(coverage_result) != set(saved["clauses"]):
        raise ToolkitError(("coverage", "it was merged for a different set of clauses from the "
                                        "ones `policy start` now selects: run `policy "
                                        "coverage-input` and the coverage steps again"))
    gaps = findings_module.parameter_gaps(chosen, coverage_result,
                                          saved.get("existing_policy", False))
    facility, warnings = _facility_check(args.run, saved, chosen)
    contradictions = facility["contradictions"]
    if saved.get("existing_policy") and not coverage_result:
        warnings.append(("coverage", "the existing policy was read but its coverage was never "
                                     "merged, so nothing in it was compared: run `policy "
                                     "coverage-merge`, then `policy findings` again"))
    status = state.read(args.run, "obligations-status")
    if status is not None and not status.get("merged"):
        warnings.append(("obligations", "the facility agreements were listed but never merged, so "
                                        "no covenant was compared with the policy: run `policy "
                                        "obligations-merge`, then `policy findings` again"))
    try:
        found = findings_module.build(chosen, coverage_result, contradictions, gaps)
    except ValueError as exc:
        # findings.build raises ValueError for a coverage status outside
        # findings.COVERAGE; a command never lets that become a traceback.
        raise ToolkitError(("coverage", str(exc)))
    rows = [dataclasses.asdict(f) for f in found]
    # `findings-base` is what the scripts found, before any reviewer or the critic.
    # Every panel step starts from it, so running `panel-merge` twice, or preparing
    # the critic after a merge, never adds the reviewers' findings a second time or
    # shifts the ids the critic's verdicts are keyed by. `findings` is the latest
    # result, which `panel-merge` overwrites and `build` reads.
    state.write(args.run, "findings-base", rows)
    state.write(args.run, "findings", rows)
    return {"findings": len(found), "counts": findings_module.counts(found),
            "constraints": len(facility["constraints"]), "_warnings": warnings}


def _base_findings(run_dir):
    return [findings_module.Finding(**row) for row in state.require(run_dir, "findings-base")]


def _collect_reviews(run_dir):
    reviews = {}
    for viewpoint in panel.VIEWPOINTS:
        data = state.accepted(run_dir, "policy-review.review-%s" % viewpoint)
        if data is not None:
            reviews[viewpoint] = data
    return reviews


def cmd_panel_input(args):
    profile = _profile(args.run)
    saved, chosen = _selection_object(args.run, profile)
    found = _base_findings(args.run)
    folder = os.path.join(state.policy_dir(args.run), "panel")
    os.makedirs(folder, exist_ok=True)
    if args.viewpoint == "critic":
        # panel's calling-order contract: merge_reviews() must run before
        # critic_input(), and critic_input() needs the *merged* findings --
        # a reviewer-added finding has no id until merge_reviews() assigns
        # one, and the critic keys its verdicts by those ids.
        reviews = _collect_reviews(args.run)
        merged = panel.merge_reviews(found, reviews, chosen)
        data = panel.critic_input(merged, reviews)
    elif args.viewpoint in panel.VIEWPOINTS:
        data = panel.reviewer_input(chosen, found, profile,
                                    state.read(args.run, "obligations", []) or [],
                                    existing_policy=bool(saved.get("existing_policy", False)))
    else:
        raise ToolkitError(("--viewpoint",
                            f"must be one of {', '.join(panel.VIEWPOINTS)} or critic"))
    path = os.path.join(folder, f"{args.viewpoint}.json")
    # Compact, not indented (I1): this file is itself a model task's input, so
    # its size counts against that task's token cap -- unlike every other
    # state file this module writes, which stays indented for a person to read.
    write_json_compact_atomic(path, data)
    return {"input": path, "viewpoint": args.viewpoint}


def cmd_panel_merge(args):
    profile = _profile(args.run)
    saved, chosen = _selection_object(args.run, profile)
    found = _base_findings(args.run)
    reviews = _collect_reviews(args.run)
    state.write(args.run, "reviews", reviews)
    merged = panel.merge_reviews(found, reviews, chosen)
    critic = state.accepted(args.run, "policy-review.critic")
    verdicts = {row["id"]: row for row in (critic or {}).get("verdicts", []) if row.get("id")}
    # I6: the critic's input carries the id every added finding was assigned, so
    # a verdict naming an id no finding here has means the critic mis-keyed it
    # (or named a finding from a stale critic input) -- worth a warning, not a
    # silent no-op.
    known_ids = {finding.id for finding in merged}
    unmatched_verdicts = sorted(vid for vid in verdicts if vid not in known_ids)
    merged = panel.apply_critic(merged, verdicts)
    state.write(args.run, "findings", [dataclasses.asdict(f) for f in merged])

    # Missing reviews are not an error -- a user may legitimately stop early,
    # and a quick run skips auditor, NED and the critic by design (their task
    # definitions only declare "full" among their depths) -- but merging
    # silently would ship findings that look fully reviewed when they are
    # not. Warn by name for every viewpoint (and the critic) this run's depth
    # expects but did not get.
    depth = load_run(args.run).get("depth")
    expected_viewpoints = panel.VIEWPOINTS if depth == "full" else panel.VIEWPOINTS[:1]
    warnings = [(f"policy-review.review-{viewpoint}",
                f"no {viewpoint} review was collected; the findings went out without it")
               for viewpoint in expected_viewpoints if viewpoint not in reviews]
    if depth == "full" and critic is None:
        warnings.append(("policy-review.critic",
                         "no critic verdict was collected; the findings went out without it"))
    warnings += [("policy-review.critic",
                 f"the critic's verdict for '{vid}' matches no finding; it was ignored")
                for vid in unmatched_verdicts]
    # M3: at basic depth (or whenever the critic did not weigh in on a
    # particular dispute) a reviewer's dispute was read from their review
    # file and then never used anywhere -- not shown to a critic, not
    # recorded, not mentioned. A finding a reviewer disputed still stands
    # unless a critic actually drops it, but the reader is told so rather
    # than it happening silently.
    for viewpoint, review in reviews.items():
        for dispute in review.get("disputed", []) or []:
            fid = dispute.get("id")
            if fid and fid not in verdicts:
                warnings.append((f"policy-review.review-{viewpoint}",
                                 f"the {viewpoint} disputed finding '{fid}' but no critic weighed "
                                 "in on it; the finding stands as found"))
    return {"findings": len(merged), "counts": findings_module.counts(merged),
            "reviewers": sorted(reviews), "_warnings": warnings}


def _with_constraints(chosen, constraints):
    """`chosen` with each board decision carrying the facility requirements
    that bear on it (`constraints`, from `constraints.json`), for the
    decisions table in the redraft and the board decisions document. A
    constraint on a clause with no board parameter at all (`parameter_id ==
    ""`) never matches a decision's own id -- no decision's is ever empty --
    so it is naturally left off here; `cmd_build` carries it to the reader
    separately, in `meta["clause_constraints"]`."""
    by_decision = {}
    for item in constraints:
        by_decision.setdefault((item.get("clause_id"), item.get("parameter_id")), []).append(item)
    decisions = [dict(decision, constraints=by_decision.get((decision["clause_id"], decision["id"]), []))
                 for decision in chosen.decisions]
    return dataclasses.replace(chosen, decisions=decisions)


def cmd_build(args):
    # Imported here, not at module level (see the note at the top of this
    # file): these four pull in python-docx and openpyxl. render_policy is
    # imported before style_profile deliberately: it does a plain `from docx
    # import Document`, so a missing python-docx fails with
    # ModuleNotFoundError.name == "docx" (matching cfo.preflight.REQUIRED)
    # -- style_profile's own `from docx.enum.dml import ...` would be the
    # first docx import instead, and fail with .name == "docx.enum", which
    # toolkit.py's _missing_library_error would not recognise.
    from cfo.policy import extras, render_policy, render_report
    from cfo.documents import style_profile

    profile = _profile(args.run)
    saved, chosen = _selection_object(args.run, profile)
    constraints = state.read(args.run, "constraints", []) or []
    chosen = _with_constraints(chosen, constraints)
    rows = state.require(args.run, "findings")
    found = [findings_module.Finding(**row) for row in rows]
    names = [name.strip() for name in (args.outputs or "gap,policy").split(",") if name.strip()]
    unknown = [n for n in names if n not in CORE_OUTPUTS + extras.EXTRAS]
    if unknown:
        raise ToolkitError([(n, f"is not one of {', '.join(CORE_OUTPUTS + extras.EXTRAS)}")
                            for n in unknown])

    out_dir = os.path.join(args.run, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    run = load_run(args.run)
    # Their document's next minor, marked draft, or "1.0 draft" when nothing
    # was found or no policy was supplied at all (policy_version, read by
    # `policy coverage-input` and pinned beside style_source) -- the one
    # source both the title block (_meta) and every file name below (name_for)
    # render from, so a run's documents can never disagree with each other.
    draft_version = policy_version.next_draft_version(saved.get("existing_version"))
    name_for = _output_namer(run, draft_version)
    meta = _meta(run, saved, profile, draft_version)
    meta["existing_policy"] = saved.get("existing_policy", False)
    # A constraint on a clause with no board parameter of its own
    # (cfo.policy.obligations.Context.clause_constraint: `parameter_id` is
    # always "") never matches a decision's id below -- select.py never
    # gives a decision an empty one -- so `_with_constraints` above already
    # leaves it off every decision's own `constraints`; it reaches the gap
    # report and the board decisions document through this key instead.
    # render_policy.py never reads it: the redraft's decisions table stays
    # keyed by board decision, as before.
    meta["clause_constraints"] = [c for c in constraints if not c.get("parameter_id")]
    # M1/M5: drives the "outside Ireland, the UK or the EU" note -- the
    # regulatory clauses' framing assumes one of those -- in both the gap
    # report and, since final gate review item 5, the redrafted policy
    # itself (render_policy.policy_markdown reads it the same way).
    meta["country"] = get_path(profile, "company.country")
    style, warnings, style_name = None, [], "standard"
    if args.style == "match":
        if saved.get("style_source"):
            style, style_warnings = style_profile.read_style(saved["style_source"])
            warnings += style_warnings
            style_name = "matched" if style else "standard"
        else:
            warnings.append(("--style", "no .docx was supplied to `policy coverage-input`; "
                                        "the standard style was used"))

    files = {}
    if "policy" in names:
        doc_name = "treasury-policy" if saved["scope"] == "treasury" else "hedging-policy"
        out_path = os.path.join(out_dir, name_for(doc_name, ".docx"))
        result = render_policy.build_policy(chosen, out_path, meta, style=style)
        files["policy"] = result["document"]
        warnings += result.get("_warnings", [])
    if "gap" in names:
        # A copy, not a mutation of the shared `meta`: the gap report's own
        # title is the policy's title plus "gap report" (a ruling from Task
        # 11's fix round 1 -- the two documents used to open with the same
        # title), and `meta` above is also passed to render_policy.build_policy
        # and extras.build_extras, neither of which should see this override.
        gap_meta = dict(meta, title=f"{meta['title']} gap report",
                        covenant_checks=state.read(args.run, "obligations-status"))
        out_path = os.path.join(out_dir, name_for("gap-report", ".docx"))
        result = render_report.build_report(chosen, found, out_path, gap_meta)
        files["gap"] = result["document"]
        warnings += result.get("_warnings", [])
    wanted = [n for n in names if n in extras.EXTRAS]
    if wanted:
        # Every extra takes the same <slug>-<document>-v<version>-<date> shape
        # as the two core documents, keeping FILENAMES's own base name and
        # extension (a workbook's, unlike a Word document's) -- only the sheet
        # name inside hedging-policy-tab.xlsx, "Hedging Policy", is untouched;
        # Hedj's import reads that, not the file name.
        filenames = {extra_name: name_for(*os.path.splitext(extras.FILENAMES[extra_name]))
                     for extra_name in wanted}
        built = extras.build_extras(wanted, {"selection": chosen, "found": found,
                                             "profile": profile, "meta": meta,
                                             "out_dir": out_dir, "brand": None,
                                             "filenames": filenames})
        for name, result in built.items():
            files[name] = result.get("document") or result.get("workbook")
            warnings += result.get("_warnings", [])
    return {"files": files, "style": style_name, "_warnings": warnings}


COMMANDS = {"recommend": cmd_recommend, "start": cmd_start, "coverage-input": cmd_coverage_input,
            "coverage-collect": cmd_coverage_collect, "coverage-merge": cmd_coverage_merge,
            "obligations-input": cmd_obligations_input,
            "obligations-collect": cmd_obligations_collect,
            "obligations-merge": cmd_obligations_merge, "findings": cmd_findings,
            "panel-input": cmd_panel_input, "panel-merge": cmd_panel_merge, "build": cmd_build}


def dispatch(args):
    return COMMANDS[args.policy_command](args)
