"""Read the document being checked into blocks: one per text frame,
paragraph, table cell or chart value, each with a location a reader can find.
Pictures are collected alongside, for the model to transcribe (images.py)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

from onus import numbers

# A block that is only a short number is a page number or a list marker.
_BARE_NUMBER = re.compile(r"^\s*\d{1,3}\s*$")
_MEDIA_ALIASES = {"image/jpg": "image/jpeg", "image/x-png": "image/png"}
# Alt text a generator fills with the file name says nothing about the picture.
_FILE_NAME = re.compile(r"[\w .()-]+\.(?:png|jpe?g|gif|webp|svg|emf|bmp|tiff?)", re.I)


@dataclass(frozen=True)
class Block:
    id: int
    label: str  # "slide 3", "paragraph 12", "line 40"
    text: str
    origin: str = "text"  # "text" | "chart" | "image": a transcription is only as good as the model's reading
    plain: bool = False  # ends in a chart value printed with no format: a magnitude, no unit
    unit: str = ""  # the currency and scale that value's chart or table names ("$B"), if it names one
    cells: bool = False  # a table row or cell: a bare number filling a cell is money in `unit`
    context: str = ""  # a table's caption and header row, which say what its numbers are about


@dataclass(frozen=True)
class Figure:
    label: str  # the slide or paragraph it sits on, as a Block's label
    ordinal: int
    data: bytes
    media_type: str
    alt: str = ""

    @property
    def location(self) -> str:
        return f"{self.label} (image {self.ordinal})"


def _media(content_type: str) -> str:
    return _MEDIA_ALIASES.get(content_type.lower(), content_type.lower())


def _alt(text: str) -> str:
    text = " ".join(text.split())
    if _FILE_NAME.fullmatch(text) or "automatically generated" in text.lower():
        return ""
    return text


@dataclass
class Document:
    path: Path
    format: str
    blocks: list[Block]
    figures: list[Figure] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)

    def extend(self, items: Iterable[Block]) -> None:
        """Add blocks, numbered on from the last, with their text normalised and a repeat dropped."""
        seen = {(b.label, b.text) for b in self.blocks}
        for block in items:
            text = " ".join(block.text.split())
            if not text or _BARE_NUMBER.match(text) and not block.context:  # a page number, not a table cell
                continue
            if (block.label, text) in seen:  # a slide repeats its eyebrow, a table its header
                continue
            seen.add((block.label, text))
            self.blocks.append(replace(block, id=len(self.blocks), text=text))


_YEAR = re.compile(r"(?:19|20)\d\d")
_RANK = re.compile(r"#|no\.?|rank(?:ing)?|position|pos\.?", re.I)
_DASHES = re.compile(r"[-:\s]*")


# What a value cell may hold besides its number: a scale or a unit of change.
_VALUE_WORDS = {"k", "m", "mm", "b", "bn", "t", "thousand", "million", "billion", "x", "bp", "bps", "pt", "pts", "pp"}


def _is_value(cell: str) -> bool:
    """Whether a cell states a figure ("$75.0M", "(26.0)", "-8.6%") rather than names one ("Month 1", "Top 10",
    "12/31/2024")."""
    found = [q for q in numbers.find(cell) if not q.temporal]
    if not found or re.search(r"\d\s*/\s*\d", cell):
        return False
    rest = cell
    for q in reversed(found):
        rest = rest[:q.start] + " " + rest[q.end:]
    return all(w.lower() in _VALUE_WORDS for w in re.findall(r"[A-Za-z]+", rest))


def _unit_of(text: str) -> tuple[str, float] | None:
    """The money `text` says a table's numbers are in: "($M)", "USD millions", "(In thousands)"."""
    if (read := numbers.stated_unit(text)) is not None:
        return read
    if (note := numbers.UNIT_NOTE.search(text)) is not None:
        return "$", numbers.SCALE[note.group(1).lower()]
    return None


