#!/usr/bin/env python3
"""CFO Toolkit command line: python3 lib/toolkit.py <group> <command> [args]

Every command prints one line of JSON on success. Problems are printed as
ERROR/WARNING lines on stderr. Exit codes: 0 ok, 1 fixable problem, 2 internal error.
"""
import argparse
import datetime as _dt
import errno
import json
import os
import re
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cfo import __version__  # noqa: E402
from cfo.console import (EXIT_INTERNAL, EXIT_OK, EXIT_USER, ToolkitError,  # noqa: E402
                         emit, print_lines, print_problems, setup_streams)
from cfo import preflight as _preflight  # noqa: E402
from cfo import runs as _runs  # noqa: E402
from cfo.tasks import prepare as _prepare  # noqa: E402
from cfo.tasks import accept as _accept  # noqa: E402
from cfo import extract as _extract  # noqa: E402
from cfo.profile import questions as _questions  # noqa: E402
from cfo.profile import store as _store  # noqa: E402

# cfo.workbook, cfo.documents.docx_build and cfo.documents.deck_build pull in
# openpyxl / python-docx / python-pptx at import time. They are imported
# lazily, inside the command functions below, so every other command (and
# preflight itself) still works when one of those libraries is missing.


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ToolkitError(("arguments", message))


def cmd_version(args):
    return {"toolkit_version": __version__}


def cmd_preflight(args):
    result = _preflight.check()
    if not result["ok"]:
        problems = []
        if "python>=3.11" in result["missing"]:
            problems.append(("python", f"Python 3.11 or newer is needed; this is {result['python']}"))
        libs = [m for m in result["missing"] if m != "python>=3.11"]
        if libs:
            problems.append(("libraries", f"missing {', '.join(libs)}: run {result['install']}"))
        raise ToolkitError(problems, result["_warnings"])
    return result


def add_preflight_commands(groups):
    groups.add_parser("preflight", help="check Python and libraries").set_defaults(func=cmd_preflight)


def cmd_run_init(args):
    return _runs.init_run(args.tool, args.company, depth=args.depth, mode=args.mode)


def cmd_run_status(args):
    return _runs.run_status(args.run)


def add_run_commands(groups):
    run = groups.add_parser("run", help="run folders")
    sub = run.add_subparsers(dest="command", required=True, parser_class=_Parser)
    p = sub.add_parser("init", help="create a run folder")
    p.add_argument("--tool", required=True)
    p.add_argument("--company", required=True)
    p.add_argument("--depth", default="full", choices=["quick", "full"])
    p.add_argument("--mode", default="parallel", choices=["parallel", "inline"])
    p.set_defaults(func=cmd_run_init)
    p = sub.add_parser("status", help="summarise a run")
    p.add_argument("--run", required=True)
    p.set_defaults(func=cmd_run_status)


def _parse_inputs(pairs):
    inputs = {}
    for pair in pairs or []:
        name, sep, path = pair.partition("=")
        if not sep or not name or not path:
            raise ToolkitError(("--input", f"'{pair}' must look like name=path"))
        inputs[name] = path
    return inputs


def cmd_task_prepare(args):
    return _prepare.prepare(args.run, args.task, _parse_inputs(args.input))


def cmd_task_accept(args):
    return _accept.accept(args.run, args.task)


def cmd_task_status(args):
    if args.failed is not None:
        if not args.task:
            raise ToolkitError(("--failed", "needs --task"))
        return _accept.mark_failed(args.run, args.task, args.failed)
    return {**_runs.run_status(args.run), "details": _runs.load_run(args.run)["tasks"]}


def add_task_commands(groups):
    task = groups.add_parser("task", help="AI task steps")
    sub = task.add_subparsers(dest="command", required=True, parser_class=_Parser)
    p = sub.add_parser("prepare", help="build a task bundle")
    p.add_argument("--run", required=True)
    p.add_argument("--task", required=True, help="task definition folder")
    p.add_argument("--input", action="append", default=[], help="name=path, repeatable")
    p.set_defaults(func=cmd_task_prepare)
    p = sub.add_parser("accept", help="validate and accept a task's output")
    p.add_argument("--run", required=True)
    p.add_argument("--task", required=True, help="task definition folder")
    p.set_defaults(func=cmd_task_accept)
    p = sub.add_parser("status", help="task statuses, or mark a task failed")
    p.add_argument("--run", required=True)
    p.add_argument("--task", help="task definition folder (with --failed)")
    p.add_argument("--failed", metavar="REASON", help="mark the task failed with this reason")
    p.set_defaults(func=cmd_task_status)
    return sub


