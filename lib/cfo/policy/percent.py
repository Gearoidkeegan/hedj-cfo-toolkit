"""A board decision's typical value, read as a percentage or a percentage range.

One grammar, shared by the Hedj hedging policy tab (`cfo.policy.extras`) and the
facility comparison (`cfo.policy.obligations`), so the two can never disagree about
what "50-70%" means. It imports nothing heavy, so the obligations step stays usable
without python-docx or openpyxl.
"""
import re

_EN_DASH = chr(0x2013)
# Two numbers may be separated by a hyphen, an en dash (Word substitutes one for the
# other, so both mean the same range) or the word "to", each with optional surrounding
# whitespace. An em dash, a repeated letter that merely looks like "to" ("70oo90%"),
# "at least 70%", "n/a" and an empty string are deliberately not read: a value the
# parser cannot read must never become a guessed number.
_RANGE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*%?\s*(?:(?:-|" + _EN_DASH +
                    r"|to)\s*(\d+(?:\.\d+)?)\s*%?)?\s*$")


def parse_range(text):
    """("70-90%") -> (70, 90); ("50%") -> (50, 50); anything else -> None.

    A range whose top is below its bottom, or above 100, is not a percentage range
    and returns None too."""
    match = _RANGE.match(str(text or ""))
    if not match:
        return None
    low = float(match.group(1))
    high = float(match.group(2)) if match.group(2) else low
    if high < low or high > 100:
        return None
    return int(low) if low.is_integer() else low, int(high) if high.is_integer() else high
