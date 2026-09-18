"""Spots text that reads like instructions to an AI, or like an attempt to
redirect a payment. Hidden text that matches is high severity; visible text
(used by the payments tool) is low severity."""
import re

PATTERNS = [
    ("ai", "ignore instructions",
     r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|earlier|all|these|your)\b"
     r".{0,20}\b(instructions?|rules|prompts?|guidance)\b"),
    ("ai", "role change",
     r"\b(you are now|act as|pretend to be|you are an?)\s+(an?\s+|the\s+)?"
     r"(ai|assistant|chatbot|model|llm|agent|reviewer|analyst)\b"),
    ("ai", "prompt reference",
     r"\b(system|developer)\s+prompt\b|\[/?inst\]|<\|im_(start|end)\|>|\bassistant\s*:"),
    ("ai", "AI named", r"\b(as an ai|language model|llm|chatgpt|claude|copilot|gemini)\b"),
    ("ai", "concealment", r"\bdo\s+not\s+(tell|mention|flag|report|note|disclose|reveal)\b"),
    ("payment", "changed bank details",
     r"\b(new|updated|changed|amended|revised)\s+(bank|banking|account|payment|remittance)\s+"
     r"(details|information|instructions)\b"),
    ("payment", "IBAN change",
     r"\biban\b.{0,40}\b(changed|updated|new|amended)\b|\b(changed|updated|new|amended)\b.{0,40}\biban\b"),
    ("payment", "redirect payment",
     r"\b(pay|send|transfer|remit)\b.{0,20}\b(to|into)\s+(this|the following|our new|the new)\s+account\b"),
    ("payment", "urgency", r"\burgent(ly)?\b.{0,30}\b(transfer|payment|wire|remit|pay)\b"),
    ("payment", "secrecy", r"\b(keep|treat)\s+this\s+(strictly\s+)?(confidential|private|between us)\b"),
    ("payment", "avoid verification",
     r"\b(do not|don't|dont|no need to)\s+(call|phone|contact|verify|check with)\b"),
    ("payment", "rush approval",
     r"\b(approve|release|process)\s+(this|the)\s+(payment|transfer|invoice)\s+"
     r"(immediately|today|now|urgently)\b"),
]
_COMPILED = [(family, name, re.compile(rx, re.I | re.S)) for family, name, rx in PATTERNS]


def _scan(text, severity):
    found = []
    for family, name, rx in _COMPILED:
        match = rx.search(text or "")
        if match:
            found.append({"family": family, "pattern": name,
                          "match": " ".join(match.group(0).split())[:120], "severity": severity})
    return found


def scan_instruction_like(text):
    return _scan(text, "high")


def scan_visible(text):
    return _scan(text, "low")


def looks_like_instructions(text):
    return bool(scan_instruction_like(text))
