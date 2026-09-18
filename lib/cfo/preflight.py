"""Checks that Python and the required libraries are present, and prints the
exact install command when they are not."""
import importlib
import os
import sys
from importlib import metadata

MAX_CWD_LENGTH = 100

MIN_PYTHON = (3, 11)
# (import name, distribution, lowest tested inclusive, upper bound exclusive, pinned install)
REQUIRED = [
    ("openpyxl", "openpyxl", (3, 1), (3, 2), "openpyxl==3.1.5"),
    ("docx", "python-docx", (1, 2), (1, 3), "python-docx==1.2.0"),
    ("pptx", "python-pptx", (1, 0), (1, 1), "python-pptx==1.0.2"),
]


def _importable(name):
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def _version_tuple(text):
    parts = []
    for piece in str(text).split(".")[:2]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def check():
    python = ".".join(str(v) for v in sys.version_info[:3])
    installer = "python" if sys.platform == "win32" else "python3"
    missing, pins, warnings = [], [], []
    if sys.version_info[:2] < MIN_PYTHON:
        missing.append("python>=3.11")
    for module, dist, low, high, pin in REQUIRED:
        if not _importable(module):
            missing.append(dist)
            pins.append(pin)
            continue
        try:
            version = _version_tuple(metadata.version(dist))
        except metadata.PackageNotFoundError:
            continue
        if not low <= version < high:
            shown = ".".join(str(v) for v in version)
            warnings.append((dist, f"version {shown} is outside the tested range; tested with {pin}"))
    if os.name == "nt":
        cwd = os.path.abspath(os.getcwd())
        if len(cwd) > MAX_CWD_LENGTH:
            warnings.append(("path", f"the working folder is {len(cwd)} characters long; Windows "
                                     "can fail on long paths, so consider moving the project to a "
                                     "shorter folder such as C:\\cfo"))
    return {
        "python": python,
        "ok": not missing,
        "missing": missing,
        "pdf_support": _importable("pymupdf") or _importable("fitz"),
        "install": f"{installer} -m pip install {' '.join(pins)}" if pins else None,
        "install_pdf": f"{installer} -m pip install pymupdf",
        "_warnings": warnings,
    }
