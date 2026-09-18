"""PowerPoint handler: visible text, tables, charts, notes and hidden-text checks.

Ported from VC Review (MIT, same author) at commit 5c5ca37.
"""
import os
import re
import zipfile

from cfo.extract.colour import MIN_ALPHA, TINY_PT, WHITE, colour_of, fill_of, invisible_on, read_theme
from cfo.extract.ooxml import NS, count_media, first_of, local, office_meta, q, read_xml, rels


def own_background(root, theme, clrmap):
    """A part's own background: RGB, 'unknown', or None when it inherits."""
    cs = root.find(q("p:cSld"))
    bg = cs.find(q("p:bg")) if cs is not None else None
    if bg is None:
        return None
    pr = bg.find(q("p:bgPr"))
    if pr is not None:
        f = fill_of(pr, None, theme, clrmap)
        return f if isinstance(f, tuple) else "unknown"
    ref = bg.find(q("p:bgRef"))
    if ref is not None and ref.get("idx") == "1001":
        res = colour_of(ref, theme, clrmap)
        return res[0] if res else "unknown"
    return "unknown"


def _ints(el, *names):
    return [int(el.get(n, "0") or 0) for n in names]


def walk_shapes(tree, transform=(0, 0, 1, 1), hidden=False, out=None):
    """Flatten a shape tree in z-order: dicts with el, tag, box (slide EMU), hidden."""
    out = [] if out is None else out
    for el in (list(tree) if tree is not None else []):
        tag = local(el.tag)
        if tag not in ("sp", "pic", "graphicFrame", "grpSp", "cxnSp"):
            continue
        cnv = el.find(".//" + q("p:cNvPr"))
        is_hidden = hidden or (cnv is not None and cnv.get("hidden") in ("1", "true"))
        if tag == "graphicFrame":
            xfrm = el.find(q("p:xfrm"))
        else:
            pr = el.find(q("p:grpSpPr") if tag == "grpSp" else q("p:spPr"))
            xfrm = pr.find(q("a:xfrm")) if pr is not None else None
        ox, oy, sx, sy = transform
        box = None
        off = xfrm.find(q("a:off")) if xfrm is not None else None
        ext = xfrm.find(q("a:ext")) if xfrm is not None else None
        if off is not None and ext is not None:
            x, y = _ints(off, "x", "y")
            w, h = _ints(ext, "cx", "cy")
            box = (ox + sx * x, oy + sy * y, ox + sx * (x + w), oy + sy * (y + h))
        if tag == "grpSp":
            inner = transform
            ch_off = xfrm.find(q("a:chOff")) if xfrm is not None else None
            ch_ext = xfrm.find(q("a:chExt")) if xfrm is not None else None
            if off is not None and ext is not None and ch_off is not None and ch_ext is not None:
                x, y = _ints(off, "x", "y")
                w, h = _ints(ext, "cx", "cy")
                cx, cy = _ints(ch_off, "x", "y")
                cw, chh = _ints(ch_ext, "cx", "cy")
                kx, ky = (w / cw if cw else 1), (h / chh if chh else 1)
                inner = (ox + sx * (x - cx * kx), oy + sy * (y - cy * ky), sx * kx, sy * ky)
            walk_shapes(el, inner, is_hidden, out)
            continue
        out.append({"el": el, "tag": tag, "box": box, "hidden": is_hidden})
    return out


def shape_fill(shape, theme, clrmap):
    if shape["tag"] in ("pic", "graphicFrame"):
        return "unknown"
    if shape["tag"] == "cxnSp":
        return "none"
    el = shape["el"]
    return fill_of(el.find(q("p:spPr")), el.find(q("p:style")), theme, clrmap)


def overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def contains(a, b):
    return a[0] <= b[0] and a[1] <= b[1] and a[2] >= b[2] and a[3] >= b[3]


def off_page(box, width, height):
    return box[2] <= 0 or box[3] <= 0 or box[0] >= width or box[1] >= height


