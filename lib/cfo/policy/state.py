"""One place for a policy run's working state: <run>/policy/<name>.json."""
import os

from cfo.console import ToolkitError
from cfo.io import read_json, write_json_atomic, write_text_atomic

NEXT_STEP = {"selection": "policy start", "coverage": "policy coverage-merge",
             "findings": "policy findings", "findings-base": "policy findings",
             "obligations": "policy obligations-merge",
             "obligations-parts": "policy obligations-input",
             "coverage-batches": "policy coverage-input"}


def policy_dir(run_dir):
    path = os.path.join(run_dir, "policy")
    os.makedirs(path, exist_ok=True)
    return path


def path_for(run_dir, name):
    return os.path.join(policy_dir(run_dir), f"{name}.json")


def read(run_dir, name, default=None):
    return read_json(path_for(run_dir, name), default)


def write(run_dir, name, data):
    write_json_atomic(path_for(run_dir, name), data)
    return data


def remove(run_dir, name):
    """Delete a state file, if it exists: a result that a later step has made
    stale must not be read as current."""
    try:
        os.remove(path_for(run_dir, name))
    except FileNotFoundError:
        pass


def write_input(run_dir, name, text):
    """A file a task will read. Returns its path."""
    path = os.path.join(policy_dir(run_dir), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_text_atomic(path, text)
    return path


def accepted(run_dir, task_id):
    """The accepted output of a task, or None when it has not been accepted."""
    return read_json(os.path.join(run_dir, "tasks", task_id, "accepted.json"))


def require(run_dir, name):
    data = read(run_dir, name)
    if data is None:
        raise ToolkitError((name, f"not ready yet: run `{NEXT_STEP.get(name, 'policy start')}` first"))
    return data
