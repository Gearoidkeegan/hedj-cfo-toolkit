"""Word handler: visible text by paragraph and table, headings, clause numbers and
hidden-text checks.

Helpers ported from VC Review (MIT, same author) at commit 5c5ca37.
"""
import os
import re
import zipfile

from cfo.extract.colour import DOCX_THEME, HIGHLIGHT, TINY_PT, WHITE, _hsl, _rgb, fill_of, hex_rgb, invisible_on, read_theme
from cfo.extract.numbering import Numbering
from cfo.extract.ooxml import NS, count_media, local, office_meta, q, read_xml


def nearest(parent, el, tag):
    el = parent.get(el)
    while el is not None and el.tag != tag:
        el = parent.get(el)
    return el


def shade_of(el):
    sh = el.find(q("w:shd")) if el is not None else None
    fill = sh.get(q("w:fill")) if sh is not None else None
    return hex_rgb(fill) if fill and fill != "auto" else None


def word_colour(c, theme):
    tc = c.get(q("w:themeColor"))
    if tc:
        base = theme.get(DOCX_THEME.get(tc, tc))
        if base is None:
            return None
        hue, sat, light = _hsl(base)
        tint, shade = c.get(q("w:themeTint")), c.get(q("w:themeShade"))
        try:
            if tint:
                light = 1 - (1 - light) * int(tint, 16) / 255
            if shade:
                light = light * int(shade, 16) / 255
        except ValueError:
            pass
        return _rgb(hue, sat, light)
    val = c.get(q("w:val"), "auto")
    return None if val == "auto" else hex_rgb(val)


def style_chain(style_id, ctx):
    out, seen = [], set()
    while style_id and style_id not in seen:
        seen.add(style_id)
        out.append(ctx["rpr"].get(style_id))
        style_id = ctx["based_on"].get(style_id)
    return out


def switched_on(el):
    return el.get(q("w:val"), "true") not in ("0", "false", "off")


def docx_technique(rpr, pstyle, back, ctx):
    rstyle = rpr.find(q("w:rStyle")) if rpr is not None else None
    explicit = ([rpr] + style_chain(rstyle.get(q("w:val")) if rstyle is not None else None, ctx)
                + style_chain(pstyle, ctx))
    everything = explicit + [ctx["defaults"]]
    for el in everything:
        v = el.find(q("w:vanish")) if el is not None else None
        if v is not None:
            if switched_on(v):
                return "hidden text attribute"
            break
    if rpr is not None:
        back = shade_of(rpr) or back
        hl = rpr.find(q("w:highlight"))
        if hl is not None and HIGHLIGHT.get(hl.get(q("w:val"))):
            back = hex_rgb(HIGHLIGHT[hl.get(q("w:val"))])
    for el in explicit:
        c = el.find(q("w:color")) if el is not None else None
        if c is not None:
            if invisible_on(word_colour(c, ctx["theme"]), back):
                return "coloured like its background"
            break
    for el in everything:
        s = el.find(q("w:sz")) if el is not None else None
        if s is not None:
            try:
                if int(s.get(q("w:val"))) / 2 < TINY_PT:
                    return "tiny text"
            except (TypeError, ValueError):
                pass
            break
    return None


def paragraph_background(p, parent, page_bg, ctx):
    el = parent.get(p)
    while el is not None:
        tag = local(el.tag)
        if tag == "tc":
            cell = shade_of(el.find(q("w:tcPr")))
            if cell is not None:
                return cell
            table = nearest(parent, el, q("w:tbl"))
            style = table.find(f"{q('w:tblPr')}/{q('w:tblStyle')}") if table is not None else None
            if style is not None and style.get(q("w:val")) in ctx["shaded_tables"]:
                return "unknown"
        elif tag == "txbxContent":
            wsp = nearest(parent, el, "{%s}wsp" % NS["wps"])
            pr = wsp.find("{%s}spPr" % NS["wps"]) if wsp is not None else None
            f = fill_of(pr, None, ctx["theme"], {}) if pr is not None else "unknown"
            return f if isinstance(f, tuple) else "unknown"
        elif tag in ("pict", "object"):
            return "unknown"
        el = parent.get(el)
    return page_bg


P = q("w:p")
R = q("w:r")
TBL = q("w:tbl")
TR = q("w:tr")
TC = q("w:tc")
HEADING_RE = re.compile(r"^heading\s*([1-9])$", re.I)
PARTS = (("word/header", "header"), ("word/footer", "footer"), ("word/footnotes", "footnotes"),
         ("word/endnotes", "endnotes"), ("word/comments", "comments"))


def read_styles(z, sink=None):
    ctx = {"theme": read_theme(read_xml(z, "word/theme/theme1.xml", sink)), "rpr": {}, "based_on": {},
           "defaults": None, "shaded_tables": set(), "names": {}, "outline": {}}
    styles = read_xml(z, "word/styles.xml", sink)
    if styles is not None:
        ctx["defaults"] = styles.find(f"{q('w:docDefaults')}/{q('w:rPrDefault')}/{q('w:rPr')}")
        for s in styles.findall(q("w:style")):
            sid = s.get(q("w:styleId"))
            ctx["rpr"][sid] = s.find(q("w:rPr"))
            based = s.find(q("w:basedOn"))
            if based is not None:
                ctx["based_on"][sid] = based.get(q("w:val"))
            if s.get(q("w:type")) == "table" and s.find(".//" + q("w:shd")) is not None:
                ctx["shaded_tables"].add(sid)
            name = s.find(q("w:name"))
            if name is not None:
                ctx["names"][sid] = name.get(q("w:val"), "")
            outline = s.find(f"{q('w:pPr')}/{q('w:outlineLvl')}")
            if outline is not None and (outline.get(q("w:val")) or "").isdigit():
                ctx["outline"][sid] = int(outline.get(q("w:val")))
    return ctx, styles


