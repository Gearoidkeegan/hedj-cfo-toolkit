"""Word list and clause numbering, so extracted paragraphs carry the clause
references readers cite: 22, 22.1, 22.1(a), 22.1(a)(i)."""
import re

from cfo.extract.ooxml import q

_PLACEHOLDER = re.compile(r"%([1-9])")


def _int(el, default=None):
    if el is None:
        return default
    try:
        return int(el.get(q("w:val")))
    except (TypeError, ValueError):
        return default


def _ilvl(el):
    try:
        return int(el.get(q("w:ilvl"), "0"))
    except (TypeError, ValueError):
        return None


def to_roman(n):
    pairs = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
             (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    out = []
    for value, letters in pairs:
        while n >= value:
            out.append(letters)
            n -= value
    return "".join(out)


def to_letters(n):
    if n < 1:
        return ""
    return "abcdefghijklmnopqrstuvwxyz"[(n - 1) % 26] * ((n - 1) // 26 + 1)


def format_number(n, fmt):
    if fmt == "decimalZero":
        return f"{n:02d}"
    if fmt == "lowerLetter":
        return to_letters(n)
    if fmt == "upperLetter":
        return to_letters(n).upper()
    if fmt == "lowerRoman":
        return to_roman(n)
    if fmt == "upperRoman":
        return to_roman(n).upper()
    if fmt in ("none", "bullet"):
        return ""
    return str(n)


class Level:
    __slots__ = ("start", "fmt", "text", "restart", "legal", "pstyle")

    def __init__(self, el):
        self.start = _int(el.find(q("w:start")), 1)
        fmt = el.find(q("w:numFmt"))
        self.fmt = fmt.get(q("w:val")) if fmt is not None else "decimal"
        text = el.find(q("w:lvlText"))
        self.text = text.get(q("w:val"), "") if text is not None else ""
        restart = el.find(q("w:lvlRestart"))
        self.restart = _int(restart) if restart is not None else None
        self.legal = el.find(q("w:isLgl")) is not None
        pstyle = el.find(q("w:pStyle"))
        self.pstyle = pstyle.get(q("w:val")) if pstyle is not None else None


class Numbering:
    def __init__(self, numbering_root, styles_root):
        self.abstract = {}      # abstractNumId -> {ilvl: Level}
        self.nums = {}          # numId -> (abstractNumId, {ilvl: start}, {ilvl: Level})
        self.style_numpr = {}   # styleId -> (numId or None, ilvl or None)
        self.based_on = {}
        self.counters = {}      # abstractNumId -> {ilvl: current value}
        self.started = set()
        if numbering_root is not None:
            for a in numbering_root.findall(q("w:abstractNum")):
                levels_by_ilvl = {}
                for lvl in a.findall(q("w:lvl")):
                    ilvl = _ilvl(lvl)
                    if ilvl is not None:
                        levels_by_ilvl[ilvl] = Level(lvl)
                self.abstract[a.get(q("w:abstractNumId"))] = levels_by_ilvl
            for n in numbering_root.findall(q("w:num")):
                abstract = n.find(q("w:abstractNumId"))
                starts, levels = {}, {}
                for override in n.findall(q("w:lvlOverride")):
                    ilvl = _ilvl(override)
                    if ilvl is None:
                        continue
                    start = override.find(q("w:startOverride"))
                    if start is not None:
                        starts[ilvl] = _int(start, 1)
                    lvl = override.find(q("w:lvl"))
                    if lvl is not None:
                        levels[ilvl] = Level(lvl)
                self.nums[n.get(q("w:numId"))] = (
                    abstract.get(q("w:val")) if abstract is not None else None, starts, levels)
        if styles_root is not None:
            for s in styles_root.findall(q("w:style")):
                sid = s.get(q("w:styleId"))
                based = s.find(q("w:basedOn"))
                if based is not None:
                    self.based_on[sid] = based.get(q("w:val"))
                numpr = s.find(f"{q('w:pPr')}/{q('w:numPr')}")
                if numpr is not None:
                    num_id = numpr.find(q("w:numId"))
                    self.style_numpr[sid] = (num_id.get(q("w:val")) if num_id is not None else None,
                                             _int(numpr.find(q("w:ilvl"))))

    def _chain(self, sid):
        chain, seen = [], set()
        while sid and sid not in seen:
            seen.add(sid)
            chain.append(sid)
            sid = self.based_on.get(sid)
        return chain

    def _levels(self, num_id):
        abstract_id, _, overrides = self.nums[num_id]
        return {**self.abstract.get(abstract_id, {}), **overrides}

    def _level(self, num_id, ilvl):
        return self._levels(num_id).get(ilvl)

    def _start(self, num_id, ilvl):
        """A level's effective start for this numId: its startOverride if present,
        otherwise the level's own w:start (ECMA-376 17.9.26)."""
        _, starts, _ = self.nums[num_id]
        if ilvl in starts:
            return starts[ilvl]
        level = self._level(num_id, ilvl)
        return level.start if level is not None else 1

    def _resolve(self, ppr, pstyle):
        num_id = ilvl = None
        numpr = ppr.find(q("w:numPr")) if ppr is not None else None
        if numpr is not None:
            el = numpr.find(q("w:numId"))
            num_id = el.get(q("w:val")) if el is not None else None
            ilvl = _int(numpr.find(q("w:ilvl")))
        chain = self._chain(pstyle)
        for sid in chain:
            if sid in self.style_numpr:
                style_num, style_ilvl = self.style_numpr[sid]
                num_id = style_num if num_id is None else num_id
                ilvl = style_ilvl if ilvl is None else ilvl
                break
        if num_id in (None, "0") or num_id not in self.nums:
            return None, None
        if ilvl is None:
            # the most specific style wins: Heading2 (based on Heading1) takes Heading2's level
            levels = sorted(self._levels(num_id).items())
            ilvl = next((k for sid in chain for k, level in levels if level.pstyle == sid), None)
        return num_id, ilvl or 0

    def _display(self, num_id, ilvl, state):
        level = self._level(num_id, ilvl)

        def replace(match):
            k = int(match.group(1)) - 1
            ref = self._level(num_id, k)
            if ref is None:
                return ""
            fmt = "decimal" if level.legal and ref.fmt not in ("bullet", "none") else ref.fmt
            return format_number(state.get(k, self._start(num_id, k)), fmt)

        return _PLACEHOLDER.sub(replace, level.text).strip()

    def _citation(self, num_id, ilvl, state):
        level = self._level(num_id, ilvl)
        if level is None:
            return ""
        own = self._display(num_id, ilvl, state).rstrip(".").strip()
        referenced = {int(p) - 1 for p in _PLACEHOLDER.findall(level.text)}
        if ilvl == 0 or all(k in referenced for k in range(ilvl)):
            return own
        parent = self._citation(num_id, ilvl - 1, state)
        if parent and own and parent[-1].isalnum() and own[0].isalnum():
            return f"{parent}.{own}"
        return parent + own

    def label(self, ppr, pstyle):
        num_id, ilvl = self._resolve(ppr, pstyle)
        if num_id is None:
            return None
        level = self._level(num_id, ilvl)
        if level is None:
            return None
        abstract_id, starts, _ = self.nums[num_id]
        state = self.counters.setdefault(abstract_id, {})
        if num_id not in self.started:
            self.started.add(num_id)
            for lvl in sorted(starts):
                for deeper in [k for k in state if k >= lvl]:
                    del state[deeper]
        state[ilvl] = state.get(ilvl, self._start(num_id, ilvl) - 1) + 1
        for deeper in [k for k in state if k > ilvl]:
            d = self._level(num_id, deeper)
            if d is None or d.restart is None or (d.restart != 0 and ilvl + 1 <= d.restart):
                del state[deeper]
        if level.fmt == "bullet":
            return None
        own = self._display(num_id, ilvl, state).rstrip(".").strip()
        if not own:
            return None
        return self._citation(num_id, ilvl, state) or None
