"""Find and normalise the quantities in a piece of text.

This is the deterministic core of onus. Every number a claim makes -- money,
percentages, multiples, counts, years, quarters, dates, durations -- is parsed
here, from the text itself, never taken from a model's reading of it. A model
extracts claims and judges what words mean; it does not get to decide that
"$2.4B" is 2.4 billion.

`$2.4B`, `2.4 billion` and `$2,400M` normalise to the same value. "Two thirds"
is a percentage. A value carries the precision it was written with, and a
claim is matched at its own: "$2.4B" matches $2,412M (it rounds to it), and
"42%" matches neither 41% nor a source that only said "about 40%".

A number from 1950 to 2099 is a year or a count: "2000 users" reads as a
count and "in 2000" as a year, and either way it is checked as both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

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
         "duration": "duration", "number": "number"}

TEMPORAL = {"year", "quarter", "date", "duration"}


@dataclass(frozen=True)
class Quantity:
    raw: str  # as written; a range end, with the tail it shares
    kind: str  # currency amount percent fraction multiple count year quarter date duration number
    value: float  # normalised; for quarter and date, a sortable encoding (see below)
    tolerance: float  # half a unit of the precision it was written with
    start: int
    end: int
    unit: str = ""  # durations: "month", "year"...; currency: the symbol
    year: int | None = None  # quarters written with a year
    alt: str = ""  # the other kind this number could be; every 1950-2099 number carries one
    scale: float = 1.0  # a chart value whose chart states its unit ("$B"): the money it is, with `unit`
    operands: tuple[int, ...] = ()  # a derived value: where the quantities it was computed from end
    cell: str = ""  # a table cell read as a magnitude: the kind it was printed as, for its unscaled reading

    @property
    def group(self) -> str:
        return GROUP[self.kind]

    @property
    def groups(self) -> set[str]:
        """Every group this number may be matched as; more than one where `alt` is set."""
        return {GROUP[k] for k in (self.kind, self.alt) if k}

    @property
    def temporal(self) -> bool:
        return self.kind in TEMPORAL

    def matches(self, other: Quantity) -> bool:
        """Whether `other`, in the evidence, states this one at the claim's own precision."""
        if "number" in (self.kind, other.kind):
            return self._magnitude(other)
        if not self.groups & other.groups:
            return False
        if self.kind == "duration" and self.unit != other.unit:
            return False
        if self.kind == "quarter" and self.year is not None and other.year is not None and self.year != other.year:
            return False
        slack = self.tolerance
        if other.kind == "fraction" and slack:
            unit = slack * 2
            if abs(other.value / unit - round(other.value / unit)) > 1e-9:
                slack = unit  # two thirds lands between 66% and 67%, so both state it; three quarters is just 75%
        return abs(self.value - other.value) <= slack + 1e-9

    def _magnitude(self, other: Quantity) -> bool:
        """A chart value printed with no format is a magnitude and no unit: a number of any kind at
        that magnitude states it (2400000 is $2.4M, 0.88 is 88%), and it states any. Under a chart
        that states its unit, the money at that scale states it too: 96.2 under "($B)" is $96.2B."""
        plain, unit = (self, other) if self.kind == "number" else (other, self)
        if unit.temporal or plain.cell and unit.group not in ("money", "count", "number"):
            return False
        readings = [(plain.value, unit.value, self.tolerance)]
        if unit.group == "percent":
            readings.append((plain.value, unit.value / 100, self.tolerance * (0.01 if self is unit else 1.0)))
        if plain.unit and unit.group == "money" and unit.unit in ("", plain.unit):
            readings.append((plain.value * plain.scale, unit.value,
                             self.tolerance * (plain.scale if self is plain else 1.0)))
        if plain.cell:  # "26.0" in a cell is no "26" on a page, nor "$36" a bare 36: precision and kind hold
            first = readings[0]
            same = unit.group in (GROUP[plain.cell], "number") and unit.tolerance <= first[2] + 1e-9
            readings = [r for r in readings if r is not first or same]
        return any(abs(a - b) <= tolerance + 1e-9 for a, b, tolerance in readings)


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
# A scale with no currency symbol before it; a bare letter is not one ("3 t" is tonnes).
AMOUNT_SCALE_WORDS = r"thousand|million|billion|trillion|bn|mm"
AMOUNT_SCALE = re.compile(AMOUNT_SCALE_WORDS, re.I)
WORDNUM = "|".join(sorted(WORDS, key=len, reverse=True))

