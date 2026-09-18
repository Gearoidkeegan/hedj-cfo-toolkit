"""Colour maths for hidden-text detection: themes, fills and contrast.

Ported from VC Review (MIT, same author), extract_materials.py at commit 5c5ca37.
"""
import math

from cfo.extract.ooxml import local, q

TINY_PT = 4.0            # text smaller than this is flagged
LOW_CONTRAST = 1.3       # below this contrast ratio text and background look alike
NEAR_DISTANCE = 60       # ...and this close in RGB (keeps yellow-on-white out)
MIN_ALPHA = 0.10         # text less opaque than this is flagged

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

SCHEME_ALIAS = {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2"}
PRESET = {"white": WHITE, "black": BLACK}
DOCX_THEME = {"dark1": "dk1", "light1": "lt1", "dark2": "dk2", "light2": "lt2",
              "text1": "dk1", "background1": "lt1", "text2": "dk2", "background2": "lt2",
              "hyperlink": "hlink", "followedHyperlink": "folHlink"}
HIGHLIGHT = {"yellow": "FFFF00", "green": "00FF00", "cyan": "00FFFF", "magenta": "FF00FF",
             "blue": "0000FF", "red": "FF0000", "darkBlue": "000080", "darkCyan": "008080",
             "darkGreen": "008000", "darkMagenta": "800080", "darkRed": "800000",
             "darkYellow": "808000", "darkGray": "808080", "lightGray": "C0C0C0",
             "black": "000000", "white": "FFFFFF"}
XL_THEME = ["lt1", "dk1", "lt2", "dk2", "accent1", "accent2", "accent3", "accent4",
            "accent5", "accent6", "hlink", "folHlink"]
XL_INDEXED = {0: BLACK, 1: WHITE, 8: BLACK, 9: WHITE, 64: BLACK, 65: WHITE}


# ------------------------------------------------------------------ colour

def hex_rgb(h):
    h = (h or "").strip().lstrip("#")
    if len(h) == 8:
        h = h[2:]
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _hsl(rgb):
    r, g, b = (v / 255 for v in rgb)
    mx, mn = max(r, g, b), min(r, g, b)
    light = (mx + mn) / 2
    if mx == mn:
        return 0.0, 0.0, light
    d = mx - mn
    sat = d / (2 - mx - mn) if light > 0.5 else d / (mx + mn)
    if mx == r:
        hue = (g - b) / d + (6 if g < b else 0)
    elif mx == g:
        hue = (b - r) / d + 2
    else:
        hue = (r - g) / d + 4
    return hue / 6, sat, light


def _rgb(hue, sat, light):
    light = max(0.0, min(1.0, light))
    if sat == 0:
        v = round(light * 255)
        return v, v, v

    def channel(p, qq, t):
        t %= 1
        if t < 1 / 6:
            return p + (qq - p) * 6 * t
        if t < 1 / 2:
            return qq
        if t < 2 / 3:
            return p + (qq - p) * (2 / 3 - t) * 6
        return p

    qq = light * (1 + sat) if light < 0.5 else light + sat - light * sat
    p = 2 * light - qq
    return tuple(round(channel(p, qq, hue + o) * 255) for o in (1 / 3, 0, -1 / 3))


def luminance(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def invisible_on(text, back):
    """True when text in this colour can't be seen against this background."""
    if not isinstance(text, tuple) or not isinstance(back, tuple):
        return False
    hi, lo = sorted((luminance(text), luminance(back)), reverse=True)
    ratio = (hi + 0.05) / (lo + 0.05)
    distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(text, back)))
    return ratio < LOW_CONTRAST and distance < NEAR_DISTANCE


def modify(rgb, el):
    """Apply DrawingML lumMod / lumOff / tint / shade, approximately."""
    hue, sat, light = _hsl(rgb)
    changed = False
    for m in el:
        tag = local(m.tag)
        try:
            val = int(m.get("val", "0")) / 100000
        except ValueError:
            continue
        if tag == "lumMod":
            light *= val
        elif tag == "lumOff":
            light += val
        elif tag == "tint":
            light = 1 - (1 - light) * val
        elif tag == "shade":
            light *= val
        else:
            continue
        changed = True
    return _rgb(hue, sat, light) if changed else rgb


def colour_of(parent, theme, clrmap):
    """(rgb, alpha) from the first colour child of a DrawingML element, or None."""
    if parent is None:
        return None
    for c in parent:
        tag = local(c.tag)
        if tag == "srgbClr":
            base = hex_rgb(c.get("val"))
        elif tag == "sysClr":
            base = hex_rgb(c.get("lastClr")) or (WHITE if c.get("val") == "window" else BLACK)
        elif tag == "schemeClr":
            name = c.get("val", "")
            name = clrmap.get(name, SCHEME_ALIAS.get(name, name))
            base = theme.get(name)
        elif tag == "prstClr":
            base = PRESET.get(c.get("val", ""))
        elif tag == "scrgbClr":
            try:
                base = tuple(round(int(c.get(k)) / 100000 * 255) for k in ("r", "g", "b"))
            except (TypeError, ValueError):
                base = None
        else:
            continue
        if base is None:
            return None
        alpha_el = c.find(q("a:alpha"))
        alpha = int(alpha_el.get("val", "100000")) / 100000 if alpha_el is not None else 1.0
        return modify(base, c), alpha
    return None


def read_theme(root):
    theme = {}
    scheme = root.find(".//" + q("a:clrScheme")) if root is not None else None
    for el in (list(scheme) if scheme is not None else []):
        for c in el:
            if local(c.tag) == "srgbClr":
                theme[local(el.tag)] = hex_rgb(c.get("val"))
            elif local(c.tag) == "sysClr":
                theme[local(el.tag)] = hex_rgb(c.get("lastClr")) or (
                    WHITE if c.get("val") == "window" else BLACK)
    theme.setdefault("lt1", WHITE)
    theme.setdefault("dk1", BLACK)
    return theme


def fill_of(sppr, style, theme, clrmap):
    """'none', 'unknown' or an RGB tuple for a DrawingML shape fill."""
    if sppr is not None:
        for c in sppr:
            tag = local(c.tag)
            if tag == "noFill":
                return "none"
            if tag == "solidFill":
                res = colour_of(c, theme, clrmap)
                if res is None:
                    return "unknown"
                return res[0] if res[1] >= 0.5 else "unknown"
            if tag in ("gradFill", "blipFill", "pattFill", "grpFill"):
                return "unknown"
    if style is not None:
        ref = style.find(q("a:fillRef"))
        if ref is not None and ref.get("idx", "0") not in ("0", ""):
            res = colour_of(ref, theme, clrmap)
            return res[0] if res else "unknown"
    return "none"
