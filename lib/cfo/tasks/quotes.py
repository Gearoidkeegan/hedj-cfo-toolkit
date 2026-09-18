"""Checks that quoted evidence really appears in the task's input."""
import re
import unicodedata

_MARKER_RE = re.compile(r"^\[[^\]\n]{1,80}\] ?", re.M)
# Map fancy quotes and dashes to their ASCII equivalents
_TRANSLATE = str.maketrans({
    '‘': "'",  # LEFT SINGLE QUOTATION MARK
    '’': "'",  # RIGHT SINGLE QUOTATION MARK
    '‚': "'",  # SINGLE LOW-9 QUOTATION MARK
    '‛': "'",  # SINGLE HIGH-REVERSED-9 QUOTATION MARK
    '′': "'",  # PRIME
    '“': '"',  # LEFT DOUBLE QUOTATION MARK
    '”': '"',  # RIGHT DOUBLE QUOTATION MARK
    '„': '"',  # DOUBLE LOW-9 QUOTATION MARK
    '‟': '"',  # DOUBLE HIGH-REVERSED-9 QUOTATION MARK
    '″': '"',  # DOUBLE PRIME
    '–': '-',  # EN DASH
    '—': '-',  # EM DASH
    '‑': '-',  # NON-BREAKING HYPHEN
    '‒': '-',  # FIGURE DASH
    '−': '-',  # MINUS SIGN
})


def strip_markers(text):
    """Remove location markers such as '[p12 §22.1(a)] ' from the start of lines."""
    return _MARKER_RE.sub("", text or "")


def normalise(text):
    text = unicodedata.normalize("NFKC", text or "").translate(_TRANSLATE)
    return " ".join(text.split()).casefold()


def values_at(data, path):
    """[(json path, value)] for a path such as 'findings[].quote'."""
    current = [("$", data)]
    for part in path.split("."):
        is_list = part.endswith("[]")
        key = part[:-2] if is_list else part
        found = []
        for where, node in current:
            if not isinstance(node, dict) or key not in node:
                continue
            value, here = node[key], f"{where}.{key}"
            if not is_list:
                found.append((here, value))
            elif isinstance(value, list):
                found.extend((f"{here}[{i}]", item) for i, item in enumerate(value))
        current = found
    return current


def check_quotes(data, checks, inputs):
    problems, haystacks = [], {}
    for check in checks:
        if check.input not in haystacks:
            haystacks[check.input] = normalise(strip_markers(inputs.get(check.input, "")))
        for where, value in values_at(data, check.path):
            if not isinstance(value, str) or not value.strip():
                continue
            if normalise(value) not in haystacks[check.input]:
                shown = value.strip()[:80]
                problems.append((where, f"quote not found in input '{check.input}': \"{shown}\""))
    return problems
