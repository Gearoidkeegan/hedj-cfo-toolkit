"""File helpers: atomic writes, hashing, slugs, token estimates, a file lock
and a shared read-modify-write / save-via-temp helper (I2, M3, I6)."""
import contextlib
import datetime as _dt
import hashlib
import json
import math
import os
import re
import tempfile
import time
import unicodedata

from cfo.console import ToolkitError

# os.replace can fail with a transient PermissionError on Windows while
# another process briefly holds the destination open (a sharing violation).
# Retrying gives that process a chance to release it.
_REPLACE_RETRIES = 10
_REPLACE_DELAY = 0.05

# A file lock (cfo-toolkit-runs/.../run.json.lock etc.) older than this is
# assumed to be left over from a process that died without cleaning up.
_LOCK_STALE_SECONDS = 60.0
_LOCK_BACKOFF_START = 0.01
_LOCK_BACKOFF_CAP = 0.2


def _replace_atomic(tmp, path):
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(_REPLACE_DELAY)


def _atomic(path, data, binary):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-")
    try:
        if binary:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
        else:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(data)
        _replace_atomic(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def write_text_atomic(path, text):
    _atomic(path, text, binary=False)


def write_bytes_atomic(path, data):
    _atomic(path, data, binary=True)


def write_json_atomic(path, data):
    write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def write_json_compact_atomic(path, data):
    """Like `write_json_atomic`, but without the indentation (about 35%
    smaller): for a file that is itself a model task's input and so counts
    against that task's token cap (I1) -- the panel and critic inputs -- not
    for run state, which stays indented for a person to read."""
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _clear_stale_lock(lock_path):
    try:
        age = time.time() - os.path.getmtime(lock_path)
    except OSError:
        return
    if age > _LOCK_STALE_SECONDS:
        try:
            os.remove(lock_path)
        except OSError:
            pass


@contextlib.contextmanager
def locked(path, timeout=15.0):
    """A cross-process file lock for read-modify-write access to `path`
    (I2). Creates `<path>.lock` exclusively, retrying with a short backoff
    until `timeout`, then raises ToolkitError. A lock file older than 60s is
    treated as abandoned and taken over."""
    lock_path = str(path) + ".lock"
    deadline = time.time() + timeout
    delay = _LOCK_BACKOFF_START
    while True:
        _clear_stale_lock(lock_path)
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except (FileExistsError, PermissionError):
            # On Windows, racing another process's os.remove() of the same
            # lock file (it is briefly in a delete-pending state) can surface
            # as PermissionError instead of FileExistsError. Either way, it
            # means someone else holds the lock right now: keep retrying.
            if time.time() >= deadline:
                raise ToolkitError(
                    (path, "is busy: another toolkit command is updating it; try again"))
            time.sleep(delay)
            delay = min(delay * 2, _LOCK_BACKOFF_CAP)
    try:
        yield
    finally:
        try:
            os.remove(lock_path)
        except OSError:
            pass


def update_json(path, fn, default=None):
    """Locked read-modify-write of a JSON file (I2): reads `path` (or
    `default` when missing), calls `fn(data)` for the new value, and writes
    it back atomically, all while holding `path`'s lock."""
    with locked(path):
        data = fn(read_json(path, default))
        write_json_atomic(path, data)
        return data


def _remove_ignoring_errors(path):
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def save_via_temp(out_path, write, suffix=""):
    """Write a file safely via a temp file in the same folder (M3/I6):
    `write(tmp_path)` does the actual save. Rejects an existing folder at
    `out_path` up front, retries the final replace against a transient
    Windows sharing violation, and never lets a masked cleanup error hide
    the real one."""
    if os.path.isdir(out_path):
        raise ToolkitError((out_path, "is a folder; give a file name"))
    folder = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-", suffix=suffix)
    os.close(fd)
    try:
        write(tmp)
    except BaseException:
        _remove_ignoring_errors(tmp)
        raise
    try:
        _replace_atomic(tmp, out_path)
    except PermissionError:
        _remove_ignoring_errors(tmp)
        raise ToolkitError((out_path, "cannot be replaced: close the file (it may be open in "
                            "Excel, Word or PowerPoint) and try again"))
    except BaseException:
        _remove_ignoring_errors(tmp)
        raise


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slugify(name):
    ascii_name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug or "company"


def estimate_tokens(text):
    return math.ceil(len(text or "") / 4)


def now_iso():
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def today_iso():
    return _dt.date.today().isoformat()
