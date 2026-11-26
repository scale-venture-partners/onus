"""What the claims are checked against.

Evidence is a list of passages, each with a source, and every quantity in
them parsed once. It can be plain files (notes, a data-room export, the
prompt), other documents (.pptx, .docx), JSON, an agent's session
transcript, or a whole directory -- a git repository, say, for a deck about
the code in it.

A directory brings three problems a single file doesn't, and each has an
answer here. Most of it is noise: lockfiles, binaries, generated files -- so
only tracked files of known text types under a size limit are read. Code
outweighs prose by volume -- so passages are ranked for the judge with BM25,
code weighted below prose. And a repository holds every small number there
is: ports, versions, loop bounds, "slide 17", "113 unsupported claims" -- so
a number read from a directory supports a claim only when its own sentence
(its line, in code or data) shares a word with the claim. `MAX_TURNS = 40`
supports "40 turns"; "on slide 17" does not support "a run costs $17". No
arithmetic is derived from code. A file named on its own is the user's
chosen source, and keeps the looser rule: any matching number counts.

A transcript needs care, because most of what is in it is the agent itself.
Only what came *into* the conversation counts: the user's messages and the
results of tools that fetch information. The agent's own text is excluded,
and so are tools that mostly echo the agent's own work back to it -- reading
the builder script it just wrote, or a hook's reminder quoting the slide.
Otherwise a fabrication becomes its own evidence the moment the agent reads
its file back.
"""

from __future__ import annotations

import bisect
import csv
import fnmatch
import io
import json
import math
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from onus import numbers

# Tools whose results are, by default, the agent's own work coming back to it.
LOCAL_TOOLS = ("read", "write", "edit", "bash", "ls", "find", "grep")


# What a directory contributes. Prose and data are read as they are; code is
# read too, but ranked lower and held to a stricter standard for numbers.
PROSE_SUFFIXES = {".md", ".markdown", ".mdx", ".rst", ".txt", ".adoc", ".org", ".tex", ".html", ".htm"}
DATA_SUFFIXES = {".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
DOCUMENT_SUFFIXES = {".pptx", ".docx"}
CODE_SUFFIXES = {".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt",
                 ".swift", ".rb", ".php", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".scala", ".sh", ".bash",
                 ".zsh", ".sql", ".r", ".jl", ".lua", ".ex", ".exs", ".vue", ".svelte", ".css", ".scss"}
PROSE_NAMES = {"readme", "changelog", "changes", "history", "notes", "authors", "contributing", "license"}
LOCKFILES = {"uv.lock", "poetry.lock", "pipfile.lock", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
             "pnpm-lock.yaml", "cargo.lock", "gemfile.lock", "go.sum", "composer.lock", "bun.lockb"}
# Outside a git repository there is no list of tracked files; skip what never is one.
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".nox", "dist",
             "build", "target", ".next", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".vscode"}
MAX_FILE_BYTES = 256_000  # per file, in a directory; a file named on its own has no limit
CODE_WEIGHT = 0.5         # a code passage's rank against an equally relevant prose one
TABLE_SUFFIXES = {".csv", ".tsv"}
_SENTENCE_END = re.compile(r"[.!?](?=\s)|\n\s*\n")

# A period longer than a quarter but shorter than a year, and a year, in a column's header or a claim.
HALF_PERIOD = re.compile(r"\b(?:six|nine|6|9)[\s-]+months?\b|\b(?:first|second)[\s-]+half\b|\b[12]?h[12]\b"
                         r"|\byear[\s-]+to[\s-]+date\b|\bytd\b", re.I)
ANNUAL_PERIOD = re.compile(r"\b(?:twelve|12)[\s-]+months?\b|\bfull[\s-]+year\b|\bfiscal\s+(?:year|\d{2,4})\b"
                           r"|\bfy\s?\d{2,4}\b|\byears?\s+ended\b|\bannual\b(?!\s+recurring)", re.I)
