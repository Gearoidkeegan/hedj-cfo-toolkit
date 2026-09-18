"""MiFID II professional-client size test (Annex II, I(2)): a company is professional
when it meets at least two of: balance sheet total of EUR 20m, net turnover of EUR 40m,
own funds of EUR 2m."""
from cfo.profile.schema import get_path

THRESHOLDS = (("size.balance_sheet_eur", 20_000_000), ("size.turnover_eur", 40_000_000),
              ("size.own_funds_eur", 2_000_000))


def mifid_professional(profile):
    met = unknown = 0
    for path, threshold in THRESHOLDS:
        value = get_path(profile, path)
        if value is None:
            unknown += 1
        elif value >= threshold:
            met += 1
    if met >= 2:
        return True
    if met + unknown < 2:
        return False
    return None
