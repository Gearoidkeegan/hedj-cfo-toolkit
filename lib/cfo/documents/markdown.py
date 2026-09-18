"""A small Markdown subset for the Word builder.

parse_md and runs are ported from VC Review's build_deliverables.py (MIT, same
author, commit 5c5ca37), extended with nested lists, links, images, page breaks
and front matter.
"""
import re

PAGEBREAK = "<!-- pagebreak -->"
INLINE = re.compile(r"(\*\*.+?\*\*|\[[^\]]+\]\([^)\s]+\)|\*[^*]+?\*|`[^`]+?`)", re.S)
_LINK = re.compile(r"^\[([^\]]+)\]\(([^)\s]+)\)$")
_IMAGE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)\)$")
_LIST = re.compile(r"^(\s*)(?:[-*]|(\d+)\.)\s+(.*)$")
_BLOCK_START = re.compile(r"^\s*(#{1,6}\s|[-*]\s|\d+\.\s|\||>|!\[)")


def parse_front_matter(text):
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta = {}
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return meta, "\n".join(lines[i + 1:])
        key, sep, value = line.partition(":")
        if sep and key.strip():
            value = value.strip()
            if value.lower() in ("true", "false"):
                value = value.lower() == "true"
            elif len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            meta[key.strip()] = value
    return {}, text


def runs(text):
    out = []
    for part in INLINE.split(text):
        if not part:
            continue
        link = _LINK.match(part)
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            out.append((part[2:-2], True, False, False, None))
        elif link:
            out.append((link.group(1), False, False, False, link.group(2)))
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            out.append((part[1:-1], False, False, True, None))
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            out.append((part[1:-1], False, True, False, None))
        else:
            out.append((part, False, False, False, None))
    return out or [(text, False, False, False, None)]


def _is_rule(line):
    return set(line) <= {"-", "*"} and len(line) >= 3


def parse_md(md):
    blocks, lines, i = [], md.replace("\r\n", "\n").split("\n"), 0
    while i < len(lines):
        raw = lines[i].rstrip()
        line = raw.strip()
        if not line:
            i += 1
            continue
        if line == PAGEBREAK:
            blocks.append(("pagebreak",))
            i += 1
            continue
        if _is_rule(line):
            blocks.append(("hr",))
            i += 1
            continue
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            blocks.append(("h", len(match.group(1)), match.group(2).strip()))
            i += 1
            continue
        match = _IMAGE.match(line)
        if match:
            blocks.append(("image", match.group(1), match.group(2)))
            i += 1
            continue
        if line.startswith("|") and "|" in line[1:]:
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(set(c) <= {"-", ":", " "} and c for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                blocks.append(("table", rows))
            continue
        if line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                buf.append(lines[i].lstrip()[1:].strip())
                i += 1
            blocks.append(("quote", " ".join(buf).strip()))
            continue
        match = _LIST.match(raw)
        if match:
            depth = 1 if len(match.group(1).replace("\t", "    ")) >= 2 else 0
            blocks.append(("li", match.group(3).strip(), bool(match.group(2)), depth))
            i += 1
            continue
        buf = [line]
        i += 1
        while (i < len(lines) and lines[i].strip() and lines[i].strip() != PAGEBREAK
               and not _BLOCK_START.match(lines[i]) and not _is_rule(lines[i].strip())):
            buf.append(lines[i].strip())
            i += 1
        blocks.append(("p", " ".join(buf)))
    return blocks