QUARTER_PERIOD = re.compile(r"\b(?:three|3)[\s-]+months?\b|\bquarter\b|\bq[1-4]\b", re.I)
_YEAR = re.compile(r"(?:19|20)\d\d")
_YEARS = re.compile(r"\b(?:19|20)\d\d\b")
_FISCAL = re.compile(r"\bFY\s?'?(\d{4}|\d{2})\b", re.I)
_QUARTER_NUMBER = re.compile(r"\bQ([1-4])\b|\b(first|second|third|fourth)[\s-]+quarter\b", re.I)


def _years(text: str) -> set[int]:
    """The years `text` names, "FY25" and "FY2025" among them."""
    fiscal = {int(y) + 2000 if len(y) == 2 else int(y) for y in _FISCAL.findall(text)}
    return {int(y) for y in _YEARS.findall(text)} | fiscal


def _quarter_numbers(text: str) -> set[int]:
    order = {"first": 1, "second": 2, "third": 3, "fourth": 4}
    return {int(q) if q else order[w.lower()] for q, w in _QUARTER_NUMBER.findall(text)}
_MONTH = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}\b", re.I)
# A claim about the year a year-end balance opens: "entering 2025" is December 31, 2024.
_ENTERING = re.compile(r"\b(?:entering|enter(?:ed)?|heading into|going into|start(?:ing)? of|beginning of|began)\b",
                       re.I)
_YEAR_END = re.compile(r"\b(?:Dec(?:ember|\.)?\s+(?:31,?\s+)?|year[\s-]+end\s+)((?:19|20)\d\d)\b", re.I)
# A claim that sets a figure against an earlier one may cite the year before the one it names.
_EARLIER = re.compile(r"\bcompared\b|\bversus\b|\bvs\.?\s|\byear[\s-]+ago\b|\bprior[\s-]+year\b"
                      r"|\b(?:grew|grow|growth|rose|fell|increased?|decreased?|declined?|change[ds]?)\b"
                      r"|\b(?:up|down|from)\s+(?:from\s+)?[$€£\d]"
                      r"|\blast\s+year\b|\ba\s+year\s+(?:earlier|before)\b|\byear[\s-]+over[\s-]+year\b|\byoy\b",
                      re.I)
_DASHES = re.compile(r"[-:\u2013\u2014]+")
_SEPARATOR = re.compile(r"\|?(?:\s*:?-{3,}:?\s*\|)+\s*(?::?-{3,}:?)?\s*\|?")


@dataclass(frozen=True)
class Cell:
    """A number's place in a table: its row's label, its column's headers and the section row above it."""
    start: int
    end: int
    label: str
    column: str
    section: str = ""


def _parts(line: str, offset: int) -> list[tuple[int, int, str]]:
    """A row's cells as (start, end, text), empty cells and lone currency signs dropped."""
    out, at = [], 0
    for piece in line.split("|"):
        text = piece.strip()
        if text and text not in ("$", "€", "£"):
            start = offset + at + piece.find(text)
            out.append((start, start + len(text), text))
        at += len(piece) + 1
    return out


_REFERENCE = re.compile(r"\b(?:exhibit|item|note|section|rule|form|page|no\.)\s*$", re.I)


def _reference(sentence: str, q: numbers.Quantity) -> bool:
    """Whether a number names a document rather than an amount: "Exhibit 4.1", "Form 8-K"."""
    return bool(_REFERENCE.search(sentence[:q.start]) or re.match(r"-[A-Z]\b", sentence[q.end:]))


def _worded(sentence: str, q: numbers.Quantity) -> bool:
    """Whether a word names a number on a barred line: in its own cell ("prior week: 4,390") or as its row's
    label ("signups | 4,812 | 4,390")."""
    cell = sentence[sentence.rfind("|", 0, q.start) + 1:q.start] + " " + sentence[q.end:].split("|", 1)[0]
    line = sentence[sentence.rfind("\n", 0, q.start) + 1:]
    label = next((c for c in line.split("|") if c.strip()), "")
    return any(re.search(r"[A-Za-z]{3,}", t) for t in (cell, label))


def _slots(line: str) -> list[str]:
    """A row's cells in place, empty ones kept and lone currency signs dropped."""
    return [t for t in (piece.strip() for piece in line.split("|")) if t not in ("$", "€", "£")]


