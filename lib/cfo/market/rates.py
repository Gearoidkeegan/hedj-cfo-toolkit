"""ECB daily reference rates, and Decimal currency conversion through them.

This is the toolkit's first network call. `lib/cfo/` has no HTTP code
anywhere else, so the failure paths here are the point of the module, not an
afterthought: a run that needs an FX rate must never die because the ECB's
site was slow or unreachable. On any fetch problem -- a network error, the
10-second timeout, or a response this module cannot parse -- `ecb_rates`
falls back to the most recently cached date, marks the result `stale` and
warns why. It only raises when it has nothing at all to fall back to: no
network and no cache means the caller must supply a rate itself.

The ECB's *daily* feed always serves the latest business day it has, not a
day of the caller's choosing. `on` therefore only ever controls cache reuse
here: a date already cached is returned straight from disk with no fetch at
all, and a date that was never cached still fetches the daily feed's real
latest, whose actual date is reported truthfully in the result's `date`
field regardless of what `on` asked for.

That is a limit of this module, not of the source, and the distinction
matters for whoever needs it next. The ECB does publish history, as separate
feeds alongside the daily one:

    /stats/eurofxref/eurofxref-hist-90d.xml   last 90 days
    /stats/eurofxref/eurofxref-hist.xml       the full series
    /stats/eurofxref/eurofxref-sdmx.xml       SDMX-ML

None is read here because the payments tool converts a batch at today's
rate and has no use for history. A tool that does -- the cash forecast
back-testing its own accuracy, or valuing a transaction on the day it
settled -- should add a `historical_rates` reader against the 90-day feed
rather than concluding from this module that the ECB has no history.

Cross rates are computed through EUR because that is the only currency the
ECB quotes -- every published rate is "1 EUR = X <currency>" -- and every
amount is `Decimal`: two roundings of a float rate would not reverse
exactly, and money must not carry that binary error.
"""
import os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, InvalidOperation

from cfo.console import ToolkitError
from cfo.io import read_json, write_json_atomic

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
TIMEOUT_SECONDS = 10
BASE_CURRENCY = "EUR"


# The daily feed is a few kilobytes: about 30 currencies, one line each. A
# cap two orders of magnitude above that is generous for the real thing and
# still bounds what this module will read from the network.
MAX_FEED_BYTES = 1_000_000


def _fetch_daily_xml() -> bytes:
    """The one place this module touches the network, kept to a single
    function so a test can stub exactly this and nothing else.

    The read is bounded. `xml.etree.ElementTree` is the standard library's
    parser and does not resolve external entities, so the XXE class does not
    apply here, and the source is one hardcoded HTTPS URL rather than
    anything a user supplies. What remains is entity expansion from a
    response far larger than the feed can legitimately be -- which reading a
    bounded number of bytes, and failing rather than truncating, removes.
    `ecb_rates` treats that failure like any other: fall back to cache, warn,
    carry on."""
    with urllib.request.urlopen(ECB_URL, timeout=TIMEOUT_SECONDS) as response:
        payload = response.read(MAX_FEED_BYTES + 1)
    if len(payload) > MAX_FEED_BYTES:
        raise ValueError(
            f"the ECB feed returned more than {MAX_FEED_BYTES} bytes, which the "
            "daily rates feed never legitimately does")
    return payload


