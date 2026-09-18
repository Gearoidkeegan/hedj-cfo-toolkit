"""Email (.eml) handler: headers, body text and attachments."""
import email
import os
import re
from email import policy
from html.parser import HTMLParser

from cfo.extract.ooxml import safe

HEADERS = ("From", "Reply-To", "Return-Path", "To", "Cc", "Date", "Subject")


class _TextOnly(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "table", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}
    SKIP = {"script", "style", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(markup):
    parser = _TextOnly()
    parser.feed(markup)
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _attachment_bytes(part):
    if part.get_content_type() == "message/rfc822":
        inner = part.get_payload(0) if part.is_multipart() else None
        return inner.as_bytes() if inner is not None else None
    return part.get_payload(decode=True)


def handle_eml(path, rel, sink):
    with open(path, "rb") as fh:
        msg = email.message_from_binary_file(fh, policy=policy.default)
    header_no = 0
    for name in HEADERS:
        value = msg.get(name)
        if value:
            header_no += 1
            sink.block("email_header", f"{name}: {value}", {"part": "headers", "para": header_no},
                       header=name)
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        content = body.get_content()
        text = html_to_text(content) if body.get_content_type() == "text/html" else content
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    for number, para in enumerate(paragraphs, 1):
        sink.block("paragraph", para, {"part": "body", "para": number})

    children, attachments = [], []
    folder = os.path.join(sink.out_dir, "attachments", sink.current["doc_id"])
    for number, part in enumerate(msg.iter_attachments(), 1):
        default = f"attachment-{number}" + (".eml" if part.get_content_type() == "message/rfc822" else "")
        filename = part.get_filename() or default
        data = _attachment_bytes(part)
        if data is None:
            sink.warn(f"attachment {number} ({filename}) could not be decoded")
            continue
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, f"{number:02d}-{safe(os.path.basename(filename))}")
        with open(target, "wb") as fh:
            fh.write(data)
        attachments.append({"name": filename, "bytes": len(data), "content_type": part.get_content_type(),
                            "saved_as": os.path.basename(target)})
        children.append(target)
    sink.meta(bytes=os.path.getsize(path), subject=str(msg.get("Subject", "")), attachments=attachments)
    sink.check("not applicable")
    return children
