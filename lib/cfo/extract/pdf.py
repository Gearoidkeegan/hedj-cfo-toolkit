"""PDF handler (PyMuPDF): visible text by block, hidden-text checks, scanned pages.

Ported from VC Review (MIT, same author) at commit 5c5ca37.
"""
import math
import os
import sys

try:
    import pymupdf
except ImportError:
    try:
        import fitz as pymupdf
    except ImportError:
        pymupdf = None

from cfo.extract.colour import MIN_ALPHA, TINY_PT, invisible_on
from cfo.extract.sink import MissingDependency


def pdf_background_hides(samples, stride, n, width, height, box, colour_int, zoom):
    text = ((colour_int >> 16) & 255, (colour_int >> 8) & 255, colour_int & 255)
    x0, y0 = max(int(box.x0 * zoom), 0), max(int(box.y0 * zoom), 0)
    x1, y1 = min(int(box.x1 * zoom), width), min(int(box.y1 * zoom), height)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return False
    xs = range(x0, x1, max(1, (x1 - x0) // 60))
    ys = range(y0, y1, max(1, (y1 - y0) // 10))
    pts = [tuple(samples[y * stride + x * n: y * stride + x * n + 3]) for y in ys for x in xs]
    if not pts:
        return False
    median = tuple(sorted(p[i] for p in pts)[len(pts) // 2] for i in range(3))
    uniform = all(math.sqrt(sum((a - b) ** 2 for a, b in zip(p, median))) < 24 for p in pts)
    return uniform and invisible_on(text, median)

def _install_pdf():
    return ("python" if sys.platform == "win32" else "python3") + " -m pip install pymupdf"


# D6 (2026-09-22, .superpowers/sdd/2026-09-22-payments-staged-output/
# defect-d6-brief.md): PyMuPDF's own block/line grouping in
# `page.get_text("dict")` is *column* order for a table -- every cell (or
# column) of a table is its own block, so reading blocks in the order
# PyMuPDF hands them back reads a whole label column, then a whole value
# column, with no guarantee the two even stay adjacent (a real invoice's own
# block order is closer to its content-stream order than either row- or
# column-major reading order). A label and the value beside it can end up
# many blocks apart, or in the wrong relative order altogether -- see the
# brief for a real reproduction. The fix below ignores PyMuPDF's block/line
# grouping for visible text entirely and instead reconstructs *visual rows*
# from `page.get_text("words")`, which already carries each word's own
# `(x0, y0, x1, y1)` -- grouping by y-centre and sorting by x within a row is
# what a human reading the page actually does, and is what reunites a label
# with its value regardless of which block either one happened to land in.
#
# Row tolerance is derived from each *word's own* height rather than a fixed
# point value or a page-wide average: a superscript, subscript or a raised
# table cell is meaningfully smaller than the running text around it, so
# sizing the tolerance off the word's own glyph height keeps it out of a
# neighbouring row it only half-overlaps, without needing a document-wide
# guess at "the" font size (real invoices mix sizes -- a heading, a table,
# a footer -- on one page).
_ROW_TOL_FACTOR = 0.3   # fraction of a word's own height it may drift in y and still be "this row"
_ROW_TOL_MIN = 0.75     # points -- a floor so a very small glyph doesn't get a near-zero tolerance

# A word-to-word gap within a row bigger than this multiple of the row's own
# (taller) word height is treated as a column gutter rather than ordinary
# inter-word spacing: two words on the same typed line sit a few points
# apart (a fraction of the line height), while a table's column gap or a
# multi-column page's gutter is several line-heights wide. Preserving that
# gap as several literal spaces -- rather than collapsing it to one, which
# would read as a single sentence -- is the brief's own suggested fix for
# "a two-column layout must not silently weld into one sentence"; it costs
# nothing on a normal label/value pair, where `[ \t]+` in every downstream
# regex (see cfo.payments.extract) already treats one space and several the
# same way.
_COLUMN_GAP_FACTOR = 2.5
_COLUMN_GAP_SEPARATOR = "    "


def _row_tolerance(height):
    return max(height * _ROW_TOL_FACTOR, _ROW_TOL_MIN)


def _word_hidden(word_rect, hidden_boxes):
    """True when `word_rect`'s centre point falls inside any of
    `hidden_boxes` -- the bounding boxes of spans this page's own
    hidden-text pass (in `handle_pdf`) already decided were invisible,
    near-transparent, tiny, off the page or coloured like their background.
    A word is a subset of the span it came from, so its centre landing
    inside that span's box is enough; this never re-runs the colour/alpha
    checks, only reuses their result so hidden text -- already excluded from
    `sink.hidden`'s point of view -- never reappears in the row-reconstructed
    visible text either."""
    cx, cy = (word_rect.x0 + word_rect.x1) / 2, (word_rect.y0 + word_rect.y1) / 2
    return any(hb.contains(pymupdf.Point(cx, cy)) for hb in hidden_boxes)


def _words_to_rows(words):
    """`words` (PyMuPDF `page.get_text("words")` tuples, already filtered to
    visible ones) grouped into visual rows by y-centre and, within each row,
    sorted left to right -- see the module-level comment above this
    function's constants for why. Returns each row's reconstructed text,
    top to bottom.

    Clustering is a single left-to-right pass over words already sorted by
    y-centre: a new row starts whenever a word's y-centre is further from
    the current row's own than either its own or the row's tolerance allows.
    That is safe to run sequentially (rather than checking every row already
    seen) only because the input is sorted by y first -- a row, once closed,
    is never revisited."""
    ordered = sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    rows = []
    for x0, y0, x1, y1, text, *_rest in ordered:
        yc = (y0 + y1) / 2
        tol = _row_tolerance(y1 - y0)
        if rows and abs(yc - rows[-1]["yc"]) <= max(tol, rows[-1]["tol"]):
            rows[-1]["items"].append((x0, x1, text, y1 - y0))
        else:
            rows.append({"yc": yc, "tol": tol, "items": [(x0, x1, text, y1 - y0)]})
    out = []
    for row in rows:
        items = sorted(row["items"], key=lambda it: it[0])
        pieces = [items[0][2]]
        for (px0, px1, _ptxt, pheight), (x0, x1, text, height) in zip(items, items[1:]):
            gap = x0 - px1
            threshold = max(pheight, height) * _COLUMN_GAP_FACTOR
            pieces.append(_COLUMN_GAP_SEPARATOR if gap > threshold else " ")
            pieces.append(text)
        out.append("".join(pieces))
    return out


def handle_pdf(path, rel, sink):
    if pymupdf is None:
        raise MissingDependency(f"PDF support needs PyMuPDF: run {_install_pdf()}")
    doc = pymupdf.open(path)
    try:
        info = doc.metadata or {}
        meta = {"bytes": os.path.getsize(path), "pages": doc.page_count,
                **{k: v for k, v in info.items() if v and k in
                   ("producer", "creator", "author", "title", "creationDate", "modDate", "format")}}
        fonts = set()
        zoom = 2
        for number in range(doc.page_count):
            page = doc[number]
            page_no = number + 1
            rect = page.rect
            if number < 3:
                fonts.update(f[3] for f in page.get_fonts())
            data = page.get_text("dict")
            spans = [s for b in data["blocks"] if b.get("type") == 0
                     for line in b["lines"] for s in line["spans"] if s["text"].strip()]
            image_area = 0.0
            for img in page.get_image_info():
                image_area += abs(pymupdf.Rect(img["bbox"]) & rect)
            invisible = [s for s in spans if s.get("alpha", 255) == 0]
            ocr_layer = bool(spans) and len(invisible) / len(spans) > 0.8 and \
                image_area / max(abs(rect), 1) > 0.5
            pix = samples = None
            loc = f"page {page_no}"
            # Pass 1 (unchanged from before this fix): walk PyMuPDF's own
            # block/line/span structure purely to find hidden text -- this
            # is still the only path `sink.hidden` and the colour/alpha
            # checks run through, exactly as before D6. What changes is
            # what happens to the *visible* spans: instead of building each
            # block's own text here (which is what used to make a table's
            # column order the extracted reading order), this pass now only
            # remembers where the hidden ones are, in `hidden_boxes`, so
            # pass 2 can leave their words out of the row reconstruction.
            hidden_boxes = []
            for block in data["blocks"]:
                if block.get("type") != 0:
                    continue
                for line in block["lines"]:
                    for s in line["spans"]:
                        txt = s["text"]
                        if not txt.strip():
                            continue
                        box = pymupdf.Rect(s["bbox"])
                        alpha = s.get("alpha", 255) / 255
                        tech = None
                        if not ocr_layer:
                            if alpha == 0:
                                tech = "invisible text"
                            elif alpha < MIN_ALPHA:
                                tech = "near-transparent text"
                            elif s["size"] < TINY_PT:
                                tech = "tiny text"
                            elif not box.intersects(rect):
                                tech = "off the page"
                            else:
                                if pix is None:
                                    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                                    samples = pix.samples
                                if pdf_background_hides(samples, pix.stride, pix.n, pix.width,
                                                        pix.height, box, s["color"], zoom):
                                    tech = "coloured like its background"
                        if tech:
                            sink.hidden(rel, loc, tech, txt)
                            hidden_boxes.append(box)
            # Pass 2 (D6's fix): `page.get_text("words")` gives every word's
            # own position, independent of which PyMuPDF block or line it
            # was grouped into -- `_words_to_rows` uses that to read the
            # page by visual row instead of by block, so a label and its
            # value end up on the same output line even when they came from
            # different blocks (see the module comment above). A word whose
            # centre falls inside a box pass 1 already flagged hidden is
            # left out here -- the same text pass 1 already excluded from
            # the emitted block before this fix, just decided the same way
            # (by span, not word) and applied at word granularity now.
            words = [w for w in page.get_text("words")
                     if w[4].strip() and not _word_hidden(pymupdf.Rect(w[:4]), hidden_boxes)]
            rows = _words_to_rows(words)
            emitted = 0
            if rows:
                sink.block("paragraph", "\n".join(rows), {"page": page_no})
                emitted = 1
            if ocr_layer:
                sink.scanned(page_no)
            if emitted == 0:
                sink.vision(page_no)
        meta["fonts"] = sorted(fonts)[:15]
        sink.meta(**meta)
        sink.check("full")
    finally:
        doc.close()
