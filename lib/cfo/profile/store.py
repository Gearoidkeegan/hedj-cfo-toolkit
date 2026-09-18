"""Company profile storage: one JSON file per company, each value with its provenance."""
import copy
import os

from cfo.console import ToolkitError
from cfo.io import read_json, today_iso, write_json_atomic
from cfo.profile import mifid, risk_appetite
from cfo.profile.schema import SCHEMA_VERSION, coerce, get_path

PROFILE_FILE = "company-profile.json"
SOURCES = ("answered", "extracted", "assumed", "derived")
__all__ = ["PROFILE_FILE", "SOURCES", "profile_file", "load_profile", "save_profile", "get_path",
           "set_path", "set_value", "delete_path", "recompute_derived", "for_task"]


def profile_file(company_dir):
    return os.path.join(company_dir, PROFILE_FILE)


def load_profile(company_dir):
    path = profile_file(company_dir)
    try:
        data = read_json(path)
    except ValueError as exc:  # json.JSONDecodeError and UnicodeDecodeError are ValueErrors
        raise ToolkitError((path, f"is not a readable profile: {exc}"))
    if data is None:
        return {"schema_version": SCHEMA_VERSION, "_provenance": {}}
    if not isinstance(data, dict):
        raise ToolkitError((path, "a profile must be a JSON object"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ToolkitError(("profile", f"schema version {data.get('schema_version')} is not supported "
                                       f"(expected {SCHEMA_VERSION})"))
    data.setdefault("_provenance", {})
    return data


def save_profile(company_dir, profile):
    write_json_atomic(profile_file(company_dir), profile)


def set_path(profile, path, value, source, tool, note=None, today=None):
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}")
    parts = path.split(".")
    node = profile
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    entry = {"source": source, "at": today or today_iso(), "tool": tool}
    if note:
        entry["note"] = note
    profile.setdefault("_provenance", {})[path] = entry


def set_value(profile, path, raw, source, tool, note=None, today=None):
    set_path(profile, path, coerce(path, raw), source, tool, note, today)


def delete_path(profile, path):
    parts = path.split(".")
    node = profile
    for part in parts[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return
    if isinstance(node, dict):
        node.pop(parts[-1], None)
    profile.get("_provenance", {}).pop(path, None)


def recompute_derived(profile, tool, today=None):
    professional = mifid.mifid_professional(profile)
    if professional is None:
        delete_path(profile, "size.mifid_professional")
    else:
        set_path(profile, "size.mifid_professional", professional, "derived", tool, today=today)
    if profile.get("risk_inputs"):
        set_path(profile, "risk_appetite", risk_appetite.compute(profile["risk_inputs"]), "derived",
                 tool, today=today)


def for_task(profile):
    data = copy.deepcopy(profile)
    data.pop("_provenance", None)
    return {key: value for key, value in data.items() if value not in ({}, [], None)}
