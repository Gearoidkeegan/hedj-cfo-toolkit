"""task accept: validate a runner's output, check its quotes, then cache and cost it."""
import json
import os
import re

from cfo import runs
from cfo.console import ToolkitError
from cfo.io import estimate_tokens, read_json, write_json_atomic
from cfo.tasks.cache import cache_put
from cfo.tasks.costlog import append_cost, estimate_usd, load_prices
from cfo.tasks.definition import load_schema, load_task
from cfo.tasks.prepare import task_work_dir
from cfo.tasks.quotes import check_quotes
from cfo.tasks.schema_check import validate

_FENCE_RE = re.compile(r"^\s*```[A-Za-z]*\s*\n(.*)\n\s*```\s*$", re.S)


def parse_output(text):
    match = _FENCE_RE.match(text)
    if match:
        text = match.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolkitError(("output.json",
                            f"not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}"))


def _work(run_dir, task):
    work = task_work_dir(run_dir, task.id)
    meta = read_json(os.path.join(work, "meta.json"))
    if meta is None:
        raise ToolkitError(("task", f"{task.id} has not been prepared in this run"))
    return work, meta


def accept(run_dir, task_dir):
    task = load_task(task_dir)
    work, meta = _work(run_dir, task)
    attempts = runs.load_run(run_dir)["tasks"].get(task.id, {}).get("attempts", 0) + 1
    output_path = os.path.join(work, "output.json")
    if not os.path.isfile(output_path):
        runs.update_task(run_dir, task.id, attempts=attempts)
        raise ToolkitError(("output.json", f"not written for {task.id}"))
    try:
        with open(output_path, encoding="utf-8-sig") as fh:
            text = fh.read()
    except UnicodeDecodeError:
        runs.update_task(run_dir, task.id, attempts=attempts, status="rejected")
        raise ToolkitError(("output.json", "output.json is not UTF-8 text"))
    try:
        data = parse_output(text)
    except ToolkitError:
        runs.update_task(run_dir, task.id, attempts=attempts, status="rejected")
        raise

    problems = validate(data, load_schema(task))
    inputs = {}
    for name in meta["inputs"]:
        with open(os.path.join(work, "inputs", f"{name}.txt"), encoding="utf-8") as fh:
            inputs[name] = fh.read()
    problems.extend(check_quotes(data, task.quote_checks, inputs))
    if problems:
        runs.update_task(run_dir, task.id, attempts=attempts, status="rejected")
        raise ToolkitError(problems)

    accepted = os.path.join(work, "accepted.json")
    write_json_atomic(accepted, data)
    cache_put(runs.cache_dir_of(run_dir), meta["key"], data)
    out_tokens = estimate_tokens(json.dumps(data, ensure_ascii=False))
    usd = estimate_usd(load_prices(), task.tier, meta["est_input_tokens"], out_tokens)
    total = append_cost(run_dir, {"task": task.id, "tier": task.tier,
                                  "est_input_tokens": meta["est_input_tokens"],
                                  "est_output_tokens": out_tokens, "est_usd": usd, "cached": False})
    runs.update_task(run_dir, task.id, status="accepted", attempts=attempts)
    return {"task": task.id, "status": "accepted", "accepted": accepted, "attempts": attempts,
            "est_usd": usd, "run_cost_estimate_usd": total}


def mark_failed(run_dir, task_dir, reason):
    task = load_task(task_dir)
    entry = runs.update_task(run_dir, task.id, status="failed", reason=str(reason)[:300])
    return {"task": task.id, "status": "failed", "reason": entry["reason"]}