def background_for(i, stack, slide_bg):
    """What sits behind shape i: its own fill, a shape beneath it, or the slide."""
    shape = stack[i]
    if shape["fill"] != "none":
        return shape["fill"]
    for j in range(i - 1, -1, -1):
        other = stack[j]
        if other["box"] is None or shape["box"] is None:
            if other["fill"] != "none":
                return "unknown"
            continue
        if not overlaps(other["box"], shape["box"]):
            continue
        if other["fill"] == "none":
            continue
        if other["fill"] == "unknown" or not contains(other["box"], shape["box"]):
            return "unknown"
        return other["fill"]
    return slide_bg


def level_def(lst, lvl):
    if lst is None:
        return None
    lp = lst.find(q(f"a:lvl{lvl + 1}pPr"))
    return lp.find(q("a:defRPr")) if lp is not None else None


def placeholder_style(master, ph):
    if master is None or ph is None:
        return None
    styles = master.find(q("p:txStyles"))
    if styles is None:
        return None
    kind = ph.get("type", "obj")
    if kind in ("title", "ctrTitle"):
        return styles.find(q("p:titleStyle"))
    if kind in ("body", "subTitle", "obj"):
        return styles.find(q("p:bodyStyle"))
    return styles.find(q("p:otherStyle"))


def run_technique(rpr, ppr, lst, lvl, ph_style, back, scale, theme, clrmap):
    """Why a DrawingML text run can't be seen, or None.

    Colour is only judged when the slide itself sets it, so text inheriting its
    colour from a layout we don't fully resolve is never flagged.
    """
    explicit = [rpr, ppr.find(q("a:defRPr")) if ppr is not None else None, level_def(lst, lvl)]
    if rpr is not None:
        hl = colour_of(rpr.find(q("a:highlight")), theme, clrmap)
        if hl:
            back = hl[0]
    colour = None
    for el in explicit:
        if el is None:
            continue
        if el.find(q("a:noFill")) is not None and el.find(q("a:ln")) is None:
            return "invisible text"
        sf = el.find(q("a:solidFill"))
        if sf is not None:
            colour = colour_of(sf, theme, clrmap)
            break
    if colour is not None and colour[1] < MIN_ALPHA:
        return "near-transparent text"
    if colour is not None and invisible_on(colour[0], back):
        return "coloured like its background"
    for el in explicit + [level_def(ph_style, lvl)]:
        if el is not None and el.get("sz"):
            if int(el.get("sz")) / 100 * scale < TINY_PT:
                return "tiny text"
            break
    return None


def text_body(body, ph_style, back, theme, clrmap, sink, rel, loc, shape_tech, fonts):
    scale = 1.0
    bp = body.find(q("a:bodyPr"))
    na = bp.find(q("a:normAutofit")) if bp is not None else None
    if na is not None and na.get("fontScale"):
        scale = int(na.get("fontScale")) / 100000
    lst = body.find(q("a:lstStyle"))
    paras = []
    for p in body.findall(q("a:p")):
        ppr = p.find(q("a:pPr"))
        lvl = int(ppr.get("lvl", "0")) if ppr is not None else 0
        visible = []
        for r in p:
            tag = local(r.tag)
            if tag == "br":
                visible.append("\n")
                continue
            if tag not in ("r", "fld"):
                continue
            t = r.find(q("a:t"))
            txt = t.text if t is not None and t.text else ""
            if not txt:
                continue
            rpr = r.find(q("a:rPr"))
            latin = rpr.find(q("a:latin")) if rpr is not None else None
            if latin is not None and not latin.get("typeface", "+").startswith("+"):
                fonts[latin.get("typeface")] = fonts.get(latin.get("typeface"), 0) + 1
            tech = shape_tech or run_technique(rpr, ppr, lst, lvl, ph_style, back, scale,
                                               theme, clrmap)
            if tech:
                sink.hidden(rel, loc, tech, txt)
            else:
                visible.append(txt)
        line = "".join(visible).strip()
        if line:
            paras.append(line)
    return "\n".join(paras)


CHART_SERIES_LIMIT = 1200


