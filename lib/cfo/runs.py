"""Run folders: one per tool run, under <cwd>/cfo-toolkit-runs/<company-slug>/."""
import collections
import datetime as _dt
import os
import re
import subprocess

from cfo import __version__
from cfo.console import ToolkitError
from cfo.io import locked, now_iso, read_json, sha256_bytes, slugify, update_json, write_json_atomic

RUNS_DIRNAME = "cfo-toolkit-runs"
DEPTHS = ("quick", "full")
MODES = ("parallel", "inline")
TOOL_RE = re.compile(r"[a-z][a-z0-9-]{1,40}")
SYNC_PROVIDERS = ("OneDrive", "Dropbox", "Google Drive", "My Drive", "iCloudDrive",
                  "iCloud Drive", "Box", "Box Sync", "Mobile Documents")
# macOS ~/Library/CloudStorage/<Provider>-<account>/... folders (I7).
CLOUDSTORAGE_PREFIXES = (("OneDrive-", "OneDrive"), ("GoogleDrive-", "Google Drive"),
                         ("Box-", "Box"), ("Dropbox-", "Dropbox"))
# Windows env vars whose target folder is a synced OneDrive tree (I7). A
# SharePoint library synced alongside OneDriveCommercial cannot be seen
# reliably this way, so it is not guessed at.
ONEDRIVE_ENV_VARS = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")
MAX_SLUG_LENGTH = 60


def sync_provider(path):
    abs_path = os.path.abspath(path)
    parts = abs_path.replace("\\", "/").split("/")
    for part in parts:
        for name in SYNC_PROVIDERS:
            if part == name or part.startswith(name + " "):
                return name
        for prefix, name in CLOUDSTORAGE_PREFIXES:
            if part.startswith(prefix):
                return name
    if "Library" in parts and "CloudStorage" in parts:
        idx = parts.index("CloudStorage")
        if idx + 1 < len(parts) and parts[idx + 1]:
            return parts[idx + 1].split("-", 1)[0]
    if os.name == "nt":
        norm_path = os.path.normcase(abs_path)
        for var in ONEDRIVE_ENV_VARS:
            value = os.environ.get(var)
            if not value:
                continue
            norm_value = os.path.normcase(os.path.abspath(value))
            if norm_path == norm_value or norm_path.startswith(norm_value + os.sep):
                return "OneDrive"
    return None


def _capped_slug(company):
    """slugify(company), capped at MAX_SLUG_LENGTH and cut at a hyphen
    boundary where possible. When the cut happens, an 8-hex-character hash of
    the full name is appended so two long names can't collide (I6)."""
    slug = slugify(company)
    if len(slug) <= MAX_SLUG_LENGTH:
        return slug
    cut = slug[:MAX_SLUG_LENGTH]
    boundary = cut.rfind("-")
    if boundary > 0:
        cut = cut[:boundary]
    digest = sha256_bytes(str(company).encode("utf-8"))[:8]
    return f"{cut}-{digest}"


def _git(cwd, *args):
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    except (OSError, ValueError):
        return None


def ensure_git_excluded(cwd):
    """Add cfo-toolkit-runs/ to the clone's local exclude file when the folder is
    inside a git work tree and not already ignored. Returns True when added."""
    inside = _git(cwd, "rev-parse", "--is-inside-work-tree")
    if inside is None or inside.returncode != 0 or inside.stdout.strip() != "true":
        return False
    ignored = _git(cwd, "check-ignore", "-q", RUNS_DIRNAME + "/")
    if ignored is not None and ignored.returncode == 0:
        return False
    found = _git(cwd, "rev-parse", "--git-path", "info/exclude")
    if found is None or found.returncode != 0:
        return False
    exclude = os.path.join(cwd, found.stdout.strip())
    os.makedirs(os.path.dirname(exclude), exist_ok=True)
    existing = ""
    if os.path.exists(exclude):
        with open(exclude, encoding="utf-8") as fh:
            existing = fh.read()
    with open(exclude, "a", encoding="utf-8") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.write(RUNS_DIRNAME + "/\n")
    return True


def init_run(tool, company, depth="full", mode="parallel", cwd=None, today=None):
    problems = []
    if not TOOL_RE.fullmatch(tool or ""):
        problems.append(("tool", f"'{tool}' is not a valid tool name"))
    if not str(company or "").strip():
        problems.append(("company", "a company name is needed"))
    if depth not in DEPTHS:
        problems.append(("depth", f"'{depth}' must be one of {', '.join(DEPTHS)}"))
    if mode not in MODES:
        problems.append(("mode", f"'{mode}' must be one of {', '.join(MODES)}"))
    if problems:
        raise ToolkitError(problems)
    cwd = os.path.abspath(cwd or os.getcwd())
    today = today or _dt.date.today().isoformat()
    slug = _capped_slug(company)
    company_dir = os.path.join(cwd, RUNS_DIRNAME, slug)
    base = f"{tool}-{today}"
    run_dir, n = os.path.join(company_dir, base), 2
    while os.path.exists(run_dir):
        run_dir, n = os.path.join(company_dir, f"{base}-{n}"), n + 1
    for sub in ("extract", "tasks", "outputs", "logs"):
        os.makedirs(os.path.join(run_dir, sub))
    save_run(run_dir, {
        "tool": tool, "toolkit_version": __version__, "depth": depth, "mode": mode,
        "started_at": now_iso(), "company_slug": slug, "company_name": str(company).strip(),
        "inputs": [], "tasks": {}, "cost_estimate_usd": 0.0,
    })
    return {"run": run_dir, "company_dir": company_dir,
            "profile": os.path.join(company_dir, "company-profile.json"),
            "git_excluded": ensure_git_excluded(cwd), "sync_warning": sync_provider(cwd)}


def load_run(run_dir):
    """A plain (Python) file read is not exclusive against Windows renaming
    the same path (os.replace can hit a transient sharing-violation
    PermissionError against an open reader), so this takes the same lock as
    update_json's writes (I2) rather than reading run.json unguarded."""
    path = os.path.join(run_dir, "run.json")
    with locked(path):
        data = read_json(path)
    if data is None:
        raise ToolkitError(("run", f"{run_dir} is not a CFO Toolkit run folder (no run.json)"))
    return data


def save_run(run_dir, data):
    write_json_atomic(os.path.join(run_dir, "run.json"), data)


def update_task(run_dir, task_id, **fields):
    """Locked read-modify-write of run.json (I2): parallel mode issues
    prepare/accept commands for several tasks in a run at once, so this must
    not lose an entry to a concurrent writer."""
    run_json = os.path.join(run_dir, "run.json")

    def _update(data):
        if data is None:
            raise ToolkitError(("run", f"{run_dir} is not a CFO Toolkit run folder (no run.json)"))
        entry = data["tasks"].setdefault(task_id, {})
        entry.update(fields)
        entry["updated_at"] = now_iso()
        return data

    data = update_json(run_json, _update)
    return data["tasks"][task_id]


def company_dir_of(run_dir):
    return os.path.dirname(os.path.abspath(run_dir))


def runs_root_of(run_dir):
    return os.path.dirname(company_dir_of(run_dir))


def cache_dir_of(run_dir):
    return os.path.join(runs_root_of(run_dir), ".cache")


def run_status(run_dir):
    data = load_run(run_dir)
    counts = collections.Counter(t.get("status", "unknown") for t in data["tasks"].values())
    return {"run": os.path.abspath(run_dir), "tool": data["tool"], "depth": data["depth"],
            "mode": data["mode"], "tasks": dict(counts),
            "cost_estimate_usd": data["cost_estimate_usd"]}