def _table_unit(caption: str, header: list[str]) -> str:
    """The one unit a table's caption and header cells name, as a unit string; none if they name several."""
    found = {u for t in [caption, *header] if (u := _unit_of(t)) is not None}
    if len(found) != 1:
        return ""
    symbol, scale = found.pop()
    return f"{symbol} {next(k for k, v in numbers.SCALE.items() if v == scale and len(k) > 2)}"


def _row_unit(label: str, table_unit: str) -> str:
    if _unit_of(label) is not None:
        return _table_unit("", [label])
    return "" if numbers.NOT_MONEY.search(label) else table_unit


def _table(label: str, rows: list[list[str]], caption: str) -> list[Block]:
    """A pptx or docx table: the header row and caption say what every cell below is about and in."""
    if not rows:
        return []
    header = rows[0]
    unit = _table_unit(caption, header)
    context = " ".join([caption, *header]).strip()
    out = [Block(0, label, cell, context=context) for cell in header]
    ranks = {i for i, h in enumerate(header) if _RANK.fullmatch(h.strip()) or i == 0 and not h.strip()}
    money = [not numbers.COUNTED.search(h) or _unit_of(h) is not None for h in header]
    for row in rows[1:]:
        row_unit = _row_unit(row[0], unit) if row else ""
        out += [Block(0, label, cell, unit=row_unit if i >= len(money) or money[i] else "",
                      cells=bool(row_unit) and i > 0 and (i >= len(money) or money[i]),
                      context=f"{context} {row[0]}" if i else context)
                for i, cell in enumerate(row) if not (i in ranks and _BARE_NUMBER.match(cell))]
    return out


def _text_tables(lines: list[str]) -> list[Block]:
    """Lines as blocks; a row of an `a | b | c` table carries the unit and the period headers its
    caption and header rows state. A table ends at a blank line or a line of prose; its caption is
    the short lines just above it, or the heading above the blank line before it."""
    blocks: list[Block] = []
    lead: list[str] = []  # short lines since the last blank line, before a table starts
    before_blank: list[str] = []
    header: list[str] = []
    columns: list[str] = []  # the last header row's cells, in place
    caption, in_table, in_data = "", False, False
    for i, line in enumerate(lines, start=1):
        text = line.strip()
        row = "|" in text
        if not text or (not row and len(text) > numbers.PROSE_LINE):
            before_blank = lead[-1:] if not text else []
            lead, header, columns, in_table, in_data = [], [], [], False, False
            blocks.append(Block(0, f"line {i}", line))
            continue
        cells = [c.strip() for c in text.strip("|").split("|")] if row else [text]
        values = [c for c in cells[1:] if _is_value(c)]
        if row and not in_table:
            in_table, caption, lead = True, " ".join(lead or before_blank), []
        if not in_table:
            lead.append(text)
            blocks.append(Block(0, f"line {i}", line))
            continue
        if not values or (not in_data and all(_YEAR.fullmatch(v) for v in values)):
            if numbers.PERIOD_HEADER.search(text) or row:
                header += [c for c in cells if c]
            if row and not all(_DASHES.fullmatch(c) for c in cells):
                columns = cells
            blocks.append(Block(0, f"line {i}", line, context=caption))
            continue
        in_data = True
        mixed = any(numbers.COUNTED.search(h) and _unit_of(h) is None and numbers.VALUE_CELL.fullmatch(c)
                    and "%" not in c for h, c in zip(columns[1:], cells[1:], strict=False))
        unit = "" if mixed else _row_unit(cells[0], _table_unit(caption, header))
        blocks.append(Block(0, f"line {i}", line, unit=unit, cells=bool(unit),
                            context=" ".join([caption, *header]).strip()))
    return blocks


def _shapes(shapes):
    from pptx.shapes.group import GroupShape

    for shape in shapes:
        if isinstance(shape, GroupShape):
            yield from _shapes(shape.shapes)
        else:
            yield shape