def chart_text(root, sink=None, loc=None):
    if root is None:
        return ""
    c = lambda t: "{%s}%s" % (NS["c"], t)
    title_el = root.find(".//" + c("title"))
    title = " ".join(x.text for x in title_el.iter(q("a:t")) if x.text) if title_el is not None else ""
    series = []
    for ser in root.iter(c("ser")):
        tx = ser.find(c("tx"))
        name = " ".join(v.text for v in tx.iter(c("v")) if v.text) if tx is not None else ""
        cat = ser.find(c("cat"))
        val = ser.find(c("val"))
        if val is None:
            val = ser.find(c("yVal"))
        cats = [v.text for v in cat.iter(c("v"))] if cat is not None else []
        vals = [v.text for v in val.iter(c("v"))] if val is not None else []
        if cats and len(cats) == len(vals):
            pairs = ", ".join(f"{a}={b}" for a, b in zip(cats, vals))
        else:
            pairs = ", ".join(v for v in vals if v)
        full = f"{name or 'series'}: {pairs}"
        if len(full) > CHART_SERIES_LIMIT and sink is not None:
            where = f"{loc}: " if loc else ""
            sink.warn(f"{where}a chart's series text was cut at {CHART_SERIES_LIMIT:,} characters")
        series.append(full[:CHART_SERIES_LIMIT])
    if not title and not series:
        return ""
    return "Chart" + (f" '{title}'" if title else "") + ": " + " / ".join(series)


def notes_text(root):
    if root is None:
        return ""
    out = []
    cs = root.find(q("p:cSld"))
    for s in walk_shapes(cs.find(q("p:spTree")) if cs is not None else None):
        ph = s["el"].find(".//" + q("p:ph"))
        if ph is not None and ph.get("type") in ("sldImg", "sldNum", "hdr", "ftr", "dt"):
            continue
        body = s["el"].find(q("p:txBody"))
        for p in (body.findall(q("a:p")) if body is not None else []):
            line = "".join(t.text or "" for t in p.iter(q("a:t"))).strip()
            if line:
                out.append(line)
    return "\n".join(out)