def _parse_daily_xml(payload):
    """(date, {currency: Decimal}) from the ECB feed's XML. Rates are read
    from the response's own attribute strings straight into `Decimal`,
    never through `float`, so a rate like 1.0712 is exact rather than the
    nearest binary approximation of it. Raises ValueError for anything this
    cannot make sense of; `ecb_rates` treats that the same as a network
    failure, since a response this toolkit cannot read is no more usable
    than one that never arrived."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError(f"the ECB response was not valid XML ({exc})") from exc
    # The feed nests the dated rates one level under a wrapping Cube, all
    # under the eurofxref namespace -- matched with the `{*}` wildcard so a
    # namespace prefix change on the ECB's side can never silently produce
    # an empty result here.
    time_cube = root.find(".//{*}Cube[@time]")
    if time_cube is None:
        raise ValueError("the ECB response had no dated rate block")
    time_text = time_cube.get("time")
    try:
        on = date.fromisoformat(time_text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"the ECB response's date ('{time_text}') was unreadable") from exc
    rates = {}
    for cube in time_cube.findall("{*}Cube"):
        currency, rate_text = cube.get("currency"), cube.get("rate")
        if not currency or not rate_text:
            continue
        try:
            rates[currency] = Decimal(rate_text)
        except InvalidOperation:
            continue
    if not rates:
        raise ValueError("the ECB response named no currency rates")
    return on, rates


def _cache_path(cache_dir, on):
    return os.path.join(cache_dir, f"{on.isoformat()}.json")


def _to_cache_record(result):
    return {**result, "rates": {ccy: str(rate) for ccy, rate in result["rates"].items()}}


def _from_cache_record(data):
    record = {**data, "rates": {ccy: Decimal(rate) for ccy, rate in data["rates"].items()}}
    record["_warnings"] = [tuple(w) for w in record.get("_warnings", [])]
    return record


def _read_cached(cache_dir, on):
    data = read_json(_cache_path(cache_dir, on))
    return None if not data else _from_cache_record(data)


def _write_cache(cache_dir, on, result):
    write_json_atomic(_cache_path(cache_dir, on), _to_cache_record(result))


def _latest_cached_date(cache_dir):
    if not cache_dir or not os.path.isdir(cache_dir):
        return None
    found = []
    for name in os.listdir(cache_dir):
        if name.endswith(".json"):
            try:
                found.append(date.fromisoformat(name[:-len(".json")]))
            except ValueError:
                continue  # not one of this module's cache files; ignore it
    return max(found) if found else None


def ecb_rates(on: date | None = None, cache_dir: str | None = None) -> dict:
    """The ECB's daily reference rates, EUR-based:
    `{"date": "2026-09-19", "base": "EUR", "rates": {"USD": Decimal(...), ...},
      "source": "ecb", "stale": False, "_warnings": [...]}`.

    `cache_dir`, when given, is where a fetched date is cached and where a
    fallback is read from on failure; without it, every call fetches (or
    fails) fresh. `on`, when it names a date already cached, is returned
    from disk with no fetch at all -- the acceptance test for this asserts
    the fetch stub's call count. Any other value of `on` is not a promise
    the ECB feed can keep (see the module docstring), so it still fetches
    the feed's actual latest and reports that date honestly.

    On a fetch or parse failure, falls back to the most recent cached date
    with `stale: True` and a warning naming that date and why. With no cache
    to fall back to, raises `ToolkitError` -- the only case where this stops
    a run, since the caller then has no rate at all and must supply one."""
    if on is not None and cache_dir:
        cached = _read_cached(cache_dir, on)
        if cached is not None:
            return cached

    try:
        payload = _fetch_daily_xml()
        fetched_on, table = _parse_daily_xml(payload)
    except Exception as exc:
        latest = _latest_cached_date(cache_dir) if cache_dir else None
        if latest is None:
            raise ToolkitError(("market.rates",
                                f"could not fetch ECB reference rates "
                                f"({type(exc).__name__}: {exc}) and no cached rates are "
                                "available; supply a rate manually")) from None
        fallback = _read_cached(cache_dir, latest)
        fallback["stale"] = True
        fallback["_warnings"] = [("market.rates",
                                  f"ECB fetch failed ({type(exc).__name__}: {exc}); using "
                                  f"cached rates for {latest.isoformat()} instead")]
        return fallback

    result = {"date": fetched_on.isoformat(), "base": BASE_CURRENCY, "rates": table,
              "source": "ecb", "stale": False, "_warnings": []}
    if cache_dir:
        _write_cache(cache_dir, fetched_on, result)
    return result


def convert(amount, from_ccy, to_ccy, rates) -> Decimal:
    """`amount` (a `Decimal`; a `float` is rejected rather than coerced, for
    the same reason `cfo.payments.model` rejects one) converted from
    `from_ccy` to `to_ccy` using `rates` (as `ecb_rates` returns it). Goes
    through the rates' own base currency (EUR, for the ECB) in both
    directions, since that is the only currency it quotes directly against
    -- so a EUR/USD/EUR round trip is exact `Decimal` arithmetic with no
    accumulated rounding."""
    if isinstance(amount, float):
        raise ToolkitError(("amount", "a float amount is not accepted here; pass a Decimal -- "
                            "money must not carry binary rounding error"))
    amount = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    from_ccy, to_ccy = str(from_ccy).upper(), str(to_ccy).upper()
    if from_ccy == to_ccy:
        return amount

    base = str(rates.get("base") or BASE_CURRENCY).upper()
    table = rates.get("rates") or {}

    def rate_per_base(ccy):
        if ccy == base:
            return Decimal("1")
        raw = table.get(ccy)
        if raw is None:
            raise ToolkitError((ccy, f"no ECB reference rate for '{ccy}' in these rates"))
        return raw if isinstance(raw, Decimal) else Decimal(str(raw))

    in_base = amount / rate_per_base(from_ccy)
    return in_base * rate_per_base(to_ccy)