RANGE_TAIL = rf"%|percent|per cent|[x×]|days?|weeks?|months?|quarters?|years?|{SCALE_WORDS}"
# A range holds to one line, and its dash is set tight: "Headcount 12 - 15%" is a label and a value.
H = r"[^\S\n]"
# Every dash a word processor, LaTeX or a PDF paste leaves behind.
RANGE_SEP = rf"(?<!\s)[-\u2010-\u2015\u2212](?!\s)|{H}+to{H}+|{H}+and{H}+"
# "and" joins two numbers in any sentence; only "between" makes them a range.
BETWEEN = re.compile(r"\bbetween\s+(?:(?:approximately|about|roughly|around|nearly|some|circa)\s+)?$", re.I)
# "from $2M to $3M" states a change between two numbers; any other range states one spread.
FROM = re.compile(r"\bfrom\s+$", re.I)
# What the duration pattern itself refuses: "2-3 years over the plan".
NOT_A_DURATION = re.compile(r"\s+(?:over|on)\b", re.I)

# A date cue before the number says year, a noun after it says count.
DATE_CUES = (r"in|by|since|from|until|till|through|during|after|before|between|calendar|fiscal|fy|cy|q[1-4]"
             r"|circa|early|mid|late")
# A determiner introduces either reading, so it overrules a noun without being a date cue.
WEAK_CUES = r"on|for|and|or|the|this|that|last|next|our|your|their|its|his|her|[a-z]+['\u2019]s"
_CUE_END = r"[\s,'\u2019(\[\u2013-]*$"
DATE_CUE = re.compile(rf"(?:\b(?:{DATE_CUES})\b|©){_CUE_END}", re.I)
YEAR_CUE = re.compile(rf"(?:\b(?:{DATE_CUES}|{WEAK_CUES})\b|©){_CUE_END}", re.I)
# Words that follow a number ending in "s" without being a noun it counts.
NOT_COUNTED = ("is", "was", "has", "does", "goes", "shows", "marks", "means", "seems", "looks", "says", "sees",
               "remains", "stays", "gives", "brings", "as", "this", "thus", "plus", "less", "unless", "versus",
               "vs", "across", "towards", "always", "perhaps", "yes", "its", "his", "hers", "ours", "yours",
               "theirs", "minus")
PLURAL_NOUN = rf"(?!(?:{'|'.join(NOT_COUNTED)})\b)[a-z]{{2,}}s|people|staff|men|women|children|personnel|crew"
# "-Q1" after a year is the quarter of that year, not a unit it counts.
COUNTED_NOUN = re.compile(rf"-(?!q[1-4]\b)[a-z]|\s+(?:{PLURAL_NOUN})\b", re.I)

PATTERNS = [
    ("range", re.compile(rf"(?<![\w.$€£#@/-])(?:(?:us|ca|c|a|nz|hk|s|r)?(?P<symbol>[$€£]))?{H}?"
                         rf"(?P<low>{NUM}){H}?(?P<low_tail>{RANGE_TAIL})?(?P<sep>{RANGE_SEP})"
                         rf"(?P<repeat>[$€£])?{H}?(?P<high>{NUM})(?:\n(?-i:(?=[a-z]))|{H})?(?P<tail>{RANGE_TAIL})?"
                         rf"(?![a-z0-9]|-[a-z])", re.I)),
    ("currency", re.compile(rf"([$€£])\s?({NUM})(?:\s?({SCALE_WORDS}))?(?![a-z])", re.I)),
    # Accounting negatives, "$(34.9)M" and "$ | (0.38)": a filing's HTML puts the dollar sign in a cell of its own.
    ("currency", re.compile(rf"([$€£])\s?\(({NUM})\)(?:\s?({SCALE_WORDS}))?(?![a-z])", re.I)),
    ("currency", re.compile(rf"([$€£])\s*\|\s*\(?({NUM})\)?()(?![\w%]|\.\d|,\d)")),
    ("percent", re.compile(rf"(?<![\w.])\(({NUM})\)\s?(%)")),
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
    ("amount", re.compile(rf"(?<![\w.$€£])({NUM})\s?({AMOUNT_SCALE_WORDS})\b", re.I)),
    ("year", re.compile(r"(?<![\w.,$])(19[5-9]\d|20\d\d)(?![\w%]|\.\d|,\d)")),
    ("count", re.compile(rf"(?<![\w.,$€£#@/-])({NUM})(?![\w%×]|\.[\w\d]|,\d|-\d{{7}}(?!\d)|-\d{{2,4}}-\d{{4}})"
                         rf"|\b({WORDNUM})\b", re.I)),
]

