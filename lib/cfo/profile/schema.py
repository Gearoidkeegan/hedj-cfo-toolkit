"""Company profile fields (schema version 1) and value checks."""
import re

from cfo.numbers import parse_number
from cfo.profile import risk_appetite as ra

SCHEMA_VERSION = 1
FRAMEWORKS = ("IFRS", "FRS102", "USGAAP", "other")
OWNERSHIP = ("founder", "family", "pe", "vc", "public", "subsidiary", "other")
ACCESS = ("broad", "limited", "none")
ROLE_FLAGS = ("initiates_payments", "approves_payments", "executes_trades")
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
# For *_eur fields: a currency symbol other than €, or a three-letter code
# other than EUR, next to the amount (M9).
_OTHER_CCY_SYMBOL = re.compile(r"[£$]")
_CCY_CODE = re.compile(r"(?<![A-Za-z])[A-Za-z]{3}(?![A-Za-z])")

FIELDS = {
    "company.name": ("text",), "company.country": ("country",), "company.sector": ("text",),
    "company.reporting_currency": ("currency",), "company.reporting_framework": ("enum", FRAMEWORKS),
    "company.listed": ("bool",), "company.ownership": ("enum", OWNERSHIP),
    "size.turnover_eur": ("number",), "size.balance_sheet_eur": ("number",),
    "size.own_funds_eur": ("number",), "size.employees": ("int",), "size.mifid_professional": ("derived",),
    "structure.entities": ("int",), "structure.countries": ("country_list",),
    "structure.intercompany_flows": ("bool",),
    "exposures.currencies": ("currency_list",), "exposures.fx_inflows": ("flows",),
    "exposures.fx_outflows": ("flows",), "exposures.commodities": ("text_list",),
    "exposures.floating_rate_debt": ("bool",), "exposures.contingent_exposure": ("bool",),
    "debt.has_debt": ("bool",), "debt.facility_count": ("int",), "debt.lender_count": ("int",),
    "debt.secured": ("bool",), "debt.has_covenants": ("bool",),
    "cash.typical_balance_eur": ("number",), "cash.bank_count": ("int",),
    "cash.holds_investments": ("bool",), "cash.rated_counterparty_access": ("enum", ACCESS),
    "team.finance_headcount": ("int",), "team.treasury_dedicated": ("bool",),
    "team.board_approves_policy": ("bool",), "team.roles": ("roles",),
    "risk_inputs.exposurePctTurnover": ("enum", tuple(ra.TURNOVER_SCORES)),
    "risk_inputs.contingentRisk": ("bool",),
    "risk_inputs.teamSize": ("enum", tuple(ra.TEAM_SCORES)),
    "risk_inputs.forecastAccuracy": ("enum", (1, 2, 3, 4, 5)),
    "risk_inputs.timingAccuracy": ("enum", tuple(ra.TIMING_SCORES)),
    "risk_inputs.clarityHorizon": ("enum", tuple(ra.CLARITY_SCORES)),
    "risk_inputs.respondentPosition": ("enum", tuple(ra.SENIORITY_SCORES)),
    "risk_inputs.fxStrategies": ("enum_list", ra.FX_STRATEGIES),
    "risk_inputs.commodityStrategies": ("enum_list", ra.FX_STRATEGIES),
    "risk_inputs.irStrategies": ("enum_list", ra.FX_STRATEGIES),
    "risk_inputs.fxProducts": ("text_list",),
    "risk_inputs.strategyPriorities": ("rank", ra.PRIORITIES),
    "risk_appetite": ("derived",),
}


def get_path(profile, path):
    node = profile
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _other_currency_mentioned(text):
    if _OTHER_CCY_SYMBOL.search(text):
        return True
    return any(m.group(0).lower() != "eur" for m in _CCY_CODE.finditer(text))


def _eur_amount(value):
    """Parse an amount that must be EUR (M9): reject £/$ or a three-letter
    code other than EUR mentioned next to the number, then parse_number it.
    Shared by every *_eur field coerce() parses, top-level or nested (e.g.
    the flows items' annual_amount_eur)."""
    if isinstance(value, str) and _other_currency_mentioned(value):
        raise ValueError("give the amount in EUR")
    return parse_number(value)


def _code(value, pattern, what):
    text = str(value or "").strip().upper()
    if not pattern.match(text):
        raise ValueError(f"'{value}' is not a {what}")
    return text


def _items(value):
    raw = value if isinstance(value, list) else re.split(r"[,;]", str(value))
    return [str(v).strip() for v in raw if str(v).strip()]


def coerce(path, value):
    spec = FIELDS.get(path)
    if spec is None:
        raise ValueError(f"unknown profile field '{path}'")
    kind = spec[0]
    if kind == "derived":
        raise ValueError(f"'{path}' is calculated, not set")
    if kind == "text":
        if isinstance(value, (list, dict)):
            raise ValueError("needs text, not a list or object")
        text = str(value or "").strip()
        if not text:
            raise ValueError("needs some text")
        return text
    if kind == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "yes", "y", "1"):
            return True
        if text in ("false", "no", "n", "0"):
            return False
        raise ValueError(f"'{value}' is not yes or no")
    if kind in ("number", "int"):
        number = _eur_amount(value) if path.endswith("_eur") else parse_number(value)
        if kind == "int":
            if float(number) != int(number):
                raise ValueError(f"'{value}' is not a whole number")
            number = int(number)
        if number < 0:
            raise ValueError("cannot be negative")
        return number
    if kind == "country":
        return _code(value, _COUNTRY, "two-letter country code")
    if kind == "currency":
        return _code(value, _CURRENCY, "three-letter currency code")
    if kind in ("country_list", "currency_list", "text_list", "enum_list"):
        out = []
        for item in _items(value):
            if kind == "country_list":
                item = _code(item, _COUNTRY, "two-letter country code")
            elif kind == "currency_list":
                item = _code(item, _CURRENCY, "three-letter currency code")
            elif kind == "enum_list":
                match = next((o for o in spec[1] if str(item).strip().lower() == str(o).lower()), None)
                if match is None:
                    raise ValueError(f"'{item}' is not one of {', '.join(spec[1])}")
                item = match
            if item not in out:
                out.append(item)
        return out
    if kind == "enum":
        for option in spec[1]:
            if value == option or str(value).strip().lower() == str(option).lower():
                return option
        raise ValueError(f"'{value}' is not one of {', '.join(str(o) for o in spec[1])}")
    if kind == "rank":
        items = _items(value)
        if sorted(items) != sorted(spec[1]):
            raise ValueError(f"needs each of {', '.join(spec[1])} exactly once")
        return items
    if kind == "flows":
        if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
            raise ValueError("needs a list of {currency, annual_amount_eur}")
        return [{"currency": _code(v.get("currency"), _CURRENCY, "three-letter currency code"),
                 "annual_amount_eur": _eur_amount(v.get("annual_amount_eur"))} for v in value]
    if kind == "roles":
        if not isinstance(value, list):
            raise ValueError("needs a list of roles")
        roles = []
        for item in value:
            title = str(item.get("title", "")).strip() if isinstance(item, dict) else ""
            if not title:
                raise ValueError("each role needs a title")
            roles.append({"title": title, **{flag: bool(item.get(flag, False)) for flag in ROLE_FLAGS}})
        return roles
    raise ValueError(f"unsupported field kind '{kind}'")
