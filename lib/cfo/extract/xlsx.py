"""Excel handler: every non-empty cell with its value, formula and comments, dates as
ISO dates, and hidden-text checks.

Helpers ported from VC Review (MIT, same author) at commit 5c5ca37.
"""
import os
import re
import zipfile

from cfo.extract.colour import TINY_PT, WHITE, XL_INDEXED, XL_THEME, _hsl, _rgb, hex_rgb, invisible_on, read_theme
from cfo.extract.numfmt import is_date_format, serial_to_iso
from cfo.extract.ooxml import is_number, local, office_meta, q, read_xml, rels

MAX_SHEET_CELLS = 30000
KEEP_SHORT_TEXT = 40


REF_RE = re.compile(r"(?<![A-Za-z_\d])(\$?)([A-Z]{1,3})(\$?)(\d+)(?![\w(])")


def _col_num(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def _col_str(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _split_ref(ref):
    m = re.match(r"\$?([A-Z]{1,3})\$?(\d+)", ref or "")
    return (_col_num(m.group(1)), int(m.group(2))) if m else (0, 0)


def shift_formula(formula, anchor, target):
    """Rewrite a shared formula from its anchor cell to another cell."""
    (ac, ar), (tc, tr) = _split_ref(anchor), _split_ref(target)
    dc, dr = tc - ac, tr - ar

    def move(m):
        col_abs, col, row_abs, row = m.groups()
        c = _col_num(col) + (0 if col_abs else dc)
        r = int(row) + (0 if row_abs else dr)
        return m.group(0) if c < 1 or r < 1 else f"{col_abs}{_col_str(c)}{row_abs}{r}"

    parts = formula.split('"')
    return '"'.join(REF_RE.sub(move, s) if i % 2 == 0 else s for i, s in enumerate(parts))


def xl_colour(el, theme):
    if el is None or el.get("auto") in ("1", "true"):
        return None
    base = None
    if el.get("rgb"):
        base = hex_rgb(el.get("rgb"))
    elif el.get("theme") is not None:
        i = int(el.get("theme"))
        base = theme.get(XL_THEME[i]) if 0 <= i < len(XL_THEME) else None
    elif el.get("indexed") is not None:
        base = XL_INDEXED.get(int(el.get("indexed")))
    if base is None:
        return None
    tint = float(el.get("tint", "0") or 0)
    if tint:
        hue, sat, light = _hsl(base)
        light = light * (1 + tint) if tint < 0 else light * (1 - tint) + tint
        base = _rgb(hue, sat, light)
    return base


def handle_xlsx(path, rel, sink):
    z = zipfile.ZipFile(path)
    meta = {"bytes": os.path.getsize(path), **office_meta(z)}
    theme = read_theme(read_xml(z, "xl/theme/theme1.xml", sink))
    sst = read_xml(z, "xl/sharedStrings.xml", sink)
    shared = ["".join(t.text or "" for t in si.iter(q("x:t")))
              for si in (sst.findall(q("x:si")) if sst is not None else [])]
    fonts, fills, xfs, formats = [], [], [], {}
    styles = read_xml(z, "xl/styles.xml", sink)
    if styles is not None:
        for nf in styles.iter(q("x:numFmt")):
            formats[nf.get("numFmtId")] = nf.get("formatCode", "")
        group = styles.find(q("x:fonts"))
        for f in (list(group) if group is not None else []):
            sz = f.find(q("x:sz"))
            fonts.append((float(sz.get("val")) if sz is not None and sz.get("val") else None,
                          xl_colour(f.find(q("x:color")), theme)))
        group = styles.find(q("x:fills"))
        for f in (list(group) if group is not None else []):
            pf = f.find(q("x:patternFill"))
            if pf is None:
                fills.append("unknown")
            elif pf.get("patternType") in (None, "none"):
                fills.append("none")
            elif pf.get("patternType") == "solid":
                fills.append(xl_colour(pf.find(q("x:fgColor")), theme) or "unknown")
            else:
                fills.append("unknown")
        group = styles.find(q("x:cellXfs"))
        for xf in (list(group) if group is not None else []):
            xfs.append((int(xf.get("fontId", "0")), int(xf.get("fillId", "0")),
                        xf.get("numFmtId", "0")))

    workbook = read_xml(z, "xl/workbook.xml", sink)
    pr = workbook.find(q("x:workbookPr")) if workbook is not None else None
    date1904 = pr is not None and pr.get("date1904") in ("1", "true")
    wrels = rels(z, "xl/workbook.xml")
    sheets = workbook.find(q("x:sheets")) if workbook is not None else None
    errors, sheet_meta = [], []
    formula_count = uncached = 0
    for sheet in (list(sheets) if sheets is not None else []):
        name, state = sheet.get("name", "?"), sheet.get("state", "visible")
        sheet_part = wrels.get(sheet.get(q("r:id")), (None,))[0]
        before = len(sink.current["warnings"])
        root = read_xml(z, sheet_part, sink)
        sheet_meta.append({"name": name, "state": state})
        if root is None:
            if len(sink.current["warnings"]) == before:
                sink.warn(f"sheet '{name}' could not be read")
            continue
        where = f"sheet '{name}'"
        sheet_tech = "very hidden sheet" if state == "veryHidden" else \
            "hidden sheet" if state == "hidden" else None
        cell_tech = {}
        shared_formulas, emitted, date_warned = {}, 0, False
        for cell in root.iter(q("x:c")):
            ref, kind = cell.get("r", "?"), cell.get("t")
            f_el, v_el = cell.find(q("x:f")), cell.find(q("x:v"))
            formula = None
            if f_el is not None:
                if f_el.text:
                    formula = f_el.text
                    if f_el.get("t") == "shared" and f_el.get("si") is not None:
                        shared_formulas[f_el.get("si")] = (ref, f_el.text)
                elif f_el.get("t") == "shared" and f_el.get("si") in shared_formulas:
                    anchor, text = shared_formulas[f_el.get("si")]
                    formula = shift_formula(text, anchor, ref)
            value = v_el.text if v_el is not None else None
            if kind == "s" and value is not None:
                try:
                    value = shared[int(value)]
                except (ValueError, IndexError):
                    pass
            elif kind == "inlineStr":
                value = "".join(t.text or "" for t in cell.iter(q("x:t")))
            elif kind == "b" and value is not None:
                value = "TRUE" if value == "1" else "FALSE"
            elif kind == "e" and value:
                errors.append(f"{name}!{ref} {value}")
            if formula is not None:
                formula_count += 1
                uncached += value in (None, "")
            if value in (None, "") and formula is None:
                continue
            size = colour = None
            fill, fmt_id = "none", "0"
            try:
                style = int(cell.get("s", "0") or 0)
            except (TypeError, ValueError):
                style = 0
            if style < len(xfs):
                font_id, fill_id, fmt_id = xfs[style]
                if font_id < len(fonts):
                    size, colour = fonts[font_id]
                if fill_id < len(fills):
                    fill = fills[fill_id]
            fmt = formats.get(fmt_id, "")
            if kind in (None, "n") and value not in (None, "") and is_number(value) \
                    and is_date_format(fmt_id, fmt):
                try:
                    value = serial_to_iso(value, date1904)
                except (OverflowError, ValueError):
                    if not date_warned:
                        sink.warn(f"sheet '{name}': some date cells could not be "
                                  "converted and are shown as numbers")
                        date_warned = True
            back = WHITE if fill == "none" else fill
            tech = None
            if state == "veryHidden":
                tech = "very hidden sheet"
            elif state == "hidden":
                tech = "hidden sheet"
            elif value not in (None, "") and fmt.replace(" ", "") == ";;;":
                tech = "number format hides the value"
            elif invisible_on(colour, back):
                tech = "coloured like its background"
            elif size is not None and size < TINY_PT:
                tech = "tiny text"
            if tech:
                cell_tech[ref] = tech
            shown = value if value is not None else ""
            if tech:
                detail = f"{ref}: {value}" if value not in (None, "") else f"{ref}:"
                if formula:
                    detail += f" [={formula}]"
                sink.hidden(rel, where, tech, detail)
                if not is_number(value) and len(str(value)) > KEEP_SHORT_TEXT:
                    shown = "[text hidden in the workbook: see hidden-text.json]"
            if emitted < MAX_SHEET_CELLS:
                text = str(shown)
                if formula and not tech:
                    text += f" [={formula}]" + (" (no cached value)" if value in (None, "") else "")
                sink.block("cell", text, {"sheet": name, "cell": ref}, formula=formula)
            emitted += 1
        if emitted > MAX_SHEET_CELLS:
            sink.warn(f"sheet '{name}': only the first {MAX_SHEET_CELLS:,} of {emitted:,} cells were extracted")
        for target, rel_kind in rels(z, sheet_part).values():
            if rel_kind not in ("comments", "threadedComment"):
                continue
            croot = read_xml(z, target, sink)
            for el in (croot.iter() if croot is not None else []):
                if local(el.tag) in ("comment", "threadedComment"):
                    text = " ".join(x.text.strip() for x in el.iter()
                                    if local(x.tag) in ("t", "text") and x.text and x.text.strip())
                    comment_ref = el.get("ref", "?")
                    comment_tech = sheet_tech or cell_tech.get(comment_ref)
                    if comment_tech:
                        sink.hidden(rel, where, comment_tech, f"{comment_ref}: {text}")
                    else:
                        sink.block("note", text, {"sheet": name, "cell": comment_ref})

    if formula_count and uncached >= max(1, formula_count // 2):
        sink.warn("most formulas have no cached values, so their results are missing: open the "
                  "workbook in Excel, save it and extract it again")
    names = workbook.find(q("x:definedNames")) if workbook is not None else None
    meta.update(sheets=sheet_meta, formula_cells=formula_count, formulas_without_cached_values=uncached,
                error_cells=errors[:30], date1904=date1904,
                defined_names=[f"{d.get('name')} = {d.text}" for d in list(names)[:50]] if names is not None else [])
    sink.meta(**meta)
    sink.check("full")