# Text no claim lives in: addresses, links, handles.
NOISE = re.compile(r"\S+@\S+|https?://\S+|www\.\S+|\b[\w-]+\.(?:com|io|ai|org|net)\b", re.I)


def _overlaps(spans, start, end) -> bool:
    return any(start < e and s < end for s, e in spans)


def find(text: str, plain: bool = False, unit: str = "", cells: bool = False) -> list[Quantity]:
    """Every quantity in `text`, longest and most specific first, no overlaps. With `plain`, a
    number that ends a line is a chart value printed with no format: a magnitude, no unit. With
    `unit` as well ("$B", as its chart states it), that magnitude is also money at that scale.
    With `cells`, `text` is a table row or cell under a table that states `unit`: a number filling
    a cell is read as that magnitude, which states it unscaled or as money at that scale."""
    taken = [(m.start(), m.end()) for m in NOISE.finditer(text)]
    found = []
    for kind, pattern in PATTERNS:
        pos = 0
        while (m := pattern.search(text, pos)) is not None:
            qs: list[Quantity] = []
            if not _overlaps(taken, m.start(), m.end()):
                qs = _range(m) if kind == "range" else [q for q in (_build(kind, m),) if q is not None]
            if qs:
                taken.append((m.start(), m.end()))
                found += qs
            pos = m.end() if qs or kind != "range" else m.start() + 1  # "120% and 2-3x": try again inside
    if plain:
        symbol, scale = stated_unit(unit) or ("", 1.0)
        found = [replace(q, kind="number", alt="", unit=symbol, scale=scale)
                 if q.kind in ("count", "year") and _ends_line(text, q.end) else q for q in found]
    elif cells and (read := stated_unit(unit)):
        symbol, scale = read
        found = [replace(q, kind="number", alt="", unit=symbol, scale=scale, cell=q.kind)
                 if (q.kind == "count" or (q.kind == "currency" and not NAMES_ITS_SCALE.search(q.raw)))
                 and _fills_cell(text, q.start, q.end) else q for q in found]
    return sorted(found, key=lambda q: q.start)


_CELL_DRESSING = re.compile(r"[\s$€£()~≈+\-\u2212\u2013]*")


def _fills_cell(text: str, start: int, end: int) -> bool:
    """Whether the number at `start`-`end` is all its table cell holds, but for sign and currency."""
    before, after = text[:start].rsplit("|", 1)[-1], text[end:].split("|", 1)[0]
    return bool(_CELL_DRESSING.fullmatch(before) and _CELL_DRESSING.fullmatch(after))


def _ends_line(text: str, end: int) -> bool:
    return not text[end:].split("\n", 1)[0].strip()


CURRENCY_CODES = {"usd": "$", "eur": "€", "gbp": "£"}
# A chart's name for the unit its values are in: "Revenue ($B)", "in $ millions", "USD M", "US$M".
STATED_UNIT = re.compile(r"([$€£]|(?<![a-z])(?:usd|eur|gbp))\s*(?:in\s+)?"
                         r"((?:thousand|million|billion|trillion)s?|bn|mm|mn|tn|[kmbt])(?![a-z])", re.I)


def stated_unit(text: str) -> tuple[str, float] | None:
    """The currency symbol and scale `text` names, if it names both; a currency alone scales nothing."""
    m = STATED_UNIT.search(text)
    if m is None:
        return None
    symbol = m.group(1).lower()
    return CURRENCY_CODES.get(symbol, symbol), SCALE[m.group(2).lower().removesuffix("s")]


DURATION_UNITS = ("day", "week", "month", "quarter", "year")


def _list_marker(number: str) -> bool:
    """"01", "02": a list or card marker, not a count."""
    return number.startswith("0") and len(number) > 1 and "." not in number


def _tail_kind(symbol: str | None, tail: str) -> tuple[str, str, float] | None:
    """A range end's kind, unit and scale from the tail it shares, or None if that is no quantity."""
    if tail in ("%", "percent", "per cent"):
        return "percent", "", 1.0
    if tail in ("x", "\u00d7"):
        return "multiple", "", 1.0
    if tail.rstrip("s") in DURATION_UNITS:
        return "duration", tail.rstrip("s"), 1.0
    if symbol:
        return "currency", symbol, SCALE.get(tail, 1.0)  # next to a symbol a letter is a scale: "$2-3M"
    if AMOUNT_SCALE.fullmatch(tail):
        return "amount", "", SCALE[tail]
    return None


