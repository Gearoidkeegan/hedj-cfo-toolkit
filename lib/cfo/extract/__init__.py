"""C1 document extractor: files in, located text blocks out.

Writes <run>/extract/manifest.json, docs/<doc_id>.json, docs/<doc_id>.md and
hidden-text.json. Files already extracted (same sha256) are skipped.
"""
import os

from cfo import __version__, runs
from cfo.console import ToolkitError
from cfo.extract import docx, eml, pdf, pptx, text, xlsx
from cfo.extract.discover import discover
from cfo.extract.sink import MissingDependency, Sink, finalise_findings, render_md
from cfo.io import now_iso, read_json, sha256_file, update_json, write_json_atomic, write_text_atomic

HANDLERS = {
    ".txt": text.handle_text, ".md": text.handle_text, ".csv": text.handle_csv,
    ".png": text.handle_image, ".jpg": text.handle_image, ".jpeg": text.handle_image,
    ".pptx": pptx.handle_pptx, ".pdf": pdf.handle_pdf, ".docx": docx.handle_docx,
    ".xlsx": xlsx.handle_xlsx, ".xlsm": xlsx.handle_xlsx, ".eml": eml.handle_eml,
}
TYPES = {".txt": "text", ".md": "text", ".csv": "csv", ".png": "image", ".jpg": "image",
         ".jpeg": "image", ".pptx": "pptx", ".pdf": "pdf", ".docx": "docx",
         ".xlsx": "xlsx", ".xlsm": "xlsx", ".eml": "email"}
MAX_CHILD_DEPTH = 1


def _text_chars(doc):
    total = 0
    for block in doc["blocks"]:
        total += len(block.get("text", ""))
        total += sum(len(str(c)) for row in block.get("rows", []) for c in row)
    return total


def extract(run_dir, paths):
    runs.load_run(run_dir)  # fail fast if run_dir is not a valid run folder
    out = os.path.join(run_dir, "extract")
    docs_dir = os.path.join(out, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    known = {d["doc_id"]: d for d in read_json(os.path.join(out, "manifest.json"),
                                                {"documents": []})["documents"]}
    old_findings = read_json(os.path.join(out, "hidden-text.json"), {"findings": []})["findings"]

    files, unsupported, missing = discover(paths, HANDLERS, skip_dirs=(run_dir,))
    problems = [(path, "not found") for path in missing]
    warnings = [(rel, "unsupported file type, not extracted") for _, rel in unsupported]
    if not files and not problems:
        raise ToolkitError(("extract", "no supported files found (supported: "
                            + ", ".join(sorted(HANDLERS)) + ")"), warnings)

    sink, extracted, skipped_ids, touched = Sink(), 0, set(), []
    sink.out_dir = out
    queue = [(path, rel, None, 0) for path, rel in files]
    while queue:
        path, rel, parent, depth = queue.pop(0)
        sha = sha256_file(path)
        doc_id = sha[:12]
        touched.append({"file": rel, "sha256": sha})
        if doc_id in sink.docs:
            if rel not in sink.docs[doc_id]["files"]:
                sink.docs[doc_id]["files"].append(rel)
            continue
        if doc_id in known and os.path.exists(os.path.join(docs_dir, doc_id + ".json")):
            if rel not in known[doc_id]["files"]:
                known[doc_id]["files"].append(rel)
            skipped_ids.add(doc_id)
            continue
        ext = os.path.splitext(path)[1].lower()
        sink.start(doc_id, rel, TYPES[ext], sha, parent)
        try:
            children = HANDLERS[ext](path, rel, sink) or []
        except MissingDependency as exc:
            sink.problem(rel, str(exc))
            del sink.docs[doc_id]
            continue
        except Exception as exc:  # one unreadable file must not stop the rest
            sink.warn(f"could not be read ({type(exc).__name__}: {exc})")
            sink.check("failed")
            children = []
        extracted += 1
        for child in children:
            child_rel = f"{rel}/{os.path.basename(child)}"
            if depth >= MAX_CHILD_DEPTH:
                warnings.append((child_rel, "attachments inside attachments are not extracted"))
            elif os.path.splitext(child)[1].lower() in HANDLERS:
                queue.append((child, child_rel, doc_id, depth + 1))
            else:
                warnings.append((child_rel, "unsupported attachment type, not extracted"))

    new_warnings = []
    for doc_id, doc in sink.docs.items():
        write_json_atomic(os.path.join(docs_dir, doc_id + ".json"),
                          {k: doc[k] for k in ("doc_id", "files", "type", "blocks")})
        write_text_atomic(os.path.join(docs_dir, doc_id + ".md"), render_md(doc))
        entry = {k: v for k, v in doc.items() if k != "blocks"}
        entry.update(blocks=len(doc["blocks"]), text_chars=_text_chars(doc))
        known[doc_id] = entry
        new_warnings.extend((doc["files"][0], w) for w in doc["warnings"])

    findings = old_findings + finalise_findings(sink.findings)
    summary = {level: sum(f["severity"] == level for f in findings) for level in ("high", "medium", "low")}
    summary["instruction_like"] = sum(bool(f["instruction_like"]) for f in findings)
    write_json_atomic(os.path.join(out, "hidden-text.json"), {"summary": summary, "findings": findings})
    write_json_atomic(os.path.join(out, "manifest.json"), {
        "toolkit_version": __version__, "updated_at": now_iso(), "hidden_text": summary,
        "documents": sorted(known.values(), key=lambda d: d["files"][0])})

    def _add_inputs(data):
        # I2: a locked read-modify-write, not a load_run(...)/save_run(...)
        # straddling the (possibly slow) extraction work above -- a
        # concurrent `task prepare`/`accept` on the same run must not have
        # its run.json update overwritten by this call's stale snapshot,
        # and must not itself clobber the inputs recorded here.
        if data is None:
            raise ToolkitError(("run", f"{run_dir} is not a CFO Toolkit run folder (no run.json)"))
        seen = {i["sha256"] for i in data["inputs"]}
        for item in touched:
            if item["sha256"] not in seen:
                seen.add(item["sha256"])
                data["inputs"].append(item)
        return data

    update_json(os.path.join(run_dir, "run.json"), _add_inputs)

    result = {"extract": out, "documents": len(known), "extracted": extracted,
              "skipped": len(skipped_ids),
              "hidden_text": summary, "_warnings": warnings + new_warnings}
    if problems or sink.problems:
        raise ToolkitError(problems + sink.problems, result["_warnings"])
    return result
