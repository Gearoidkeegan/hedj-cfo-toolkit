"""The question bank: which questions apply to a tool, in what order, and how
answers are checked and saved to the profile."""
import json
import re

from cfo.console import ToolkitError
from cfo.paths import asset
from cfo.profile import conditions, store
from cfo.profile.schema import FIELDS, coerce, get_path

TYPES = ("choice", "multi", "select", "bool", "number", "text", "currency_list", "text_list", "rank")
MAX_HEADER = 12
SKIP_SOURCES = ("answered", "extracted", "derived")


def _check_options(q, where, problems):
    options = q.get("options", [])
    if q.get("type") in ("choice", "multi") and not 2 <= len(options) <= 4:
        problems.append((where, "choice and multi questions need 2 to 4 options"))
    if q.get("type") in ("select", "rank") and len(options) < 2:
        problems.append((where, "select and rank questions need options"))
    if not all(isinstance(o, dict) and "label" in o and "value" in o for o in options):
        problems.append((where, "each option needs a label and a value"))
        return
    if q.get("field") not in FIELDS or not options:
        return
    try:
        if q["type"] == "rank":
            coerce(q["field"], [o["value"] for o in options])
        else:
            for option in options:
                coerce(q["field"], [option["value"]] if q["type"] == "multi" else option["value"])
    except ValueError as exc:
        problems.append((where, f"option value does not fit the field: {exc}"))


def load_bank(path=None):
    with open(path or asset("questions", "core.json"), encoding="utf-8") as fh:
        bank = json.load(fh)
    problems, seen = [], set()
    for i, q in enumerate(bank.get("questions", [])):
        qid = q.get("id") if isinstance(q, dict) else None
        if not qid or qid in seen:
            problems.append((f"questions[{i}]", "needs a unique id"))
            continue
        seen.add(qid)
        field = q.get("field")
        if field not in FIELDS or FIELDS[field][0] == "derived":
            problems.append((qid, f"unknown or calculated field '{field}'"))
        if q.get("type") not in TYPES:
            problems.append((qid, f"type must be one of {', '.join(TYPES)}"))
        if not 1 <= len(q.get("header", "")) <= MAX_HEADER:
            problems.append((qid, f"header must be 1 to {MAX_HEADER} characters"))
        if not q.get("text"):
            problems.append((qid, "needs text"))
        if not isinstance(q.get("tools"), list) or not q["tools"]:
            problems.append((qid, "tools must list tool names, or *"))
        if not isinstance(q.get("priority", 100), int):
            problems.append((qid, "priority must be a whole number"))
        _check_options(q, qid, problems)
        problems.extend((qid, m) for m in conditions.validate_condition(q.get("ask_if", "always"), FIELDS))
    if problems:
        raise ToolkitError(problems)
    return bank


def to_prompt(q):
    prompt = {"id": q["id"], "field": q["field"], "type": q["type"], "header": q["header"],
              "question": q["text"], "why": q.get("why", "")}
    if q["type"] in ("choice", "multi"):
        prompt.update(dialog=True, multiSelect=q["type"] == "multi",
                      options=[{"label": o["label"], "description": o.get("description", "")} for o in q["options"]])
    elif q["type"] == "bool":
        prompt.update(dialog=True, multiSelect=False,
                      options=[{"label": "Yes", "description": ""}, {"label": "No", "description": ""}])
    else:
        prompt["dialog"] = False
        if q.get("options"):
            prompt["options"] = [{"label": o["label"], "number": n} for n, o in enumerate(q["options"], 1)]
    return prompt


def _pending(bank, profile, tool):
    out = []
    for index, q in enumerate(bank["questions"]):
        if "*" not in q["tools"] and tool not in q["tools"]:
            continue
        if get_path(profile, q["field"]) is not None and \
                profile.get("_provenance", {}).get(q["field"], {}).get("source") in SKIP_SOURCES:
            continue
        if not conditions.evaluate(q.get("ask_if", "always"), profile):
            continue
        out.append((q.get("priority", 100), index, q))
    return [q for _, _, q in sorted(out, key=lambda item: item[:2])]


def next_questions(bank, profile, tool, max_n=4):
    return [to_prompt(q) for q in _pending(bank, profile, tool)[:max_n]]


def _option_value(q, answer):
    text = str(answer).strip().lower()
    for number, option in enumerate(q.get("options", []), 1):
        if answer == option["value"] or text in (str(option["label"]).lower(),
                                                  str(option["value"]).lower(), str(number)):
            return option["value"]
    return None


def _mapped(q, answer):
    value = _option_value(q, answer)
    return answer if value is None else value


def apply_answers(bank, profile, tool, answers, today=None):
    by_id = {q["id"]: q for q in bank["questions"]}
    problems = []
    for qid, answer in answers.items():
        q = by_id.get(qid)
        if q is None:
            problems.append((qid, "unknown question"))
            continue
        if q["type"] in ("choice", "select"):
            raw = _mapped(q, answer)
        elif q["type"] in ("multi", "rank"):
            if isinstance(answer, list):
                items = answer
            else:
                text = str(answer)
                # "Risk Transfer, Derivatives" splits on commas; "5 1 3 2" on spaces
                items = re.split(r"[,;]", text) if re.search(r"[,;]", text) else text.split()
            raw = [_mapped(q, str(item).strip()) for item in items if str(item).strip()]
        else:
            raw = answer
        try:
            store.set_value(profile, q["field"], raw, "answered", tool, today=today)
        except ValueError as exc:
            problems.append((qid, str(exc)))
    store.recompute_derived(profile, tool, today=today)
    return problems


def pending_count(bank, profile, tool):
    return len(_pending(bank, profile, tool))
