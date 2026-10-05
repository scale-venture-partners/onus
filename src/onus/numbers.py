"""Find and normalise the quantities in a piece of text.

This is the deterministic core of onus. Every number a claim makes -- money,
percentages, multiples, counts, years, quarters, dates, durations -- is parsed
here, from the text itself, never taken from a model's reading of it. A model
extracts claims and judges what words mean; it does not get to decide that
"$2.4B" is 2.4 billion.

`$2.4B`, `2.4 billion` and `$2,400M` normalise to the same value. "Two thirds"
is a percentage. A value carries the precision it was written with, so
"$2.4B" matches $2,412M (it rounds to it) and "42%" does not match 41%.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9

WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
         "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
         "fifty": 50, "hundred": 100}
# "one" is left out on purpose: "one of", "no one", "one ask" -- as a count it
# is almost never a claim, and as a word it is everywhere.
DURATION_WORDS = {**WORDS, "one": 1, "a": 1, "an": 1}

SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9,
         "billion": 1e9, "t": 1e12, "tn": 1e12, "trillion": 1e12}

FRACTIONS = {"half": 50.0, "halves": 50.0, "third": 100 / 3, "thirds": 100 / 3, "quarter": 25.0,
             "quarters": 25.0, "fifth": 20.0, "fifths": 20.0, "tenth": 10.0, "tenths": 10.0}
FRACTION_COUNT = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "nine": 9}

# Which kinds can stand for one another when matching.
GROUP = {"currency": "money", "amount": "money", "percent": "percent", "fraction": "percent",
         "multiple": "multiple", "count": "count", "year": "year", "quarter": "quarter", "date": "date",
         "duration": "duration"}

TEMPORAL = {"year", "quarter", "date", "duration"}


@dataclass(frozen=True)
class Quantity:
    raw: str  # as written
    kind: str  # currency amount percent fraction multiple count year quarter date duration
    value: float  # normalised; for quarter and date, a sortable encoding (see below)
    tolerance: float  # half a unit of the precision it was written with
    start: int
    end: int
    unit: str = ""  # durations: "month", "year"...; currency: the symbol
    year: int | None = None  # quarters written with a year

    @property
    def group(self) -> str:
        return GROUP[self.kind]

    @property
    def temporal(self) -> bool:
        return self.kind in TEMPORAL

    def matches(self, other: Quantity) -> bool:
        if self.group != other.group:
            return False
        if self.kind == "duration" and self.unit != other.unit:
            return False
        if self.kind == "quarter" and self.year is not None and other.year is not None and self.year != other.year:
            return False
        return abs(self.value - other.value) <= max(self.tolerance, other.tolerance) + 1e-9


def _half_unit(number: str, scale: float = 1.0) -> float:
    """Half the last written digit: '2.4' -> 0.05, '24' -> 0.5, '2,400' -> 0.5."""
    digits = number.replace(",", "")
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    return 0.5 * 10 ** -decimals * scale


def _num(text: str) -> float:
    return float(text.replace(",", ""))


NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?"
         r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
SCALE_WORDS = r"thousand|million|billion|trillion|bn|mm|mn|tn|[kmbt]"
WORDNUM = "|".join(sorted(WORDS, key=len, reverse=True))

PATTERNS = [
    ("currency", re.compile(rf"([$€£])\s?({NUM})\s?({SCALE_WORDS})?(?![a-z])", re.I)),
    ("percent", re.compile(rf"(?<![\w.])({NUM})\s?(%|percent\b|per cent\b)", re.I)),
    ("fraction", re.compile(r"\b(a|one|two|three|four|nine)[\s-]+(half|halves|thirds?|quarters?|fifths?|tenths?)\b"
                            r"(?!\s+(?:of\s+)?(?:the\s+)?(?:year|fiscal))", re.I)),
    ("multiple", re.compile(rf"(?<![\w.])({NUM})\s?[x×](?![a-z0-9])", re.I)),
    ("quarter", re.compile(r"\b(?:FY\s?)?Q([1-4])(?:\s*(?:FY\s?)?'?(\d{4}|\d{2}))?\b", re.I)),
    ("fiscal", re.compile(r"\bFY\s?'?(\d{4}|\d{2})\b", re.I)),
    ("date", re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({MONTH})\b"
                        rf"|\b({MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)),
    ("duration", re.compile(rf"\b({NUM}|{WORDNUM}|one|an?)[\s-]+(day|week|month|quarter|year)s?\b(?!\s+(?:over|on)\b)",
                            re.I)),
    ("amount", re.compile(rf"(?<![\w.$€£])({NUM})\s?(thousand|million|billion|trillion|bn|mm)\b", re.I)),
    ("year", re.compile(r"(?<![\w.,$])(19[5-9]\d|20\d\d)(?![\w%]|\.\d|,\d)")),
    ("count", re.compile(rf"(?<![\w.,$€£#@/-])({NUM})(?![\w%×.]|\.\d|,\d)|\b({WORDNUM})\b", re.I)),
]

# Text no claim lives in: addresses, links, handles.
NOISE = re.compile(r"\S+@\S+|https?://\S+|www\.\S+|\b[\w-]+\.(?:com|io|ai|org|net)\b", re.I)


def _overlaps(spans, start, end) -> bool:
    return any(start < e and s < end for s, e in spans)


def find(text: str) -> list[Quantity]:
    """Every quantity in `text`, longest and most specific first, no overlaps."""
    taken = [(m.start(), m.end()) for m in NOISE.finditer(text)]
    found = []
    for kind, pattern in PATTERNS:
        for m in pattern.finditer(text):
            if _overlaps(taken, m.start(), m.end()):
                continue
            q = _build(kind, m)
            if q is not None:
                taken.append((m.start(), m.end()))
                found.append(q)
    return sorted(found, key=lambda q: q.start)


def _build(kind: str, m: re.Match) -> Quantity | None:
    raw, s, e = m.group(0), m.start(), m.end()
    if kind == "currency":
        scale = SCALE.get((m.group(3) or "").lower(), 1.0)
        return Quantity(raw, "currency", _num(m.group(2)) * scale, _half_unit(m.group(2), scale), s, e, m.group(1))
    if kind == "amount":
        scale = SCALE[m.group(2).lower()]
        return Quantity(raw, "amount", _num(m.group(1)) * scale, _half_unit(m.group(1), scale), s, e)
    if kind == "percent":
        return Quantity(raw, "percent", _num(m.group(1)), _half_unit(m.group(1)), s, e)
    if kind == "fraction":
        n = FRACTION_COUNT[m.group(1).lower()]
        return Quantity(raw, "fraction", n * FRACTIONS[m.group(2).lower()], 1.0, s, e)
    if kind == "multiple":
        return Quantity(raw, "multiple", _num(m.group(1)), _half_unit(m.group(1)), s, e)
    if kind == "quarter":
        year = _year(m.group(2)) if m.group(2) else None
        return Quantity(raw, "quarter", float(m.group(1)), 0.0, s, e, year=year)
    if kind == "fiscal":
        return Quantity(raw, "year", float(_year(m.group(1))), 0.0, s, e)
    if kind == "date":
        day, month = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        month_n = MONTHS.get(month.lower().rstrip("."))
        if month_n is None or not 1 <= int(day) <= 31:
            return None
        return Quantity(raw, "date", month_n * 100 + int(day), 0.0, s, e)
    if kind == "duration":
        n = m.group(1).lower()
        value = DURATION_WORDS[n] if n in DURATION_WORDS else _num(n)
        return Quantity(raw, "duration", float(value), 0.0 if n in DURATION_WORDS else _half_unit(n), s, e,
                        unit=m.group(2).lower())
    if kind == "year":
        return Quantity(raw, "year", float(m.group(1)), 0.0, s, e)
    if kind == "count":
        if m.group(2):
            return Quantity(raw, "count", float(WORDS[m.group(2).lower()]), 0.0, s, e)
        if m.group(1).startswith("0") and len(m.group(1)) > 1 and "." not in m.group(1):
            return None  # "01", "02": list and card markers, not counts
        return Quantity(raw, "count", _num(m.group(1)), _half_unit(m.group(1)), s, e)
    raise NotImplementedError(kind)


def _year(text: str) -> int:
    y = int(text)
    return y + 2000 if y < 100 else y


def derived(quantities: list[Quantity]) -> list[Quantity]:
    """Values a writer could honestly compute from quantities stated together.

    From each pair of same-kind quantities: the percentage change between
    them, either way round, and their ratio. "1.4x, down from 2.1x" licenses
    "down 33%"; it does not license 50%. Only pairs from one sentence are
    combined -- across a whole document, some pair would produce almost any
    number by coincidence.
    """
    out = []
    for i, a in enumerate(quantities):
        for b in quantities[i + 1:]:
            if a.group != b.group or a.temporal or a.kind == "fraction" or not a.value or not b.value:
                continue
            for x, y in ((a, b), (b, a)):
                change = abs(x.value - y.value) / y.value * 100
                out.append(Quantity(f"{x.raw} vs {y.raw}", "percent", change, 0.0, -1, -1))
                out.append(Quantity(f"{x.raw} / {y.raw}", "multiple", x.value / y.value, 0.0, -1, -1))
    return out


SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")


def sentences(text: str) -> list[str]:
    return [s for s in SENTENCE.split(text) if s.strip()]
