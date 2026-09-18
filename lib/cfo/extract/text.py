"""Plain text, Markdown, CSV and image files."""
import csv
import io
import os
import re

MAX_TEXT_CHARS = 400000


def _decode(raw):
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _read_capped(path, sink):
    with open(path, "rb") as fh:
        text = _decode(fh.read())
    if len(text) > MAX_TEXT_CHARS:
        sink.warn(f"only the first {MAX_TEXT_CHARS:,} characters were extracted")
        text = text[:MAX_TEXT_CHARS]
    return text


def handle_text(path, rel, sink):
    text = _read_capped(path, sink)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    for n, para in enumerate(paragraphs, 1):
        sink.block("paragraph", para, {"para": n})
    sink.meta(bytes=os.path.getsize(path))
    sink.check("not applicable")


def handle_csv(path, rel, sink):
    text = _read_capped(path, sink)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [row for row in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in row)]
    sink.block("table", "", {"table": 1}, rows=rows)
    sink.meta(bytes=os.path.getsize(path), rows=len(rows),
              columns=max((len(r) for r in rows), default=0))
    sink.check("not applicable")


def handle_image(path, rel, sink):
    sink.vision(1)
    sink.meta(bytes=os.path.getsize(path))
    sink.check("not run (image)")
