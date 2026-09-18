"""Finds the files to extract."""
import os

SKIP_DIRS = {"cfo-toolkit-runs", "__pycache__", "node_modules"}


def discover(paths, extensions, skip_dirs=()):
    extensions = {e.lower() for e in extensions}
    skip_abs = {os.path.abspath(p) for p in skip_dirs}
    files, unsupported, missing = [], [], []

    def add(path, rel):
        target = files if os.path.splitext(path)[1].lower() in extensions else unsupported
        target.append((os.path.abspath(path), rel.replace("\\", "/")))

    for src in paths:
        if os.path.isfile(src):
            add(src, os.path.basename(src))
        elif os.path.isdir(src):
            for base, dirs, names in os.walk(src):
                dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS
                                 and os.path.abspath(os.path.join(base, d)) not in skip_abs)
                for name in sorted(names):
                    if not name.startswith(("~$", ".")):
                        path = os.path.join(base, name)
                        add(path, os.path.relpath(path, src))
        else:
            missing.append(src)
    return files, unsupported, missing
