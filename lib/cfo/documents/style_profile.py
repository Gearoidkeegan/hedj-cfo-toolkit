"""A style profile read from a company's own Word document.

Only typography and page setup travel: fonts, sizes, margins, page size,
heading colour and whether their headings were numbered. Never their header,
footer, logo or images -- a toolkit document must not arrive wearing someone
else's letterhead.

A missing or unreadable value is never silent: whenever this module falls
back to the standard style for a value, it says so in the returned warnings,
naming the file and, where it can tell, the reason (most often that the
value is set by the document's theme -- Word's Design > Fonts/Colours --
which the toolkit cannot resolve to a concrete font name or RGB colour).
"""
import dataclasses
import os

from docx.enum.dml import MSO_COLOR_TYPE
from docx.oxml.ns import qn
from docx.shared import Mm

MIN_PT, MAX_PT = 7.0, 16.0
MIN_MARGIN_MM, MAX_MARGIN_MM = 5.0, 60.0
MIN_PAGE_MM, MAX_PAGE_MM = 100.0, 500.0


@dataclasses.dataclass
class StyleProfile:
    body_font: object
    body_pt: object
    heading_font: object
    heading_colour: object
    margins_mm: object
    page_mm: object
    numbered_headings: object   # True/False when the source document gives a
                                # clear signal either way, None when it has no
                                # headings to judge from at all
    source: str


def _mm(value):
    return None if value is None else round(value / Mm(1), 1)


def _in_range(value, low, high):
    return value is not None and low <= value <= high


def _font_of(style):
    if style is None:
        return None
    try:
        return style.font.name or None
    except (AttributeError, KeyError):
        return None


def _is_theme_font(style):
    """True when the style's font is set via the document's theme (Word's
    Design > Fonts), which `_font_of` cannot read: the rFonts element carries
    `w:asciiTheme` or `w:hAnsiTheme` rather than a literal `w:ascii` name --
    Word writes whichever of the two the theme's Latin-text mapping uses, so a
    document that only sets `w:hAnsiTheme` must still be recognised as
    theme-driven, not reported as unreadable for no stated reason."""
    if style is None:
        return False
    try:
        rpr = style.element.rPr
        rfonts = rpr.rFonts if rpr is not None else None
    except AttributeError:
        return False
    if rfonts is None:
        return False
    return rfonts.get(qn("w:asciiTheme")) is not None or rfonts.get(qn("w:hAnsiTheme")) is not None


def _colour_of(style):
    """The style's explicit RGB colour, or None when it is set by the
    document's theme -- Word writes only a best-guess snapshot RGB for a
    theme colour, never the resolved value, per python-docx's own ColorFormat
    docs -- or not overridden at all."""
    if style is None:
        return None
    try:
        colour = style.font.color
    except AttributeError:
        return None
    if colour.type != MSO_COLOR_TYPE.RGB:
        return None
    rgb = colour.rgb
    return None if rgb is None else str(rgb)


def _is_theme_colour(style):
    if style is None:
        return False
    try:
        return style.font.color.theme_color is not None
    except AttributeError:
        return False


def _size_pt(style):
    try:
        size = style.font.size
    except (AttributeError, KeyError):
        return None
    return None if size is None else round(size.pt, 1)


def _numbering_signal(document):
    """True/False when the document's headings give a clear signal either
    way (an explicit numbering list, or heading text starting with a digit),
    None when it has no heading paragraphs to judge from at all."""
    for style_name in ("Heading 1", "Heading 2"):
        try:
            style = document.styles[style_name]
        except KeyError:
            continue
        properties = style.element.pPr
        if properties is not None and properties.numPr is not None:
            return True
    headings = [p for p in document.paragraphs if p.style.name.startswith("Heading")]
    if not headings:
        return None
    return any(p.text[:1].isdigit() for p in headings[:40])


def read_style(path):
    """(StyleProfile or None, [(where, message)])."""
    name = os.path.basename(path)
    if not path.lower().endswith(".docx"):
        return None, [(name, "style matching needs a .docx; the standard style was used")]
    if not os.path.isfile(path):
        # M8: name the path it was recorded under (now an absolute path, from
        # `policy coverage-input`), not just the bare file name -- a relative
        # path stored as given would fall back silently if `policy build`
        # ever ran from a different working folder.
        return None, [(name, f"could not be found at {path}; the standard style was used")]
    try:
        import docx as python_docx
        document = python_docx.Document(path)
    except Exception:
        return None, [(name, "could not be read for its style; the standard style was used")]

    warnings = []
    try:
        normal = document.styles["Normal"]
    except KeyError:
        return None, [(name, "has no Normal style; the standard style was used")]

    body_pt = _size_pt(normal)
    if body_pt is not None and not _in_range(body_pt, MIN_PT, MAX_PT):
        warnings.append((name, f"body text is {body_pt}pt, which is outside the usual range; "
                               "the standard size was used"))
        body_pt = None

    section = document.sections[0]
    margins = tuple(_mm(getattr(section, attr)) for attr in
                    ("left_margin", "right_margin", "top_margin", "bottom_margin"))
    if not all(_in_range(value, MIN_MARGIN_MM, MAX_MARGIN_MM) for value in margins):
        warnings.append((name, "its margins are outside the usual range; the standard margins were used"))
        margins = None

    page = (_mm(section.page_width), _mm(section.page_height))
    if not all(_in_range(value, MIN_PAGE_MM, MAX_PAGE_MM) for value in page):
        warnings.append((name, "its page size is unusual; A4 was used"))
        page = None

    try:
        heading_style = document.styles["Heading 1"]
    except KeyError:
        heading_style = None

    body_font = _font_of(normal)
    if body_font is None:
        if _is_theme_font(normal):
            warnings.append((name, "its body font is set by the document's theme, which the "
                                   "toolkit cannot read; the standard font was used"))
        else:
            warnings.append((name, "its body font could not be read; the standard font was used"))

    heading_font = _font_of(heading_style)
    if heading_font is None:
        if _is_theme_font(heading_style):
            warnings.append((name, "its heading font is set by the document's theme, which the "
                                   "toolkit cannot read; the standard font was used"))
        else:
            warnings.append((name, "its heading font could not be read; the standard font was used"))

    heading_colour = _colour_of(heading_style)
    if heading_colour is None:
        if _is_theme_colour(heading_style):
            warnings.append((name, "its heading colour is set by the document's theme, which the "
                                   "toolkit cannot read; the standard colour was used"))
        else:
            warnings.append((name, "its heading colour could not be read; the standard colour was used"))

    numbered_headings = _numbering_signal(document)
    if numbered_headings is None:
        warnings.append((name, "it has no headings to show whether they were numbered; "
                               "the standard numbering was used"))

    profile = StyleProfile(body_font=body_font, body_pt=body_pt, heading_font=heading_font,
                           heading_colour=heading_colour, margins_mm=margins, page_mm=page,
                           numbered_headings=numbered_headings, source=name)
    return profile, warnings


def as_settings(profile, template_settings):
    """Template settings with whatever the profile supplies."""
    settings = dict(template_settings)
    if profile is None:
        return settings
    if profile.margins_mm:
        settings["margins_mm"] = profile.margins_mm
    if profile.body_pt:
        settings["body_pt"] = profile.body_pt
    return settings