def heading_level(ppr, pstyle, ctx):
    outline = ppr.find(q("w:outlineLvl")) if ppr is not None else None
    if outline is not None and (outline.get(q("w:val")) or "").isdigit():
        level = int(outline.get(q("w:val")))
        return level + 1 if level < 9 else None
    seen, sid = set(), pstyle
    while sid and sid not in seen:
        seen.add(sid)
        name = ctx["names"].get(sid, "")
        match = HEADING_RE.match(name) or HEADING_RE.match(sid)
        if match:
            return int(match.group(1))
        if name.lower() == "title":
            return 1
        if ctx["outline"].get(sid, 9) < 9:
            return ctx["outline"][sid] + 1
        sid = ctx["based_on"].get(sid)
    return None


def paragraph_text(p, parent, page_bg, ctx, sink, rel, location):
    ppr = p.find(q("w:pPr"))
    ps = ppr.find(q("w:pStyle")) if ppr is not None else None
    pstyle = ps.get(q("w:val")) if ps is not None else None
    back = shade_of(ppr) or paragraph_background(p, parent, page_bg, ctx)
    visible = []
    for r in p.iter(R):
        if nearest(parent, r, P) is not p:
            continue
        pieces = []
        for ch in r:
            tag = local(ch.tag)
            if tag == "t":
                pieces.append(ch.text or "")
            elif tag == "tab":
                pieces.append("\t")
            elif tag in ("br", "cr"):
                pieces.append("\n")
        txt = "".join(pieces)
        if not txt.strip():
            visible.append(txt)
            continue
        tech = docx_technique(r.find(q("w:rPr")), pstyle, back, ctx)
        if tech:
            sink.hidden(rel, location, tech, txt)
        else:
            visible.append(txt)
    return "".join(visible).strip(), ppr, pstyle


def _labelled(label, text):
    return f"{label} {text}" if label else text


def docx_part(root, ctx, page_bg, sink, rel, part, numbering=None):
    parent = {child: node for node in root.iter() for child in node}
    consumed = set()
    para_no = table_no = 0
    for el in root.iter():
        if el.tag == TBL and el not in consumed and nearest(parent, el, TBL) is None:
            table_no += 1
            consumed.update(el.iter(TBL))
            rows = []
            for tr in el.iter(TR):
                if nearest(parent, tr, TBL) is not el:
                    continue
                cells = []
                for tc in tr:
                    if tc.tag != TC:
                        continue
                    texts = []
                    for p in tc.iter(P):
                        consumed.add(p)
                        text, ppr, pstyle = paragraph_text(p, parent, page_bg, ctx, sink, rel,
                                                           f"{part} table {table_no}")
                        label = numbering.label(ppr, pstyle) if numbering is not None else None
                        if text:
                            texts.append(_labelled(label, text))
                    cells.append(" ".join(texts))
                rows.append(cells)
            if any(c.strip() for row in rows for c in row):
                sink.block("table", "", {"part": part, "table": table_no}, rows=rows)
        elif el.tag == P and el not in consumed:
            para_no += 1
            text, ppr, pstyle = paragraph_text(el, parent, page_bg, ctx, sink, rel,
                                               f"{part} paragraph {para_no}")
            label = numbering.label(ppr, pstyle) if numbering is not None else None
            if not text:
                continue
            loc = {"part": part, "para": para_no, "clause": label}
            level = heading_level(ppr, pstyle, ctx)
            if level:
                sink.block("heading", _labelled(label, text), loc, level=level)
            else:
                sink.block("paragraph", _labelled(label, text), loc)


def handle_docx(path, rel, sink):
    z = zipfile.ZipFile(path)
    meta = {"bytes": os.path.getsize(path), **office_meta(z)}
    ctx, styles_root = read_styles(z, sink)
    numbering = Numbering(read_xml(z, "word/numbering.xml", sink), styles_root)
    before = len(sink.current["warnings"])
    document = read_xml(z, "word/document.xml", sink)
    if document is None and len(sink.current["warnings"]) == before:
        sink.warn("word/document.xml could not be read (malformed, or it declares a DTD)")
    page_bg = WHITE
    bg = document.find(q("w:background")) if document is not None else None
    if bg is not None:
        page_bg = hex_rgb(bg.get(q("w:color"))) or WHITE
    parts = [("word/document.xml", "document")] + [
        (name, label) for name in sorted(z.namelist()) for prefix, label in PARTS
        if name.startswith(prefix) and name.endswith(".xml")]
    for name, part in parts:
        root = document if name == "word/document.xml" else read_xml(z, name, sink)
        if root is not None:
            docx_part(root, ctx, page_bg, sink, rel, part, numbering if part == "document" else None)
    meta["images"] = count_media(z, "word/media/")
    sink.meta(**meta)
    sink.check("full")
