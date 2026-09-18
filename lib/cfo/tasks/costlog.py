"""Estimated cost of task runs, priced from assets/prices.json."""
import json
import os

from cfo.io import now_iso, update_json
from cfo.paths import asset


def load_prices():
    with open(asset("prices.json"), encoding="utf-8") as fh:
        return json.load(fh)


def estimate_usd(prices, tier, input_tokens, output_tokens):
    rate = prices["per_million_tokens"][tier]
    return round((input_tokens * rate["input"] + output_tokens * rate["output"]) / 1_000_000, 6)


def append_cost(run_dir, entry):
    """Appends one cost.jsonl line, then locked-read-modify-writes run.json's
    running total (I2): concurrent tasks in the same run append and total
    their cost at the same time."""
    entry = {"at": now_iso(), **entry}
    path = os.path.join(run_dir, "logs", "cost.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _add(data):
        data["cost_estimate_usd"] = round(
            data.get("cost_estimate_usd", 0.0) + entry.get("est_usd", 0.0), 6)
        return data

    data = update_json(os.path.join(run_dir, "run.json"), _add)
    return data["cost_estimate_usd"]
