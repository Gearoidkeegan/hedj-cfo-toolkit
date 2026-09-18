"""Console conventions shared by every command: UTF-8 output, one-line JSON
results, ERROR/WARNING lines and exit codes."""
import json
import re
import sys

EXIT_OK = 0
EXIT_USER = 1
EXIT_INTERNAL = 2
MAX_LINES = 20
MAX_LINE_LENGTH = 500
# C0 controls (incl. CR/LF), DEL, C1 controls, and the Unicode line/paragraph
# separators: a newline (or look-alike) here must not be able to forge a
# second stderr line.
_CONTROL_RE = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")


class ToolkitError(Exception):
    """A problem the user or the calling skill can fix. Exit code 1."""

    def __init__(self, problems, warnings=None):
        if isinstance(problems, tuple):
            problems = [problems]
        self.problems = [(str(w), str(m)) for w, m in problems]
        self.warnings = [(str(w), str(m)) for w, m in (warnings or [])]
        first = self.problems[0] if self.problems else ("toolkit", "unknown problem")
        super().__init__(f"{first[0]}: {first[1]}")


def setup_streams():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _single_line(text):
    """Collapse any control character or Unicode line/paragraph separator to a
    single space, so one `where`/`message` can never forge extra stderr
    lines (an injection channel: the orchestrating model reads stderr)."""
    return _CONTROL_RE.sub(" ", str(text))


def format_line(level, where, message):
    line = f"{level} {_single_line(where)}: {_single_line(message)}"
    if len(line) > MAX_LINE_LENGTH:
        # The one allowed silent shortening: this is diagnostic output, not
        # user data.
        line = line[:MAX_LINE_LENGTH - 1] + "\u2026"
    return line


def emit(result):
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def print_lines(level, items):
    items = list(items)
    for where, message in items[:MAX_LINES]:
        sys.stderr.write(format_line(level, where, message) + "\n")
    if len(items) > MAX_LINES:
        sys.stderr.write(f"{level} toolkit: {len(items) - MAX_LINES} more not shown\n")
    sys.stderr.flush()


def print_problems(errors, warnings):
    """Print ERROR lines, then WARNING lines, capped at MAX_LINES total
    (including a single summary line naming how many were not shown)."""
    combined = [("ERROR", w, m) for w, m in errors] + [("WARNING", w, m) for w, m in warnings]
    limit = MAX_LINES - 1 if len(combined) > MAX_LINES else len(combined)
    for level, where, message in combined[:limit]:
        sys.stderr.write(format_line(level, where, message) + "\n")
    hidden = combined[limit:]
    if hidden:
        level = "ERROR" if any(lvl == "ERROR" for lvl, _, _ in hidden) else "WARNING"
        sys.stderr.write(format_line(level, "toolkit", f"{len(hidden)} more not shown") + "\n")
    sys.stderr.flush()