def _range(m: re.Match) -> list[Quantity]:
    """Both ends of "$2-3M", which is $2M and $3M: a tail stated once is meant twice.

    With nothing to share there is no range, and the patterns below read the
    text as they did before: "2019-2024" is two years, "3-4 t" is not money.
    """
    symbol, sep, repeated = m.group("symbol"), m.group("sep"), m.group("repeat")
    joined = sep.strip().lower()
    if joined == "and" and not BETWEEN.search(m.string[:m.start()]):
        return []
    if repeated and repeated != symbol:
        return []  # "$2 to \u20ac3M": each end carries its own currency
    low = (m.group("low_tail") or "", " " if m.start("low_tail") > m.end("low") else "")
    high = (m.group("tail") or "", " " if m.start("tail") > m.end("high") else "")
    whole = (m.start(), m.end())
    if "to" in joined and FROM.search(m.string[:m.start()]):
        low_span = (m.start(), m.end("low_tail") if m.group("low_tail") else m.end("low"))
        high_span = (m.start("repeat") if m.group("repeat") else m.start("high"), m.end())
    else:
        low_span = high_span = whole
    ends = []
    dashed = joined not in ("to", "and")
    for number, (written, gap), (start, end) in ((m.group("low"), low if low[0] else high, low_span),
                                                 (m.group("high"), high if high[0] or not dashed else low, high_span)):
        if _list_marker(number):
            return []
        read = _tail_kind(symbol, written.lower())
        if read is None:
            return []
        kind, unit, scale = read
        prefix = symbol if kind == "currency" else ""  # a symbol belongs to money, not to "$18 months"
        ends.append(Quantity(f"{prefix}{number}{gap}{written}", kind, _num(number) * scale,
                             _half_unit(number, scale), start, end, unit))
    if ends[-1].kind == "duration" and NOT_A_DURATION.match(m.string[m.end():]):
        return []
    if ends[0].value > ends[1].value:
        return []  # "the 2024-25 year" counts down, so it is no range, whatever the patterns below read
    if ends[0].kind == "duration" and all(e.value >= 1000 for e in ends):
        return []  # "the 2024-2025 year" is a span of years too, however it is written
    return ends


def _year_or_count(m: re.Match) -> tuple[str, str]:
    """Which kind a bare 1950-2099 number is, and which it might also be."""
    before, after = m.string[:m.start()], m.string[m.end():]
    noun = COUNTED_NOUN.match(after)
    hyphenated = noun is not None and noun.group(0).startswith("-")
    if noun and (not YEAR_CUE.search(before) or (hyphenated and not DATE_CUE.search(before))):
        return "count", "year"
    return "year", "count"


def _build(kind: str, m: re.Match) -> Quantity | None:
    raw, s, e = m.group(0).strip(), m.start(), m.end()
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
        read, alt = _year_or_count(m)
        tolerance = 0.0 if read == "year" else _half_unit(m.group(1))
        return Quantity(raw, read, float(m.group(1)), tolerance, s, e, alt=alt)
    if kind == "count":
        if m.group(2):
            return Quantity(raw, "count", float(WORDS[m.group(2).lower()]), 0.0, s, e)
        if _list_marker(m.group(1)):
            return None
        return Quantity(raw, "count", _num(m.group(1)), _half_unit(m.group(1)), s, e)
    raise NotImplementedError(kind)


def _year(text: str) -> int:
    y = int(text)
    return y + 2000 if y < 100 else y


def derived(quantities: list[Quantity]) -> list[Quantity]:
    """Values a writer could honestly compute from quantities stated together.

    From each pair of quantities of one kind: the percentage change between
    them, either way round, and their ratio. "1.4x, down from 2.1x" licenses
    "down 33%"; it does not license 50%. Only pairs from one sentence are
    combined -- across a whole document, some pair would produce almost any
    number by coincidence.

    The two ends of one range are not such a pair, unless the range states a
    change ("from $2M to $3M") rather than a spread; nor is a number that could
    be a year as easily as a count.
    """
    out = []
    for i, a in enumerate(quantities):
        for b in quantities[i + 1:]:
            if a.group != b.group or a.temporal or a.alt or b.alt or a.kind == "fraction":
                continue
            if not a.value or not b.value:
                continue
            if (a.start, a.end) == (b.start, b.end):
                continue  # one range, read at both of its ends
            for x, y in ((a, b), (b, a)):
                change = abs(x.value - y.value) / y.value * 100
                where = (x.end, y.end)
                out.append(Quantity(f"{x.raw} vs {y.raw}", "percent", change, 0.0, -1, -1, operands=where))
                out.append(Quantity(f"{x.raw} / {y.raw}", "multiple", x.value / y.value, 0.0, -1, -1,
                                    operands=where))
    return out


