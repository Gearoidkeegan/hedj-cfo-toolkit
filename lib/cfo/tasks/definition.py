"""Loads a task definition folder: task.json, prompt.md and schema.json."""
import dataclasses
import json
import os
import re

from cfo.console import ToolkitError
from cfo.tasks.schema_check import unsupported_keywords

TIERS = ("haiku", "sonnet", "opus")
DEPTHS = ("quick", "full")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*)+$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[\])?(\.[A-Za-z_][A-Za-z0-9_]*(\[\])?)*$")


@dataclasses.dataclass(frozen=True)
class InputSpec:
    name: str
    required: bool
    max_tokens: int


@dataclasses.dataclass(frozen=True)
class QuoteCheck:
    path: str
    input: str


@dataclasses.dataclass(frozen=True)
class TaskDef:
    dir: str
    id: str
    version: int
    tier: str
    depths: tuple
    group: str
    inputs: tuple
    quote_checks: tuple
    max_output_tokens: int
    prompt_path: str
    schema_path: str

    def input(self, name):
        return next((spec for spec in self.inputs if spec.name == name), None)


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def load_task(task_dir):
    task_dir = os.path.abspath(task_dir)
    where = os.path.basename(task_dir)
    paths = {name: os.path.join(task_dir, name) for name in ("task.json", "prompt.md", "schema.json")}
    missing = [name for name, path in paths.items() if not os.path.isfile(path)]
    if missing:
        raise ToolkitError([(where, f"missing {name}") for name in missing])
    try:
        with open(paths["task.json"], encoding="utf-8") as fh:
            raw = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ToolkitError((where, f"task.json is not valid JSON: {exc}"))

    if not isinstance(raw, dict):
        raise ToolkitError((where, "task.json must be a JSON object"))

    problems = []
    task_id = raw.get("id", "")
    if not isinstance(task_id, str) or not _ID_RE.match(task_id):
        problems.append(("id", f"'{task_id}' must look like tool.task-name"))
    version = raw.get("version")
    if not _positive_int(version):
        problems.append(("version", "must be a whole number of 1 or more"))
    tier = raw.get("tier")
    if tier not in TIERS:
        problems.append(("tier", f"must be one of {', '.join(TIERS)}"))
    depths = raw.get("depths", list(DEPTHS))
    if not isinstance(depths, list) or not depths or any(d not in DEPTHS for d in depths):
        problems.append(("depths", f"must be a non-empty list drawn from {', '.join(DEPTHS)}"))
    group = raw.get("group", "main")
    if not isinstance(group, str) or not _NAME_RE.match(group):
        problems.append(("group", "must be a lower-case name"))

    inputs, seen = [], set()
    for i, item in enumerate(raw.get("inputs", [])):
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not _NAME_RE.match(name) or name in seen:
            problems.append((f"inputs[{i}]", "needs a unique lower-case name"))
            continue
        if not _positive_int(item.get("max_tokens")):
            problems.append((f"inputs[{i}]", "max_tokens must be a positive whole number"))
            continue
        seen.add(name)
        inputs.append(InputSpec(name, bool(item.get("required", True)), item["max_tokens"]))

    checks = []
    for i, item in enumerate(raw.get("quote_checks", [])):
        path = item.get("path") if isinstance(item, dict) else None
        source = item.get("input") if isinstance(item, dict) else None
        if not isinstance(path, str) or not _PATH_RE.match(path):
            problems.append((f"quote_checks[{i}]", "path must look like findings[].quote"))
        elif source not in seen:
            problems.append((f"quote_checks[{i}]", f"input '{source}' is not declared"))
        else:
            checks.append(QuoteCheck(path, source))

    max_out = raw.get("max_output_tokens", 4000)
    if not _positive_int(max_out):
        problems.append(("max_output_tokens", "must be a positive whole number"))

    try:
        with open(paths["schema.json"], encoding="utf-8") as fh:
            schema = json.load(fh)
        problems.extend(("schema.json", message) for message in unsupported_keywords(schema))
    except json.JSONDecodeError as exc:
        problems.append(("schema.json", f"not valid JSON: {exc}"))

    if problems:
        raise ToolkitError([(f"{where} {w}", m) for w, m in problems])
    return TaskDef(task_dir, task_id, version, tier, tuple(depths), group, tuple(inputs),
                   tuple(checks), max_out, paths["prompt.md"], paths["schema.json"])


def load_schema(task):
    with open(task.schema_path, encoding="utf-8") as fh:
        return json.load(fh)