def cmd_extract(args):
    return _extract.extract(args.run, args.paths)


def add_extract_commands(groups):
    p = groups.add_parser("extract", help="extract located text from documents into the run folder")
    p.add_argument("--run", required=True)
    p.add_argument("paths", nargs="+", help="files or folders")
    p.set_defaults(func=cmd_extract)


def _unreadable(path, exc):
    if str(path).lstrip().startswith(("{", "[")):
        return ToolkitError((str(path)[:40], "expects a path to a file, not inline JSON: write the JSON to a file first"))
    if isinstance(exc, UnicodeDecodeError):
        return ToolkitError((path, "is not UTF-8 text"))
    if isinstance(exc, FileNotFoundError):
        return ToolkitError((path, "not found"))
    return ToolkitError((path, f"cannot be read: {exc.strerror or exc}"))


def _read_spec(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ToolkitError((path, f"not valid JSON: {exc.msg} at line {exc.lineno}"))
    except (OSError, UnicodeDecodeError) as exc:
        raise _unreadable(path, exc)


def cmd_workbook_build(args):
    from cfo.workbook import build as _workbook
    return _workbook.build_workbook(_read_spec(args.spec), args.out)


def add_workbook_commands(groups):
    workbook = groups.add_parser("workbook", help="Excel workbooks")
    sub = workbook.add_subparsers(dest="command", required=True, parser_class=_Parser)
    p = sub.add_parser("build", help="build a workbook from a JSON spec")
    p.add_argument("--spec", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_workbook_build)


def _read_text(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise _unreadable(path, exc)


def cmd_doc_build(args):
    from cfo.documents import docx_build as _docx
    return _docx.build_docx(_read_text(args.md), args.out, template=args.template,
                            base_dir=os.path.dirname(os.path.abspath(args.md)))


def add_doc_commands(groups):
    doc = groups.add_parser("doc", help="Word documents")
    sub = doc.add_subparsers(dest="command", required=True, parser_class=_Parser)
    p = sub.add_parser("build", help="build a Word document from Markdown")
    p.add_argument("--md", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--template", choices=["report", "policy", "onepager"])
    p.set_defaults(func=cmd_doc_build)


def cmd_deck_build(args):
    from cfo.documents import deck_build as _deck
    return _deck.build_deck(_read_spec(args.spec), args.out)


def add_deck_commands(groups):
    deck = groups.add_parser("deck", help="PowerPoint decks")
    sub = deck.add_subparsers(dest="command", required=True, parser_class=_Parser)
    p = sub.add_parser("build", help="build a deck from a JSON spec")
    p.add_argument("--spec", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_deck_build)


def _company_dir(args):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.company):
        raise ToolkitError(("--company", "use the company slug from run init, e.g. acme-ltd"))
    root = os.path.abspath(args.root or os.path.join(os.getcwd(), _runs.RUNS_DIRNAME))
    return os.path.join(root, args.company)


def cmd_profile_next(args):
    bank = _questions.load_bank()
    profile = _store.load_profile(_company_dir(args))
    return {"company": args.company, "tool": args.tool,
            "questions": _questions.next_questions(bank, profile, args.tool, args.max),
            "remaining": _questions.pending_count(bank, profile, args.tool)}


def cmd_profile_set(args):
    bank = _questions.load_bank()
    company_dir = _company_dir(args)
    profile = _store.load_profile(company_dir)
    answers = _read_spec(args.answers)
    if not isinstance(answers, dict):
        raise ToolkitError((args.answers, "answers must be a JSON object of question id to answer"))
    problems = _questions.apply_answers(bank, profile, args.tool, answers)
    _store.save_profile(company_dir, profile)
    if problems:
        raise ToolkitError(problems)
    return {"company": args.company, "saved": len(answers), "profile": _store.profile_file(company_dir)}


def cmd_profile_show(args):
    profile = _store.load_profile(_company_dir(args))
    if args.fields:
        data = {field: _store.get_path(profile, field) for field in args.fields.split(",")}
    else:
        data = _store.for_task(profile) if args.for_task else profile
    return {"company": args.company, "profile": data}


def add_profile_commands(groups):
    profile = groups.add_parser("profile", help="company profile and quiz")
    sub = profile.add_subparsers(dest="command", required=True, parser_class=_Parser)
    for name, func in (("next", cmd_profile_next), ("set", cmd_profile_set), ("show", cmd_profile_show)):
        p = sub.add_parser(name)
        p.add_argument("--company", required=True)
        p.add_argument("--root", help="runs folder (default: ./cfo-toolkit-runs)")
        p.set_defaults(func=func)
        if name in ("next", "set"):
            p.add_argument("--tool", required=True)
        if name == "next":
            p.add_argument("--max", type=int, default=4)
        if name == "set":
            p.add_argument("--answers", required=True)
        if name == "show":
            p.add_argument("--fields")
            p.add_argument("--for-task", action="store_true")


def build_parser():
    parser = _Parser(prog="toolkit.py", description="CFO Toolkit command line")
    groups = parser.add_subparsers(dest="group", required=True, parser_class=_Parser)
    groups.add_parser("version", help="print the toolkit version").set_defaults(func=cmd_version)
    add_preflight_commands(groups)
    add_run_commands(groups)
    add_task_commands(groups)
    add_extract_commands(groups)
    add_workbook_commands(groups)
    add_doc_commands(groups)
    add_deck_commands(groups)
    add_profile_commands(groups)
    return parser


def _log_internal(args, exc):
    run = getattr(args, "run", None) if args is not None else None
    folder = os.path.join(run, "logs") if run and os.path.isdir(run) else tempfile.gettempdir()
    os.makedirs(folder, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(folder, f"cfo-toolkit-error-{stamp}.log")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    return path


def _internal_error_line(args, exc):
    path = _log_internal(args, exc)
    return [("internal", f"{type(exc).__name__}: {exc} (details: {path})")]


def _missing_library_error(exc):
    """For a ModuleNotFoundError naming a library preflight lists as required,
    return (where, message) with preflight's own install line. Reuses
    cfo.preflight.REQUIRED instead of duplicating the package names. Returns
    None when the missing module is not one preflight tracks."""
    match = next((r for r in _preflight.REQUIRED if r[0] == exc.name), None)
    if match is None:
        return None
    _module, dist, _low, _high, pin = match
    installer = "python" if sys.platform == "win32" else "python3"
    return (dist, f"missing {dist}: run {installer} -m pip install {pin}")


def _long_path_error(exc):
    """For a Windows long-path OSError (winerror 206/3, or ENAMETOOLONG),
    return (where, message). Returns None for any other OSError."""
    if getattr(exc, "winerror", None) in (206, 3) or exc.errno == errno.ENAMETOOLONG:
        where = exc.filename or "path"
        return (where, "this folder path is too long for Windows; move the files to a "
                       "shorter folder such as C:\\cfo and try again")
    return None


def main(argv=None):
    setup_streams()
    args = None
    try:
        args = build_parser().parse_args(argv)
        result = args.func(args)
        warnings = result.pop("_warnings", []) if isinstance(result, dict) else []
        print_problems([], warnings)
        emit(result)
        return EXIT_OK
    except ToolkitError as exc:
        print_problems(exc.problems, exc.warnings)
        return EXIT_USER
    except ModuleNotFoundError as exc:
        problem = _missing_library_error(exc)
        if problem is None:
            print_lines("ERROR", _internal_error_line(args, exc))
            return EXIT_INTERNAL
        print_lines("ERROR", [problem])
        return EXIT_USER
    except OSError as exc:
        problem = _long_path_error(exc)
        if problem is None:
            print_lines("ERROR", _internal_error_line(args, exc))
            return EXIT_INTERNAL
        print_lines("ERROR", [problem])
        return EXIT_USER
    except Exception as exc:  # anything unexpected: log it, one line out
        print_lines("ERROR", _internal_error_line(args, exc))
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
