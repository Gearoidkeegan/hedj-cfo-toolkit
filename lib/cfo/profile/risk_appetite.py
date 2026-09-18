"""Risk appetite on Hedj's five dimensions.

A port of computeRiskAppetite and getRiskBand from the Hedj platform's
risk-scoring.js (as of 2026-09-16). Keep the two in step:
tests/fixtures/risk_appetite_parity.json is generated from the JavaScript.
Arithmetic keeps the JavaScript's operand order so floating-point results match.
"""
import math

TURNOVER_SCORES = {"200%+": 90, "100%-200%": 75, "75%-100%": 60, "50%-75%": 45,
                   "25%-50%": 30, "10%-25%": 20, "<10%": 10}
TEAM_SCORES = {"10+ people": 80, "5-10 people": 65, "2-5 people": 50, "1-2 people": 35, "1 person": 20}
ACCURACY_SCORES = {1: 80, 2: 65, 3: 50, 4: 35, 5: 20}
TIMING_SCORES = {
    "Very precisely (to the day)": 90,
    "Precisely (within a few days)": 75,
    "Reasonably accurately (within 1 month)": 50,
    "Within 3 months": 30,
    "3 months or longer / regularly delays or rescheduling": 15,
}
CLARITY_SCORES = {"24m+": 90, "19-24m": 75, "13-18m": 60, "7-12m": 45, "4-6m": 30, "1-3m": 15, "Other": 30}
SENIORITY_SCORES = {
    "Treasury/Trading Manager": 85, "Financial Controller": 70, "Financial Director": 60,
    "CEO/Managing Director": 40, "Board of Directors or Executive Committee": 40,
    "Owner/Shareholder": 30, "Sales Manager": 25, "Other": 40,
}
PRIORITIES = ("rateCertainty", "hedgeAccounting", "minimiseCost", "lowOpportunityCost",
              "simplicity", "flexibility", "timingFlexibility", "rateEnhancement")
FX_STRATEGIES = ("Netting/Offsetting", "Risk Transfer", "Derivatives", "Risk Acceptance", "Avoidance", "Other")
OVERALL_WEIGHTS = {"FIT": 0.25, "CP": 0.25, "CC": 0.15, "CS": 0.15, "SF": 0.20}
DIMENSIONS = {"FIT": "Financial Impact Tolerance", "CP": "Certainty Preference",
              "CC": "Complexity Capacity", "CS": "Cost Sensitivity", "SF": "Strategic Flexibility"}


def js_round(x):
    """JavaScript Math.round: halves round up, unlike Python's round()."""
    return math.floor(x + 0.5)


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def rank_to_score(ranks, item):
    ranks = list(ranks or [])
    if item not in ranks:
        return 50
    n = len(ranks)
    if n == 1:
        return 95          # the JavaScript divides by zero here and yields NaN
    return js_round(95 - (85 / (n - 1)) * ranks.index(item))


def sophistication(count):
    if count >= 5:
        return 90
    return {4: 75, 3: 55, 2: 40, 1: 25}.get(count, 10)


def band(score):
    if score <= 20:
        return "Very Conservative"
    if score <= 40:
        return "Conservative"
    if score <= 55:
        return "Moderate"
    if score <= 75:
        return "Moderately Aggressive"
    return "Aggressive"


def compute(inputs):
    d = inputs or {}
    turnover = TURNOVER_SCORES.get(d.get("exposurePctTurnover"), 40)
    contingent = 15 if d.get("contingentRisk") else 0
    team = TEAM_SCORES.get(d.get("teamSize"), 40)
    accuracy = ACCURACY_SCORES.get(_as_int(d.get("forecastAccuracy")), 50)
    timing = TIMING_SCORES.get(d.get("timingAccuracy"), 50)
    clarity = CLARITY_SCORES.get(d.get("clarityHorizon"), 45)
    seniority = SENIORITY_SCORES.get(d.get("respondentPosition"), 50)
    strategies = set(d.get("fxStrategies") or []) | set(d.get("commodityStrategies") or []) \
        | set(d.get("irStrategies") or [])
    derivatives = 70 if ("Derivatives" in (d.get("fxStrategies") or []) or len(d.get("fxProducts") or []) > 0) else 20
    soph = sophistication(len(strategies))
    sp = d.get("strategyPriorities") or []
    cert, hedge_acct, cost_rank, opp_cost, simplicity, flex_rank, timing_flex, enhance = (
        rank_to_score(sp, key) for key in PRIORITIES)

    fit = js_round(turnover * 0.40 + contingent * 0.20 + team * 0.15 + accuracy * 0.25)
    cp = js_round(cert * 0.35 + hedge_acct * 0.20 + timing * 0.25 + clarity * 0.20)
    cc = js_round(team * 0.30 + soph * 0.30 + derivatives * 0.20 + seniority * 0.20)
    cs = js_round(cost_rank * 0.40 + opp_cost * 0.30 + simplicity * 0.15 + turnover * 0.15)
    sf = js_round(flex_rank * 0.30 + timing_flex * 0.25 + enhance * 0.20
                  + (80 if d.get("contingentRisk") else 30) * 0.15 + accuracy * 0.10)
    overall = js_round(fit * OVERALL_WEIGHTS["FIT"] + (100 - cp) * OVERALL_WEIGHTS["CP"]
                       + cc * OVERALL_WEIGHTS["CC"] + (100 - cs) * OVERALL_WEIGHTS["CS"]
                       + sf * OVERALL_WEIGHTS["SF"])
    return {"FIT": fit, "CP": cp, "CC": cc, "CS": cs, "SF": sf, "overall": overall, "band": band(overall)}