# "($ in millions)", "in thousands": a table states its unit once, and its rows print bare dollars.
UNIT_NOTE = re.compile(r"\bin\s+(thousand|million|billion)s\b", re.I)
NAMES_ITS_SCALE = re.compile(rf"(?:{SCALE_WORDS})$", re.I)
PROSE_LINE = 100  # a line this long with no cells is prose, and ends the table above it
# A table cell holding one number, as a filing prints it: "43,503", "(34,912)", "(46.5)%", "—".
VALUE_CELL = re.compile(r"[-\u2212\u2013(~]*[$€£]?\s*\(?\d[\d,]*(?:\.\d+)?(?:\s?(?:[kKmMbB]|mm|MM|bn|BN|[x×]))?\)?"
                        r"\s*%?\)?|[-\u2014\u2013]+")
# A table header row names a period: "Three Months Ended", "June 28, 2025", "Q2", "FY2024".
PERIOD_HEADER = re.compile(r"\b(?:months?|weeks?|quarters?|years?|ended|fiscal|fy\d*|q[1-4]|h[12])\b|\b(?:19|20)\d\d\b",
                           re.I)
# A row or column whose label says it counts things, not money, whatever the table's unit.
COUNTED = re.compile(r"\bshares\b|\bheadcount|\bemployees\b|\bstaff\b|\bpeople\b|\bcustomers\b|\busers\b|"
                     r"\bsubscribers\b|\bmembers\b|\baccounts\b|\bstores\b|\blocations\b|\bcountries\b|\bunits?\b|"
                     r"\bvolume\b|\bpounds?\b|\bdays?\b", re.I)
NOT_MONEY = re.compile(rf"%|\bpercent|\bmargin|\bbps\b|\bbasis points?|\bper share|\beps\b|\bratio\b|\(x\)|"
                       rf"{COUNTED.pattern}", re.I)


_YEAR_CELL = re.compile(r"(?:19|20)\d\d")


def scaled(text: str, quantities: list[Quantity]) -> list[Quantity]:
    """The dollar amounts of a table under a unit note, read at that scale as well. A filing puts
    "$" on a statement's first and total rows only, so every bare number in a row's cells is read
    as dollars too, but in a header row (no numbers but years, above the first data row) or a row
    whose label names no money ("Shares"). They stay
    readable unscaled: a per-share column under "(In thousands, except per share data)" is dollars.
    A blank line or a line of prose ends the table; an amount that names its own scale ("$4.2
    billion") is left alone."""
    out, scale, pos, noted, in_data = [], 1.0, 0, False, False
    for line in text.split("\n"):
        start, pos = pos, pos + len(line) + 1
        row = "|" in line
        if not line.strip() or (not row and len(line) > PROSE_LINE):
            in_data = False
        if not line.strip() and not noted or (not row and len(line) > PROSE_LINE):
            scale = 1.0  # a blank line right after a note keeps it for the table below
        if note := UNIT_NOTE.search(line):
            scale = SCALE[note.group(1).lower()]
        if line.strip():
            noted = bool(note) and not row
        if scale == 1.0:
            continue
        cells_from = start + line.find("|") if row else pos
        values = [c for c in (c.strip() for c in line.strip().strip("|").split("|")[1:])
                  if VALUE_CELL.fullmatch(c) and any(ch.isdigit() for ch in c)]
        # A header row holds no numbers but years, and those only above the table's first data row.
        header = not values or (not in_data and all(_YEAR_CELL.fullmatch(c) for c in values))
        in_data = in_data or (row and not header)
        bare = row and not header and not NOT_MONEY.search(line.split("|", 1)[0])
        for q in quantities:
            if not start <= q.start < pos - 1:
                continue
            if q.kind == "currency" and not NAMES_ITS_SCALE.search(q.raw):
                out.append(replace(q, value=q.value * scale, tolerance=q.tolerance * scale))
            elif bare and q.start > cells_from and q.kind in ("count", "year"):
                out.append(replace(q, kind="currency", alt="", unit="$", value=q.value * scale,
                                   tolerance=_half_unit(q.raw.strip("()"), scale)))
    return out


SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")


def sentences(text: str) -> list[str]:
    return [s for s in SENTENCE.split(text) if s.strip()]
