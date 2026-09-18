"""Brand styles for the workbook builder."""
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from cfo.brand import hex6

RAG_LEVELS = ("red", "amber", "green")
RAG_LABELS = {"red": "Red", "amber": "Amber", "green": "Green"}
THIN = Side(style="thin", color="D1D5DB")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def solid(colour):
    value = hex6(colour)
    return PatternFill(fill_type="solid", start_color=value, end_color=value)


def header_font(brand):
    return Font(name=brand["fonts"]["body"], bold=True, color=hex6(brand["colours"]["white"]))


def header_fill(brand):
    return solid(brand["colours"]["primary"])


def header_alignment():
    return Alignment(horizontal="center", vertical="center", wrap_text=True)


def body_font(brand, **kwargs):
    return Font(name=brand["fonts"]["body"], color=hex6(brand["colours"]["ink"]), **kwargs)


def rag_fill(brand, level):
    return solid(brand["colours"][level])


def rag_font(brand, level):
    colour = brand["colours"]["ink"] if level == "amber" else brand["colours"]["white"]
    return Font(name=brand["fonts"]["body"], bold=True, color=hex6(colour))


DATE_FORMAT = "dd-mmm-yyyy"
