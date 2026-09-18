"""Brand settings from assets/brand/, shared by the workbook, document and deck builders."""
import copy
import json
import os

from cfo.paths import asset

DEFAULT = {
    "name": "Hedj",
    "colours": {"primary": "#1DB88D", "accent": "#FFC000", "ink": "#1F2A37", "muted": "#6B7280",
                "red": "#E5484D", "amber": "#FFC000", "green": "#1DB88D", "light": "#E8F7F2",
                "white": "#FFFFFF"},
    "fonts": {"heading": "Calibri", "body": "Calibri", "figures": "Calibri",
              "fallback": "Arial"},
    "chart_series": ["#1DB88D", "#1F2A37", "#FFC000", "#7C6AED", "#E5484D"],
    "footer_text": "Prepared with the Hedj CFO Toolkit · hedj.eu",
    "disclaimer": "",
}


def load_brand():
    brand = copy.deepcopy(DEFAULT)
    path = asset("brand", "brand.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for key, value in json.load(fh).items():
                if isinstance(value, dict):
                    brand.setdefault(key, {}).update(value)
                else:
                    brand[key] = value
    disclaimer = asset("brand", "disclaimer.md")
    if os.path.exists(disclaimer):
        with open(disclaimer, encoding="utf-8") as fh:
            brand["disclaimer"] = fh.read().strip()
    return brand


def hex6(colour):
    value = str(colour).strip().lstrip("#").upper()
    if len(value) != 6 or any(ch not in "0123456789ABCDEF" for ch in value):
        raise ValueError(f"not a hex colour: {colour}")
    return value