def handle_pptx(path, rel, sink):
    z = zipfile.ZipFile(path)
    meta = {"bytes": os.path.getsize(path), **office_meta(z)}
    pres = read_xml(z, "ppt/presentation.xml", sink)
    size = pres.find(q("p:sldSz")) if pres is not None else None
    width = int(size.get("cx")) if size is not None else 12192000
    height = int(size.get("cy")) if size is not None else 6858000
    prels = rels(z, "ppt/presentation.xml")
    ids = pres.find(q("p:sldIdLst")) if pres is not None else None
    order = [prels[s.get(q("r:id"))][0] for s in (list(ids) if ids is not None else [])
             if s.get(q("r:id")) in prels]
    if not order:
        order = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                       key=lambda n: int(re.findall(r"\d+", n)[-1]))
    parts, fonts, layouts, hidden_slides = {}, {}, {}, []

    def part(name):
        if name not in parts:
            parts[name] = read_xml(z, name, sink)
        return parts[name]

    for n, slide_part in enumerate(order, 1):
        before = len(sink.current["warnings"])
        root = part(slide_part)
        if root is None:
            if len(sink.current["warnings"]) == before:
                sink.warn(f"slide {n} could not be read (malformed, or it declares a DTD) "
                          "and was not checked for hidden text")
            continue
        srels = rels(z, slide_part)
        layout_part = first_of(srels, "slideLayout")
        layout = part(layout_part) if layout_part else None
        master_part = first_of(rels(z, layout_part), "slideMaster") if layout_part else None
        master = part(master_part) if master_part else None
        theme_part = first_of(rels(z, master_part), "theme") if master_part else None
        theme = read_theme(part(theme_part) if theme_part else None)
        cmap_el = master.find(q("p:clrMap")) if master is not None else None
        clrmap = dict(cmap_el.attrib) if cmap_el is not None else {}
        override = root.find(f"{q('p:clrMapOvr')}/{q('a:overrideClrMapping')}")
        if override is not None:
            clrmap = dict(override.attrib)

        slide_bg = own_background(root, theme, clrmap)
        for inherited in (layout, master):
            if slide_bg is None and inherited is not None:
                slide_bg = own_background(inherited, theme, clrmap)
        if slide_bg is None:
            slide_bg = theme.get(clrmap.get("bg1", "lt1"), WHITE)

        if layout is not None and layout.find(q("p:cSld")) is not None:
            layout_name = layout.find(q("p:cSld")).get("name", "")
            layouts[layout_name] = layouts.get(layout_name, 0) + 1

        backdrop = []
        show_master = layout is None or layout.get("showMasterSp") not in ("0", "false")
        for base, include in ((master, show_master), (layout, True)):
            if base is None or not include or base.find(q("p:cSld")) is None:
                continue
            for s in walk_shapes(base.find(q("p:cSld")).find(q("p:spTree"))):
                if s["el"].find(".//" + q("p:ph")) is None:
                    s["fill"] = shape_fill(s, theme, clrmap)
                    backdrop.append(s)
        cs = root.find(q("p:cSld"))
        shapes = walk_shapes(cs.find(q("p:spTree")) if cs is not None else None)
        for s in shapes:
            s["fill"] = shape_fill(s, theme, clrmap)
        stack = backdrop + shapes

        hidden_slide = root.get("show") in ("0", "false")
        if hidden_slide:
            hidden_slides.append(n)
        loc, where, tables = f"slide {n}", {"slide": n}, 0
        for i, s in enumerate(shapes):
            el = s["el"]
            if hidden_slide:
                shape_tech = "hidden slide"
            elif s["hidden"]:
                shape_tech = "hidden shape"
            elif s["box"] is not None and off_page(s["box"], width, height):
                shape_tech = "off the page"
            else:
                shape_tech = None
            if s["tag"] == "sp":
                body = el.find(q("p:txBody"))
                if body is None:
                    continue
                ph = el.find(f"{q('p:nvSpPr')}/{q('p:nvPr')}/{q('p:ph')}")
                back = background_for(len(backdrop) + i, stack, slide_bg)
                text = text_body(body, placeholder_style(master, ph), back, theme, clrmap,
                                 sink, rel, loc, shape_tech, fonts)
                sink.block("paragraph", text, where)
            elif s["tag"] == "graphicFrame":
                data = el.find(".//" + q("a:graphicData"))
                table = data.find(q("a:tbl")) if data is not None else None
                if table is not None:
                    rows = []
                    for tr in table.findall(q("a:tr")):
                        cells = []
                        for tc in tr.findall(q("a:tc")):
                            cell_body = tc.find(q("a:txBody"))
                            cell_bg = fill_of(tc.find(q("a:tcPr")), None, theme, clrmap)
                            cell_bg = cell_bg if isinstance(cell_bg, tuple) else "unknown"
                            cells.append(text_body(cell_body, None, cell_bg, theme, clrmap, sink,
                                                   rel, loc, shape_tech, fonts)
                                         if cell_body is not None else "")
                        rows.append([c.replace("\n", " ") for c in cells])
                    if any(c.strip() for row in rows for c in row):
                        tables += 1
                        sink.block("table", "", {"slide": n, "table": tables}, rows=rows)
                chart = data.find("{%s}chart" % NS["c"]) if data is not None else None
                if chart is not None:
                    target = srels.get(chart.get(q("r:id")), (None,))[0]
                    ctext = chart_text(read_xml(z, target, sink), sink, loc)
                    if ctext and shape_tech:
                        sink.hidden(rel, loc, shape_tech, ctext)
                    elif ctext:
                        sink.block("paragraph", ctext, where)
        for target, kind in srels.values():
            if kind == "diagramData":
                droot = read_xml(z, target, sink)
                smart = " · ".join(x.text.strip() for x in droot.iter(q("a:t"))
                                   if x.text and x.text.strip()) if droot is not None else ""
                if smart and hidden_slide:
                    sink.hidden(rel, loc, "hidden slide", smart)
                elif smart:
                    sink.block("paragraph", smart, where)
        notes_part = first_of(srels, "notesSlide")
        notes = notes_text(read_xml(z, notes_part, sink)) if notes_part else ""
        if notes and hidden_slide:
            sink.hidden(rel, loc, "hidden slide", notes)
        elif notes:
            sink.block("note", notes, where)

    meta.update(slide_count=len(order), layouts=layouts, hidden_slides=hidden_slides,
                fonts=[f for f, _ in sorted(fonts.items(), key=lambda kv: -kv[1])][:10],
                images=count_media(z, "ppt/media/"))
    sink.meta(**meta)
    sink.check("full")