def _filled(slots: list[str]) -> list[str]:
    """A header row's cells with each heading carried over the empty cells after it, as a spanning
    heading ("Three Months Ended | | |") covers them."""
    out, last = [], ""
    for text in slots:
        last = text or last
        out.append(text or last)
    return out


def _columns(headers: list[tuple[list[str], list[str]]], row: list[str], places: list[int]) -> list[str]:
    """The header text above each value cell, at `places` among the row's cells `row`. A header row laid
    out like the row names each cell's column by place; else one with as many headings as values names
    one each, one with one more names the label column first, one with fewer spans them evenly ("Three
    Months Ended | Six Months Ended" over four), and any other is skipped."""
    n = len(places)
    out = [""] * n
    for slots, cells in headers:
        if len(slots) == len(row):
            texts = [slots[i] for i in places]
        else:
            if len(cells) == n + 1:
                cells = cells[1:]
            if not cells or n % len(cells):
                continue
            texts = [cells[i // (n // len(cells))] for i in range(n)]
        out = [f"{a} {b}".strip() for a, b in zip(out, texts, strict=True)]
    return out


def table_cells(text: str) -> list[Cell]:
    """Every number filling a cell after the first in each `a | b | c` row of `text`, with its row's
    label and the headers above its column. Only a statement's rows count: those under a unit note
    or a period header, or with two numbers or more. A blank line ends a table."""
    out: list[Cell] = []
    headers: list[tuple[list[str], list[str]]] = []
    in_data, stated, pos, section = False, False, 0, ""
    pending: list[Cell] = []  # a bare headerless row's cells, kept once a second row shows it is a table
    noted = False  # the last line was a unit note: a blank line before its table keeps it
    for line in text.split("\n"):
        start, pos = pos, pos + len(line) + 1
        if not line.strip():
            headers, in_data, stated, section, pending = [], False, stated and noted, "", []
            continue
        if _SEPARATOR.fullmatch(line.strip()):
            continue  # "|---|:---:|" only rules a markdown table
        row = "|" in line
        parts = _parts(line, start) if row else [(start, pos - 1, line.strip())]
        values = [c for c in parts[1:] if numbers.VALUE_CELL.fullmatch(c[2])] if row else []
        noted = bool(numbers.UNIT_NOTE.search(line)) and not values
        if noted:
            stated = True
        short = row or len(line) <= numbers.PROSE_LINE
        years = bool(values) and all(_YEAR.fullmatch(t) for _, _, t in values) and not in_data
        named = [t for _, _, t in parts if not _DASHES.fullmatch(t)]
        if (not values and short and len(named) == 1 and (row or named[0].endswith(":"))
                and not numbers.PERIOD_HEADER.search(line) and not numbers.UNIT_NOTE.search(line)):
            section = named[0].rstrip(":")  # "Operating income:" heads the rows below it
            continue
        if values and not years:
            in_data = True
            if stated or len(values) > 1 and 2 * len(values) >= len(parts) - 1:
                row_slots = _slots(line)
                places, at = [], 0
                for _, _, t in values:
                    at = row_slots.index(t, at)
                    places.append(at)
                    at += 1
                columns = _columns(headers, row_slots, places)
                found = [Cell(a, b, parts[0][2], c, section) for (a, b, _), c in zip(values, columns, strict=True)]
                if stated or headers or pending or re.search(r"[$€£%]", line):
                    out += pending + found
                    pending = []
                else:
                    pending = found
        elif short and (numbers.PERIOD_HEADER.search(line) or (row and not in_data)):
            if in_data:
                headers, in_data = [], False
            stated = stated or bool(numbers.PERIOD_HEADER.search(line))
            cells = [t for _, _, t in parts if not numbers.UNIT_NOTE.search(t) and not _DASHES.fullmatch(t)]
            if cells:
                slots = ["" if numbers.UNIT_NOTE.search(t) or _DASHES.fullmatch(t) else t for t in _slots(line)]
                headers = [*headers[-3:], (_filled(slots), cells)]  # a table's header is a few rows at most
    return out


SUFFIXES = ("ings", "ing", "ions", "ion", "ment", "ees", "ee", "es", "ed", "s", "e")
# Words a claim and a row label use for one thing.
SYNONYMS = {"sales": "revenue", "turnover": "revenue", "eps": "pershare", "headcount": "employee",
            "staff": "employee", "workforce": "employee", "attrition": "churn", "profit": "income",
            "earnings": "income", "clients": "customer", "client": "customer"}
# Abbreviations written out, so "NRR" and "net revenue retention" share their words.
EXPANSIONS = [(re.compile(p, re.I), r) for p, r in (
    (r"\bper\s+(?:common\s+|diluted\s+|basic\s+)?share\b", "pershare"), (r"\bR&D\b", "research development"),
    (r"\bSG&A\b", "selling general administrative"), (r"\bNRR\b", "net revenue retention"),
    (r"\bARR\b", "annual recurring revenue"), (r"\bcapex\b", "capital expenditures"),
    (r"\bopex\b", "operating expenses"), (r"\bCOGS\b", "cost goods sold"),
    (r"\bCAC\b", "customer acquisition cost"), (r"\bLTV\b", "lifetime value"), (r"\bAOV\b", "average order value"),
    (r"\bMAUs?\b", "monthly active users"), (r"\bDAUs?\b", "daily active users"),
    (r"\bGMV\b", "gross merchandise value"), (r"\bFCF\b", "free cash flow"),
    (r"\bARPU\b", "average revenue per user"),
    (r"\bas\s+an?\s+(?:%|percent(?:age)?)\s+of\s+(?:total\s+|net\s+)*(?:revenues?|sales)\b", " margin"),
    (r"%\s+of\s+(?:total\s+|net\s+)*(?:revenues?|sales)\b", "% margin"))]


def _expand(text: str) -> str:
    for pattern, words in EXPANSIONS:
        text = pattern.sub(words, text)
    return text


def _stem(word: str) -> str:
    """Enough of a stem that a row label and a claim agree: "operating", "operations" -> "operat"."""
    if word.endswith("ies") and len(word) >= 7:
        return word[:-3] + "y"
    return next((word[:-len(s)] for s in SUFFIXES if word.endswith(s) and len(word) - len(s) >= 4), word)


def _stems(text: str) -> set[str]:
    return {_stem(SYNONYMS.get(w, w)) for w in _named(_expand(text))}


# Words every row label and claim in a filing shares, which say nothing about what a number is.
GENERIC = {_stem(w) for w in (
    "total", "net", "period", "other", "end", "ended", "beginning", "three", "six", "nine", "twelve", "month", "year",
    "ago", "prior", "current", "compared", "million", "thousand", "billion", "percent", "quarter", "per", "including",
    "related", "data", "except", "change", "amount", "variance", "difference", "growth", "value", "metric", "actual",
    "reported", "yoy", "basic", "diluted")}


def _subject(column: str) -> set[str]:
    """The words of a column's headers that name what it holds, not when."""
    words = {w for w in _named(_expand(column)) if not numbers.PERIOD_HEADER.search(w)
             and not re.fullmatch(numbers.MONTH, w, re.I)}
    return {_stem(SYNONYMS.get(w, w)) for w in words}


_FISCAL_YEAR = re.compile(r"(?<=\bQ[1-4])\s*(?:fy\s?|fiscal\s+)'?\d{2,4}\b", re.I)


def _spans(text: str) -> set[str]:
    """The lengths of period `text` names: a half or year to date, a quarter, a year. A fiscal year
    beside a quarter ("Q3 FY2025") only says which year the quarter is in."""
    text = _FISCAL_YEAR.sub(" ", text)
    return {name for name, pattern in (("half", HALF_PERIOD), ("quarter", QUARTER_PERIOD),
                                       ("annual", ANNUAL_PERIOD)) if pattern.search(text)}


def same_period(column: str, claim: str, prose: bool = False) -> bool:
    """A six-month column states nothing about a claim that does not name the half; a year's column or a
    quarter's nothing about a claim that names other periods only. A column's year must be one the claim
    names, the year before when the claim compares, or a later one (the claim names only the year it
    compares with: "down from $47.6M in Q2 2024"). A year-end balance column opens the next year for a
    claim about entering it."""
    spans, said = _spans(column), _spans(claim)
    span = next((s for s in ("half", "quarter", "annual") if s in spans), "")
    if prose:  # a sentence may name several periods: it rules out only a claim naming none of them
        if spans == {"half"} and "half" not in said or spans and said and not spans & said:
            return False
    elif span == "half" and "half" not in said or span in ("annual", "quarter") and said and span not in said:
        return False
    quarters, asked = _quarter_numbers(column), _quarter_numbers(claim)
    if quarters and asked and not quarters & asked and not _EARLIER.search(claim):
        return False  # "Q3" is no Q2 column
    years, named = _years(column), _years(claim)
    if _ENTERING.search(claim) and not _MONTH.search(claim) and not re.search(r"\bended\b|\bmonths\b", column, re.I):
        years |= {int(y) + 1 for y in _YEAR_END.findall(column)}
    if not years or not named:
        return True
    if _EARLIER.search(claim):
        named |= {y - 1 for y in named}
    return bool(years & named) or min(years) > max(named)


def fits(cell: Cell, claim: str) -> bool:
    """A table number is about the claim only if its row label or column header shares a word with
    it, in its period; a word every filing uses ("total", "net") counts only in a label that has no
    other ("Other, net"); the section row above ("Operating income:") names a row whose label says no more
    ("Total").
    A cell with no words to go by is judged by its period alone."""
    names, said = _stems(cell.label) | _subject(cell.column), _stems(claim)
    if not names and not cell.section:
        return same_period(cell.column, claim)
    about = bool((names - GENERIC or names) & said)
    if not about and not names - GENERIC:
        about = bool(_stems(cell.section) - GENERIC & said)
    return about and same_period(cell.column, claim)


@dataclass
class Passage:
    source: str
    text: str
    kind: str = "prose"  # "prose" | "code" | "table"
    strict: bool = False  # read from a directory: a number needs context to count
    header: str = ""      # a table's first line, context for every row
    plain: bool = False   # a chart's values, one per line, printed with no format
    unit: str = ""        # the currency and scale that chart names for all of them ("$B")
    quantities: list[numbers.Quantity] = field(default_factory=list)
    derived: list[numbers.Quantity] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)
    # Per derived value: the cells it came from, or the sentence when it came from prose.
    derived_from: list[list[Cell] | str] = field(default_factory=list)

    def __post_init__(self):
        self.quantities = numbers.find(self.text, self.plain, self.unit)
        if self.kind == "code":
            return  # pairs of numbers in code license no arithmetic
        self.quantities += numbers.scaled(self.text, self.quantities)
        self.cells = table_cells(self.text)
        offset = 0
        for sentence in numbers.sentences(self.text):
            at = self.text.find(sentence, offset)
            offset = at + len(sentence)
            found = numbers.find(sentence, self.plain, self.unit)
            by_end = {q.end: q for q in found}
            for d in numbers.derived(found):
                cells = [c for e in d.operands if (c := self.cell(at + e - 1)) is not None]
                operands = [by_end[e] for e in d.operands]
                if any(_reference(sentence, q) for q in operands):
                    continue  # "Exhibit 4.1" and "Form 8-K" are names, not amounts
                if (len(cells) < len(d.operands) and "|" in sentence[min(d.operands):max(d.operands)]
                        and not all(_worded(sentence, q) for q in operands)):
                    continue  # across a barred line's cells, only figures their own words name
                self.derived.append(d)
                self.derived_from.append(cells if len(cells) == len(d.operands) else sentence)

    @cached_property
    def _starts(self) -> list[int]:
        return [c.start for c in self.cells]

    def cell(self, at: int) -> Cell | None:
        """The table cell holding position `at`, if any."""
        i = bisect.bisect_right(self._starts, at) - 1
        return self.cells[i] if i >= 0 and at < self.cells[i].end else None

    def can_derive(self, index: int, claim_text: str) -> bool:
        """Whether derived value `index` may support a claim: from table cells, each must fit it;
        from prose, the sentence must cover the claim's period."""
        source = self.derived_from[index]
        if isinstance(source, str):
            return same_period(source, claim_text, prose=True)
        return all(fits(c, claim_text) for c in source)

    @cached_property
    def quote_key(self) -> str:
        return _quote_key(self.text)

    def can_support(self, quantity: numbers.Quantity, claim_text: str) -> bool:
        """Whether this passage's `quantity` may support a claim. In a table row, only if the
        row's label shares a word with the claim and its column's period is the claim's, whatever
        the passage's strictness. Else, in
        a named file, always; in code, or anything read from a directory, only if the number's
        context -- its line in code and tables, its sentence in prose -- shares a word with the claim."""
        if (cell := self.cell(quantity.end - 1)) is not None:
            if not fits(cell, claim_text):
                return False
            if _stems(cell.label) | _subject(cell.column) or cell.section:
                return True  # its label or header shared a word; one with no words is held to its line's
        if not (self.strict or self.kind == "code"):
            return True
        return bool(_named(self._context(quantity)) & _named(claim_text))

    def _context(self, quantity: numbers.Quantity) -> str:
        text, a, b = self.text, quantity.start, quantity.end
        if self.kind in ("code", "table") or self.plain:
            start = text.rfind("\n", 0, a) + 1
            end = text.find("\n", b)
            return f"{self.header} {text[start:end if end >= 0 else len(text)]}"
        bounds = [m.end() for m in _SENTENCE_END.finditer(text, 0, a)]
        stop = _SENTENCE_END.search(text, b)
        return text[bounds[-1] if bounds else 0:stop.start() if stop else len(text)]


@dataclass
class Evidence:
    passages: list[Passage] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, source: str, text: str, kind: str = "prose", strict: bool = False, plain: bool = False,
            unit: str = "") -> None:
        header = text.split("\n", 1)[0] if kind == "table" else ""
        for i, chunk in enumerate(_chunks(text)):
            name = f"{source}#{i + 1}" if i else source
            self.passages.append(Passage(name, chunk, kind, strict, header, plain, unit))
        self.__dict__.pop("_index", None)

    def __bool__(self) -> bool:
        return bool(self.passages)

    def contains(self, quote: str) -> Passage | None:
        """The passage holding `quote` word for word.

        Words and numbers must match exactly and in order; case, whitespace,
        punctuation and markup don't count. A judge quoting a markdown source
        drops its `**bold**` and backticks, and quoting a sentence it often
        drops a comma -- rejecting those as fabricated quotes turned four
        supported claims into "unsupported" on one deck.
        """
        needle = _quote_key(quote)
        if not needle.strip():
            return None
        for p in self.passages:
            if needle in p.quote_key:
                return p
        return None

    def relevant(self, query: str, k: int = 6) -> list[Passage]:
        """The `k` passages most relevant to `query`, by BM25 with code weighted
        below prose -- enough context for a judge without sending everything.

        BM25 rather than shared-word counts: across a repository, a common word
        ("model", "test") matches thousands of passages and says little; a rare
        one says a lot. And a README paragraph should beat the code that
        implements it when both match equally.
        """
        terms = set(_tokens(query))
        index = self._index
        scored = []
        for p, (tf, length) in zip(self.passages, index["docs"], strict=True):
            score = 0.0
            for t in terms & tf.keys():
                n = tf[t]
                score += index["idf"][t] * n * 2.2 / (n + 1.2 * (0.25 + 0.75 * length / index["avg"]))
            scored.append(score * (CODE_WEIGHT if p.kind == "code" else 1.0))
        order = sorted(range(len(self.passages)), key=lambda i: -scored[i])
        return [self.passages[i] for i in order[:k] if scored[i] > 0] or [self.passages[i] for i in order[:k]]

    @cached_property
    def _index(self) -> dict:
        docs = [(Counter(_tokens(p.text)), 0) for p in self.passages]
        docs = [(tf, sum(tf.values())) for tf, _ in docs]
        df = Counter(t for tf, _ in docs for t in tf)
        n = len(docs)
        return {"docs": docs, "avg": max(1.0, sum(d for _, d in docs) / max(1, n)),
                "idf": {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}}


