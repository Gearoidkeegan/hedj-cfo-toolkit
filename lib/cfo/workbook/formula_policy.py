"""Formula safety policy for the workbook builder.

Workbook cells carry live formulas with no cached values, so Excel
recalculates them whenever the file is opened. A formula that can reach
outside the workbook -- a DDE link, a UNC path, an external-workbook
reference, or a call to a function that fetches a URL, loads an image, or
talks to a registered DLL -- could leak the contents of a cell to an
outside server, or run something on the machine that opens the file. Toolkit
output goes to lenders and banks outside Hedj's control, so these patterns
are rejected outright rather than merely flagged.
"""
import re
from typing import Optional

#: Function names that must never appear as a call in a formula the toolkit
#: writes, matched case-insensitively, with or without an _xlfn./_xlws. prefix.
BAD_FUNCTIONS = ("WEBSERVICE", "FILTERXML", "IMAGE", "HYPERLINK", "RTD", "CALL",
                 "REGISTER.ID", "REGISTER", "EXEC", "INFO")

_FUNCTION_RE = re.compile(
    r"(?:_xlfn\.|_xlws\.)?(" + "|".join(re.escape(name) for name in BAD_FUNCTIONS) + r")\s*\(",
    re.IGNORECASE)

# Patterns rejected inside a quoted sheet name ('...'!): the UNC probe hides
# a backslash pair and an external-workbook bracket behind a leading quote,
# so this span is scanned even though it looks like a string literal.
_QUOTED_SHEET_BAD = (("\\\\", "a UNC path (\\\\)"), ("[", "an external workbook reference ([)"),
                     ("://", "a URL (://)"))


def _quoted_sheet_problem(segment):
    for pattern, label in _QUOTED_SHEET_BAD:
        if pattern in segment:
            return f"the quoted sheet name contains {label}, which is not allowed"
    return None


def check_formula(text) -> Optional[str]:
    """Return a readable problem message, or None if the formula is safe.

    Double-quoted string literals ("..." with "" as an escaped quote) are
    ignored while scanning: their contents are just text to Excel, never
    evaluated. A single-quoted span ('...') is a sheet-name reference, not a
    string literal, so it is still scanned for the patterns above.
    """
    text = str(text)
    i, n = 0, len(text)
    normal = []
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n:
                if text[j] == '"':
                    if j + 1 < n and text[j + 1] == '"':
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            else:
                j = n
            i = j
            continue
        if ch == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            else:
                j = n
            problem = _quoted_sheet_problem(text[i:j])
            if problem:
                return problem
            i = j
            continue
        if ch == "|":
            return "contains | which is not allowed (a DDE link)"
        if ch == "[":
            return "contains [ which is not allowed (an external workbook reference)"
        if text[i:i + 2] == "\\\\":
            return "contains \\\\ which is not allowed (a UNC path)"
        if text[i:i + 3] == "://":
            return "contains :// which is not allowed (a URL)"
        normal.append(ch)
        i += 1
    match = _FUNCTION_RE.search("".join(normal))
    if match:
        return f"calls {match.group(1).upper()}, which is not allowed in a formula"
    return None
