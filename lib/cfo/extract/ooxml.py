"""Office Open XML helpers: safe part reading, relationships and package metadata.

Ported from VC Review (MIT, same author), extract_materials.py at commit 5c5ca37.
"""
import os
import re
import zipfile
import xml.etree.ElementTree as ET
import xml.parsers.expat as expat

MAX_PART_BYTES = 150000000

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
}


def q(tag):
    prefix, name = tag.split(":")
    return "{%s}%s" % (NS[prefix], name)


def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def safe(name):
    return re.sub(r"[^\w.-]+", "_", name).strip("_") or "file"


class _DTDRefused(Exception):
    """Internal signal: a part declared a DTD (any encoding) and was refused."""


def _declares_dtd(data):
    """True if `data` declares a DTD anywhere, detected at the parser level
    (via expat, which resolves the part's real encoding) rather than by
    searching the raw bytes, which a UTF-16 (or other non-ASCII-compatible)
    encoding defeats."""
    parser = expat.ParserCreate()

    def _refuse(*_args):
        raise _DTDRefused()

    parser.StartDoctypeDeclHandler = _refuse
    parser.EntityDeclHandler = _refuse
    try:
        parser.Parse(data, True)
    except _DTDRefused:
        return True
    except expat.ExpatError:
        return False
    return False


def read_xml(z, name, sink=None):
    """Parse one package part. The files are untrusted: Office XML never needs a
    DTD, so a part declaring one (the route to entity-expansion and external
    entity tricks) is refused, whatever its encoding, as is anything
    implausibly large. When `sink` is given, a refused part is warned about
    by name."""
    if not name:
        return None
    try:
        if z.getinfo(name).file_size > MAX_PART_BYTES:
            return None
        data = z.read(name)
    except (KeyError, zipfile.BadZipFile):
        return None
    if _declares_dtd(data):
        if sink is not None:
            sink.warn(f"{name}: declares a DTD, left out")
        return None
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        return None


def rels(z, part):
    """{rId: (resolved path, relationship type)} for a package part."""
    if not part:
        return {}
    folder, base = part.rsplit("/", 1) if "/" in part else ("", part)
    root = read_xml(z, f"{folder}/_rels/{base}.rels" if folder else f"_rels/{base}.rels")
    out = {}
    for r in (list(root) if root is not None else []):
        if r.get("TargetMode") == "External":
            continue
        target = r.get("Target", "")
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            path = os.path.normpath(os.path.join(folder, target)).replace("\\", "/")
        out[r.get("Id")] = (path, r.get("Type", "").rsplit("/", 1)[-1])
    return out


def first_of(relmap, kind):
    return next((path for path, t in relmap.values() if t == kind), None)


def is_number(s):
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def read_text(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def office_meta(z):
    meta = {}
    app = read_xml(z, "docProps/app.xml")
    for key, tag in (("application", "Application"), ("app_version", "AppVersion"),
                     ("template", "Template"), ("total_edit_minutes", "TotalTime"),
                     ("company", "Company"), ("pages", "Pages"), ("words", "Words"),
                     ("slides", "Slides")):
        el = app.find(q(f"ep:{tag}")) if app is not None else None
        if el is not None and el.text:
            meta[key] = el.text
    core = read_xml(z, "docProps/core.xml")
    for key, tag in (("creator", "dc:creator"), ("last_modified_by", "cp:lastModifiedBy"),
                     ("created", "dcterms:created"), ("modified", "dcterms:modified"),
                     ("revision", "cp:revision"), ("title", "dc:title")):
        el = core.find(q(tag)) if core is not None else None
        if el is not None and el.text:
            meta[key] = el.text
    return meta


IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".emf", ".wmf")


def count_media(z, prefix):
    return sum(1 for i in z.infolist()
               if i.filename.startswith(prefix) and i.filename.lower().endswith(IMAGE_EXTENSIONS))