STOP = set("the a an and or of to in on for with by at is are was were be been this that these those it its as "
           "from our we you they their has have had not but".split())


def _tokens(text: str) -> list[str]:
    """Words for ranking: identifiers split (`maxTurns`, `max_turns` -> max, turns)."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return [w.strip(".") for w in _words(text) if w.strip(".")]


def _named(text: str) -> set[str]:
    """The words of `text` that name something (no numbers): what a line of code and a
    claim must share before a number in one supports the other."""
    return {w for w in _tokens(text) if any(c.isalpha() for c in w) and len(w) > 2}


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9$%.]+", text.lower()) if w not in STOP and len(w) > 1]


# A number keeps its separators and units ("$2.4B", "1,200", "38%"); a word its
# apostrophe ("don't"). Everything else -- punctuation, markdown, quote marks --
# is not part of what a quote says.
_QUOTE_TOKEN = re.compile(r"\$?\d+(?:[.,:]\d+)*%?|[a-z]+(?:'[a-z]+)*")


def _quote_key(text: str) -> str:
    """Text as a space-delimited word sequence, for whole-word containment."""
    text = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    return " " + " ".join(_QUOTE_TOKEN.findall(text)) + " "


def _is_note(paragraph: str) -> bool:
    return len(paragraph) < 200 and "|" not in paragraph and bool(numbers.UNIT_NOTE.search(paragraph))


def _chunks(text: str, size: int = 1500) -> list[str]:
    """Paragraph-aligned chunks, so a judge sees whole passages."""
    paragraphs: list[str] = []
    for p in (p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()):
        if paragraphs and _is_note(paragraphs[-1]):
            paragraphs[-1] = f"{paragraphs[-1]}\n\n{p}"  # a unit note stays with the table below it
        else:
            paragraphs.append(p)
    chunks, current = [], ""
    for p in paragraphs:
        if current and len(current) + len(p) > size:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{p}" if current else p
    return chunks + ([current] if current else [])


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [] if value is None or isinstance(value, bool) else [str(value)]


def _block_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content
                         if not (isinstance(b, dict) and b.get("type") == "image"))
    return ""


def _is_reminder(text: str) -> bool:
    return text.lstrip().startswith("<system-reminder>")


def add_transcript(evidence: Evidence, lines: list[dict], source: str, exclude_tools=LOCAL_TOOLS) -> None:
    """A Messages-API-shaped session log: one {"role", "content"} per line."""
    tool_names = {}
    for line in lines:
        content = line.get("content")
        if line.get("role") == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_names[block.get("id")] = block.get("name")
    for n, line in enumerate(lines, start=1):
        if line.get("role") != "user":
            continue
        content = line.get("content")
        if isinstance(content, str):
            if not _is_reminder(content):
                evidence.add(f"{source}:user:{n}", content)
            continue
        for block in content or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                name = tool_names.get(block.get("tool_use_id"), "tool")
                if name in exclude_tools or block.get("is_error"):
                    continue
                text = _block_text(block.get("content"))
                if text.strip():
                    evidence.add(f"{source}:{name}:{n}", text)
            elif block.get("type") == "text" and not _is_reminder(block.get("text", "")):
                evidence.add(f"{source}:user:{n}", block["text"])


def load(paths=(), texts=(), exclude_tools=LOCAL_TOOLS, exclude=()) -> Evidence:
    """Evidence from files, directories and inline texts. `exclude` holds globs, matched
    against paths inside a directory (`tests/*`, `*.csv`)."""
    evidence = Evidence()
    for i, text in enumerate(texts, start=1):
        evidence.add(f"text:{i}", text)
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            _load_directory(evidence, path, exclude_tools, exclude)
        else:
            _load_file(evidence, path, path.name, exclude_tools, path.read_text)
    return evidence


def _load_file(evidence: Evidence, path: Path, source: str, exclude_tools, read, strict: bool = False) -> None:
    from onus import documents

    def add(src, text, kind="prose"):
        evidence.add(src, text, kind, strict)

    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        lines = [json.loads(line) for line in read().splitlines() if line.strip()]
        if lines and all(isinstance(x, dict) and "role" in x for x in lines):
            add_transcript(evidence, lines, source, exclude_tools)
        else:
            add(source, "\n\n".join(s for x in lines for s in _strings(x)))
    elif suffix == ".json":
        add(source, "\n\n".join(_strings(json.loads(read()))))
    elif suffix in DOCUMENT_SUFFIXES:
        doc = documents.load(path)
        add(source, "\n\n".join(b.text for b in doc.blocks if b.origin != "chart"))
        for label in dict.fromkeys(b.label for b in doc.blocks if b.origin == "chart"):  # one passage per chart
            chart = [b for b in doc.blocks if b.origin == "chart" and b.label == label]
            units = {b.unit for b in chart if b.plain}  # a passage holds one unit; series that differ keep none
            evidence.add(f"{source}:{label}", "\n".join(b.text for b in chart), "prose", strict, plain=True,
                         unit=units.pop() if len(units) == 1 else "")
    elif suffix == ".csv":  # rows as "a | b | c", so a comma-separated figure is read as a cell
        text = read().replace("\r\n", "\n").replace("\r", "\n")
        try:
            add(source, "\n".join(" | ".join(row) for row in csv.reader(io.StringIO(text))), "table")
        except csv.Error:  # a field too long for the csv module, or no CSV at all: read it as it stands
            add(source, text, "table")
    else:
        add(source, read(), "code" if suffix in CODE_SUFFIXES else "table" if suffix in TABLE_SUFFIXES else "prose")


def _listed_files(root: Path) -> tuple[list[str], bool]:
    """Paths under `root`, relative to it: git's tracked files when `root` is in a
    repository (so .gitignore'd output never counts), else a walk that skips the
    usual tool and environment folders. Returns (paths, from_git)."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=60)
        if out.returncode == 0:
            return sorted(p for p in out.stdout.decode("utf-8", "replace").split("\0") if p), True
    except (OSError, subprocess.SubprocessError):
        pass
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        found += [Path(dirpath, f).relative_to(root).as_posix() for f in filenames]
    return sorted(found), False


def _skip_reason(rel: str, path: Path, exclude) -> str | None:
    name = path.name.lower()
    if any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(rel, g.rstrip("/") + "/*") for g in exclude):
        return "excluded"
    if name in LOCKFILES:
        return "lockfiles"
    suffix = path.suffix.lower()
    known = PROSE_SUFFIXES | DATA_SUFFIXES | DOCUMENT_SUFFIXES | CODE_SUFFIXES
    if not (suffix in known or (not suffix and name in PROSE_NAMES)) or name.endswith((".min.js", ".min.css")):
        return "other types"
    if not path.is_file():
        return "other types"  # a submodule, a dangling link
    if path.stat().st_size > MAX_FILE_BYTES and suffix not in DOCUMENT_SUFFIXES:
        return "too large"
    return None


def _load_directory(evidence: Evidence, root: Path, exclude_tools, exclude) -> None:
    listed, from_git = _listed_files(root)
    label = root.resolve().name
    loaded = 0
    skipped: Counter[str] = Counter()
    for rel in listed:
        path = root / rel
        reason, text = _skip_reason(rel, path, exclude), ""
        if reason is None and path.suffix.lower() not in DOCUMENT_SUFFIXES:
            data = path.read_bytes()
            if b"\0" in data[:8192]:
                reason = "binary"
            else:
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    reason = "binary"
        if reason:
            skipped[reason] += 1
            continue
        try:
            _load_file(evidence, path, f"{label}/{rel}", exclude_tools, lambda text=text: text, strict=True)
        except Exception as e:  # one malformed file shouldn't sink a repository
            skipped["unreadable"] += 1
            evidence.notes.append(f"evidence: skipped {label}/{rel} ({type(e).__name__}: {e})")
            continue
        loaded += 1
    what = "tracked files" if from_git else "files"
    detail = ", ".join(f"{n} {r}" for r, n in sorted(skipped.items(), key=lambda x: (-x[1], x[0])))
    evidence.notes.append(f"evidence: {loaded} {what} from {label}/" + (f" (skipped {detail})" if detail else ""))
