"""Text fitting for slides. Ported from VC Review's build_deliverables.py (MIT,
same author, commit 5c5ca37)."""

AVG_GLYPH = 0.52       # average Arial glyph width, as a fraction of the point size


def truncate(s, n, sentence=False):
    """Trim to n chars. With sentence=True, prefer the last full sentence that
    fits. Otherwise cut at the last word boundary, never inside a word."""
    s = " ".join(str(s).split())
    if len(s) <= n:
        return s
    cut = s[:n - 1]
    if sentence:
        stop = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        if stop > n * 0.5:
            return cut[:stop + 1]
    space = cut.rfind(" ")
    if space > n * 0.7:
        cut = cut[:space]
    return cut.rstrip(" ,.;:(-") + "…"


def fit_items(items, box, size=9.0, before=4.5):
    """Shorten items so they share a text box of (width, height) inches.
    Drops one point only when the longest item wouldn't fit.
    Returns (items, font size, character limit per item)."""
    width, height = box
    n = max(len(items), 1)
    options = []
    for pt in (size, size - 1):
        per_line = int(width * 72 / (pt * AVG_GLYPH) * 0.9)
        lines = int((height * 72 - n * before) / (pt * 1.2))
        options.append((pt, max(40, lines // n * per_line)))
    longest = max((len(" ".join(str(s).split())) for s in items), default=0)
    pt, limit = options[0] if longest <= options[0][1] else options[1]
    return [truncate(s, limit, sentence=True) for s in items], pt, limit
