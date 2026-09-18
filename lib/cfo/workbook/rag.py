"""Red/amber/green rules shared by table status columns and dashboard KPIs."""
import re

from cfo.workbook.styles import RAG_LABELS, RAG_LEVELS

RAG_RE = re.compile(r'^(<=|>=|<>|<|>|=)\s*(-?\d+(?:\.\d+)?|"[^"]*")$')
RAG_MESSAGE = 'rag rules must be red/amber/green comparisons such as <180 or ="Breach"'


def valid_rag(rules):
    return isinstance(rules, dict) and bool(rules) and all(
        level in RAG_LEVELS and RAG_RE.match(str(rule).strip()) for level, rule in rules.items())


def condition(ref, rule):
    op, operand = RAG_RE.match(rule.strip()).groups()
    return f"{ref}{op}{operand}"


def status_formula(ref, rules):
    expr = '""' if "green" in rules else '"Green"'
    for level in reversed([lvl for lvl in RAG_LEVELS if lvl in rules]):
        expr = f'IF({condition(ref, rules[level])},"{RAG_LABELS[level]}",{expr})'
    return f'=IF({ref}="","",{expr})'
