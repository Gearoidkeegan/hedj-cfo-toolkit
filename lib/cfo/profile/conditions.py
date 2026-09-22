"""Conditions that decide when a question applies."""
from cfo.profile.schema import get_path

OPS = ("eq", "ne", "in", "gte", "lte", "exists")
_SHAPE = 'ask_if must be "always" or {"all": [...]} or {"any": [...]}'


def validate_condition(cond, known_fields=None):
    if cond == "always":
        return []
    if not isinstance(cond, dict) or len(cond) != 1 or next(iter(cond)) not in ("all", "any") \
            or not isinstance(next(iter(cond.values())), list) or not next(iter(cond.values())):
        return [_SHAPE]
    problems = []
    for i, item in enumerate(next(iter(cond.values()))):
        if not isinstance(item, dict) or "field" not in item:
            problems.append(f"condition {i} needs a field")
            continue
        if known_fields is not None and item["field"] not in known_fields:
            problems.append(f"condition {i}: unknown field '{item['field']}'")
        ops = [key for key in item if key != "field"]
        if len(ops) != 1 or ops[0] not in OPS:
            problems.append(f"condition {i} needs exactly one of {', '.join(OPS)}")
    return problems


def _present(value):
    """A field holds something: not missing, and not an empty list, set, mapping or
    string. Answering "no currencies" stores an empty list, which must read as no
    currency exposure rather than as one. A False answer is still an answer."""
    if value is None:
        return False
    if isinstance(value, (list, tuple, set, dict, str)):
        return len(value) > 0
    return True


def _test(item, profile):
    value = get_path(profile, item["field"])
    op = next(key for key in item if key != "field")
    arg = item[op]
    if op == "exists":
        return _present(value) == bool(arg)
    if value is None:
        return False
    if op == "eq":
        return value == arg
    if op == "ne":
        return value != arg
    if op == "in":
        return value in arg
    try:
        if op == "gte":
            return value >= arg
        if op == "lte":
            return value <= arg
    except TypeError:
        return False
    raise ValueError(f"unknown condition operator '{op}'")


def evaluate(cond, profile):
    problems = validate_condition(cond)
    if problems:
        raise ValueError("; ".join(problems))
    if cond == "always":
        return True
    (mode, items), = cond.items()
    results = [_test(item, profile) for item in items]
    return all(results) if mode == "all" else any(results)
