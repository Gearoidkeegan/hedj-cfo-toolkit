"""The redraft's version: what a company's existing policy states, and the
next minor version -- always marked draft -- that the redraft becomes.

Deliberately small and its own unit: `read_version` never opens a file
itself (the caller already has the extracted text -- `policy
coverage-input` reads the document once, for coverage, and this reuses that
same text rather than opening it again), and `next_draft_version` never
touches a document at all.
"""
import re

# An explicit version label -- "Version 2.1", "Version: 2.1", "v2.1",
# "V2.1", "Rev 2.1", "Revision 2.1" -- case-insensitive, with the label and
# the number allowed to sit in adjacent table cells (`| Version | 2.1 |`,
# as a document-control table renders once extracted: see
# cfo.extract.sink.render_md) as well as in running prose. Never a bare
# number, a date, a clause number or a year on its own -- requiring one of
# these labels immediately before the digits is what tells the two apart.
_LABEL = r"(?:version|revision|rev|v)"
_SEP = r"[\s|:]*"
_NUMBER = r"\d+(?:\.\d+)*"
# A number immediately followed by a file extension (".docx", ".pdf", ...)
# is the document's own file name, not a version it states -- a policy
# named "Treasury Policy v2.docx" must not read back as "their version is
# 2". Rejected by a negative lookahead rather than left to chance.
_NOT_A_FILE_EXTENSION = r"(?!\.[a-zA-Z]{2,4}\b)"
_VERSION_RE = re.compile(rf"\b{_LABEL}{_SEP}({_NUMBER}){_NOT_A_FILE_EXTENSION}\b", re.IGNORECASE)


def read_version(text):
    """The version `text` states explicitly, or `None` when it does not.

    Accepts `2`, `2.1` and `2.1.3` after the label -- `next_draft_version`
    ignores anything past the second component, so a longer number is still
    read rather than rejected. Never guesses: no label, no version --
    returning `None` here, and falling back to "1.0 draft", is correct and
    safe; inventing a lineage for a board's policy is not."""
    match = _VERSION_RE.search(text or "")
    return match.group(1) if match else None


def next_draft_version(existing):
    """The redraft's version: `existing`'s next minor, marked draft.

    `2.1` -> `2.2 draft`, `2` -> `2.1 draft`, `2.1.3` -> `2.2 draft` (only
    the first two components count -- `read_version` may return a longer
    number, but the third component and beyond never change what the
    redraft's own version becomes). `existing=None` -- no version found in
    the company's document, or no document supplied at all -- is `1.0
    draft`, never invented as anything else."""
    if existing is None:
        return "1.0 draft"
    parts = str(existing).split(".")
    major = int(parts[0])
    minor = int(parts[1]) if len(parts) > 1 else 0
    return f"{major}.{minor + 1} draft"
