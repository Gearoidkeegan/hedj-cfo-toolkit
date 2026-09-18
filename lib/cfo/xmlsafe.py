"""Remove characters that XML 1.0, and so .docx, .pptx and .xlsx files, cannot store."""
import re

# XML 1.0 allows tab, newline, carriage return, U+0020-U+D7FF, U+E000-U+FFFD and
# U+10000-U+10FFFF. Everything else (other control characters, lone surrogates,
# U+FFFE, U+FFFF) is removed.
INVALID = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def clean(text):
    """Return (text without characters XML cannot store, number removed)."""
    return INVALID.subn("", text)


def clean_tree(value):
    """Clean every string in nested dicts and lists, keys included, without changing the input.

    Returns (cleaned copy, number of characters removed)."""
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, list):
        out, total = [], 0
        for item in value:
            item, n = clean_tree(item)
            out.append(item)
            total += n
        return out, total
    if isinstance(value, dict):
        out, total = {}, 0
        for key, item in value.items():
            key, k = clean_tree(key)
            item, n = clean_tree(item)
            out[key] = item
            total += k + n
        return out, total
    return value, 0