def _pptx(path: Path) -> tuple[list[Block], list[Figure]]:
    from pptx import Presentation
    from pptx.shapes.picture import Picture

    from onus import charts

    texts: list[Block] = []
    figures: list[Figure] = []
    for n, slide in enumerate(Presentation(str(path)).slides, start=1):
        pictures = graphs = 0
        title = slide.shapes.title.text_frame.text if slide.shapes.title is not None else ""
        for shape in _shapes(slide.shapes):
            if getattr(shape, "has_chart", False) and shape.has_chart:
                graphs += 1
                where = f"slide {n} (chart {graphs})"
                texts += [Block(0, where, text, "chart", plain, unit)
                          for _, text, plain, unit in charts.blocks(shape.chart, where)]
                continue
            if isinstance(shape, Picture):
                pictures += 1
                # python-pptx's Image wrapper decodes the bytes to name their type; the part already knows it
                rId = shape._element.blip_rId
                if rId is None:  # a linked picture has no bytes in the file
                    continue
                try:
                    part = shape.part.related_part(rId)
                except (KeyError, ValueError):  # a dangling or external relationship
                    continue
                alt = next(iter(shape._element.xpath("./p:nvPicPr/p:cNvPr/@descr")), "")
                figures.append(Figure(f"slide {n}", pictures, part.blob, _media(part.content_type), _alt(alt)))
                continue
            if getattr(shape, "has_table", False) and shape.has_table:
                rows = [[c.text_frame.text for c in row.cells] for row in shape.table.rows]
                texts += [replace(b, text=p) for b in _table(f"slide {n}", rows, title) for p in b.text.split("\n")]
            elif shape.has_text_frame:
                for paragraph in shape.text_frame.text.split("\n"):
                    texts.append(Block(0, f"slide {n}", paragraph))
    return texts, figures


def _docx(path: Path) -> tuple[list[Block], list[Figure]]:
    import docx

    d = docx.Document(str(path))
    texts: list[Block] = []
    figures: list[Figure] = []

    def pictures(label: str, scope) -> None:
        for n, pic in enumerate(scope.xpath(".//pic:pic[not(ancestor::*[local-name()='Fallback'])]"), start=1):
            rids = pic.xpath("./pic:blipFill/a:blip/@r:embed")
            part = d.part.related_parts.get(rids[0]) if rids else None
            if part is None:  # a linked picture has no bytes in the file
                continue
            described = (pic.xpath("./pic:nvPicPr/pic:cNvPr/@descr")
                         or pic.xpath("ancestor::w:drawing[1]//wp:docPr/@descr"))
            alt = _alt(next(iter(described), ""))
            figures.append(Figure(label, n, part.blob, _media(part.content_type), alt))

    for i, p in enumerate(d.paragraphs, start=1):
        texts.append(Block(0, f"paragraph {i}", p.text))
        pictures(f"paragraph {i}", p._p)
    for t, table in enumerate(d.tables, start=1):
        before = table._tbl.getprevious()
        caption = "".join(before.xpath(".//w:t/text()")) if before is not None and before.tag.endswith("}p") else ""
        texts += _table(f"table {t}", [[cell.text for cell in row.cells] for row in table.rows], caption)
        pictures(f"table {t}", table._tbl)  # once per table: a merged cell is listed per column
    return texts, figures


def _text(path: Path) -> tuple[list[Block], list[Figure]]:
    return _text_tables(path.read_text().splitlines()), []


READERS = {".pptx": _pptx, ".docx": _docx, ".md": _text, ".txt": _text, ".markdown": _text}


def load(path: str | Path) -> Document:
    path = Path(path)
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        raise ValueError(f"{path.name}: unsupported format (onus reads {', '.join(sorted(READERS))})")
    texts, figures = reader(path)
    doc = Document(path, path.suffix.lower().lstrip("."), [], figures)
    doc.extend(texts)
    return doc
