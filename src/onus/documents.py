"""Read the document being checked into blocks: one per text frame,
paragraph, or table cell, each with a location a reader can find."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# A block that is only a short number is a page number or a list marker.
_BARE_NUMBER = re.compile(r"^\s*\d{1,3}\s*$")


@dataclass(frozen=True)
class Block:
    id: int
    label: str  # "slide 3", "paragraph 12", "line 40"
    text: str


@dataclass
class Document:
    path: Path
    format: str
    blocks: list[Block]

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


def _pptx(path: Path) -> list[tuple[str, str]]:
    from pptx import Presentation

    out = []
    for n, slide in enumerate(Presentation(str(path)).slides, start=1):
        for shape in slide.shapes:
            frames = []
            if shape.has_text_frame:
                frames = [shape.text_frame]
            elif getattr(shape, "has_table", False) and shape.has_table:
                frames = [c.text_frame for row in shape.table.rows for c in row.cells]
            for frame in frames:
                for paragraph in frame.text.split("\n"):
                    out.append((f"slide {n}", paragraph))
    return out


def _docx(path: Path) -> list[tuple[str, str]]:
    import docx

    d = docx.Document(str(path))
    out = [(f"paragraph {i}", p.text) for i, p in enumerate(d.paragraphs, start=1)]
    for t, table in enumerate(d.tables, start=1):
        for row in table.rows:
            for cell in row.cells:
                out.append((f"table {t}", cell.text))
    return out


def _text(path: Path) -> list[tuple[str, str]]:
    return [(f"line {i}", line) for i, line in enumerate(path.read_text().splitlines(), start=1)]


READERS = {".pptx": _pptx, ".docx": _docx, ".md": _text, ".txt": _text, ".markdown": _text}


def load(path: str | Path) -> Document:
    path = Path(path)
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        raise ValueError(f"{path.name}: unsupported format (onus reads {', '.join(sorted(READERS))})")
    blocks: list[Block] = []
    seen: set[tuple[str, str]] = set()
    for label, text in reader(path):
        text = " ".join(text.split())
        if not text or _BARE_NUMBER.match(text):
            continue
        key = (label, text)
        if key in seen:  # a slide repeats its eyebrow, a table its header
            continue
        seen.add(key)
        blocks.append(Block(len(blocks), label, text))
    return Document(path, path.suffix.lower().lstrip("."), blocks)
