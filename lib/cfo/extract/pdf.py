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
            emitted = 0
            for block in data["blocks"]:
                if block.get("type") != 0:
                    continue
                lines = []
                for line in block["lines"]:
                    kept = []
                    for s in line["spans"]:
                        txt = s["text"]
                        if not txt.strip():
                            kept.append(txt)
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
                        else:
                            kept.append(txt)
                    joined = "".join(kept).strip()
                    if joined:
                        lines.append(joined)
                if lines:
                    sink.block("paragraph", "\n".join(lines), {"page": page_no})
                    emitted += 1
            if ocr_layer:
                sink.scanned(page_no)
            if emitted == 0:
                sink.vision(page_no)
        meta["fonts"] = sorted(fonts)[:15]
        sink.meta(**meta)
        sink.check("full")
    finally:
        doc.close()
