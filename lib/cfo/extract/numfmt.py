"""Excel number formats: which ones are dates, and serial numbers as ISO dates."""
import datetime as _dt
import re

BUILTIN_DATE_IDS = {14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47}
_QUOTED = re.compile(r'"[^"]*"')
_ESCAPED = re.compile(r"\\.")
_BRACKETS = re.compile(r"\[[^\]]*\]")
_ELAPSED = re.compile(r"\[(h+|m+|s+)\]", re.I)


def is_date_format(fmt_id, code):
    try:
        if int(fmt_id) in BUILTIN_DATE_IDS:
            return True
    except (TypeError, ValueError):
        pass
    code = code or ""
    if not code:
        return False
    section = code.split(";")[0]
    if _ELAPSED.search(section):
        return False
    cleaned = _BRACKETS.sub("", _ESCAPED.sub("", _QUOTED.sub("", section)))
    if re.search(r"[dy]", cleaned, re.I):
        return True
    return bool(re.search(r"m", cleaned, re.I)) and not re.search(r"[hs0#?]", cleaned, re.I)


def serial_to_iso(value, date1904=False):
    serial = float(value)
    days = int(serial)
    seconds = round((serial - days) * 86400)
    if days == 0 and not date1904:
        hours, rest = divmod(seconds, 3600)
        minutes, secs = divmod(rest, 60)
        return f"{hours:02d}:{minutes:02d}" + (f":{secs:02d}" if secs else "")
    if not date1904 and days == 60:
        # the 1900 system's fictitious 29 Feb 1900, which datetime cannot represent
        hours, rest = divmod(seconds, 3600)
        minutes, secs = divmod(rest, 60)
        if hours == minutes == secs == 0:
            return "1900-02-29"
        text = f"1900-02-29T{hours:02d}:{minutes:02d}"
        return text + (f":{secs:02d}" if secs else "")
    if date1904:
        base = _dt.datetime(1904, 1, 1)
    elif serial < 60:
        base = _dt.datetime(1899, 12, 31)   # the 1900 system counts a 29 Feb 1900 that never was
    else:
        base = _dt.datetime(1899, 12, 30)
    moment = base + _dt.timedelta(days=days, seconds=seconds)
    if moment.hour == moment.minute == moment.second == 0:
        return moment.date().isoformat()
    text = moment.strftime("%Y-%m-%dT%H:%M")
    return text + (moment.strftime(":%S") if moment.second else "")
