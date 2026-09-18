"""Column letters and formula placeholders for the workbook builder."""
import dataclasses
import re

_PLACEHOLDER = re.compile(
    r"(?:'((?:[^']|''){1,62})'!|([A-Za-z0-9_]{1,31})!)?\{([a-z0-9_]+)(:col)?\}"
)


def col_letter(index):
    letters = ""
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def quote_sheet(name):
    return "'" + name.replace("'", "''") + "'!"


def write_literal(cell, value):
    """Store a value that must never be read as a formula (openpyxl treats any string starting with = as one)."""
    cell.value = value
    if isinstance(value, str) and value.startswith("="):
        cell.data_type = "s"
    return cell


@dataclasses.dataclass
class SheetLayout:
    name: str
    columns: dict
    first_row: int = 2
    last_row: int = 1


def resolve_formula(formula, sheet, row, layouts):
    def replace(match):
        quoted, bare, key, whole_column = match.groups()
        target = (quoted.replace("''", "'") if quoted else None) or bare or sheet
        layout = layouts.get(target)
        if layout is None:
            raise ValueError(f"unknown sheet '{target}' in formula")
        if key not in layout.columns:
            raise ValueError(f"unknown column '{key}' on sheet '{target}'")
        letter = layout.columns[key]
        prefix = quote_sheet(target) if (quoted or bare) else ""
        if whole_column:
            last = max(layout.last_row, layout.first_row)
            return f"{prefix}${letter}${layout.first_row}:${letter}${last}"
        if row is None or target != sheet:
            raise ValueError("{" + key + "} needs a row: use {" + key + ":col} here")
        return f"{prefix}{letter}{row}"

    return _PLACEHOLDER.sub(replace, formula)
