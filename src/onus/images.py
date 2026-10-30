"""Read a document's pictures with the model, so a chart exported as an
image, a KPI tile or a screenshot is checked like the text around it.

A vision model transcribes each picture into lines, one statement per line
("Fare revenue, 2025: $3.1M"), and the lines become blocks of the document
under the picture's location. From there they are ordinary text: the scan
finds their numbers, tier 1 checks them against the evidence, and the
extractor and judge see them too. Only what is printed is written down. A
bar's height read off an axis is an estimate, not a claim, so tier 1 never
sees a number the document did not state.

Every picture goes to the model, which says when there is nothing to check
(a photo, a logo); nothing here judges a picture by its size or looks. The
only ones held back are those the API refuses: a vector format (EMF, SVG),
which would need rendering first, and a file over its size limit. A picture
repeated across slides is read once.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from onus.documents import Block, Document, Figure

MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_IMAGE_BYTES = 3_750_000  # the API's 5 MB per image, before base64 adds a third

TRANSCRIBE_INSTRUCTIONS = """You transcribe a picture from a document so that what it prints can be fact-checked.

Say what the picture is. For a chart, table, KPI tile, infographic, diagram or screenshot, write out what is
printed in it, one statement per line, so that every number stands next to the words that say what it is:
- the title, subtitle, caption, source line and footnotes, each as its own line
- each data label, callout or annotation with its series and category: "Fare revenue, 2025: $3.1M"
- a table's rows one per line, with their column names: "East bank: 24 docks, 88% renewal"
Copy numbers exactly as printed, with their currency, unit and suffix ($3.1M, 42%, 1.4x). Leave out axis
tick values, gridlines and legend swatches, and never read a value off an axis or estimate a bar's height:
a number nobody printed is not a claim. Skip anything you cannot read with confidence.

Text from beside the picture may be given to name what it shows. Do not copy it into the lines. A photo,
logo, icon or decoration carries nothing to check: say so and return no lines."""


class Transcript(BaseModel):
    kind: Literal["chart", "table", "infographic", "text", "none"] = Field(
        description="chart or plot; table; infographic, KPI tile or diagram with figures in it; text or a "
                    "screenshot of text; none for a photo, logo, icon or decoration")
    lines: list[str] = Field(description="What the picture prints, one statement per line, every number next "
                                         "to the words that say what it is")


def _eligible(figures: list[Figure], skipped: Counter) -> dict[str, list[Figure]]:
    """The pictures the API will take, grouped by content so a repeated one is read once."""
    todo: dict[str, list[Figure]] = {}
    for f in figures:
        if f.media_type not in MEDIA_TYPES:
            skipped[f.media_type] += 1
        elif len(f.data) > MAX_IMAGE_BYTES:
            skipped["over the size limit"] += 1
        else:
            todo.setdefault(hashlib.sha1(f.data).hexdigest(), []).append(f)
    return todo


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _prompt(doc: Document, figure: Figure) -> list:
    from pydantic_ai import BinaryContent

    beside = [f"Alt text: {figure.alt}"] if figure.alt else []
    beside += [b.text for b in doc.blocks if b.label == figure.label]
    text = f"{figure.location} of {doc.path.name}."
    if beside:
        text += " Text beside it, to name what it shows (do not copy it):\n" + "\n".join(beside)
    return [BinaryContent(data=figure.data, media_type=figure.media_type), text]


def transcribe(doc: Document, model, concurrency: int = 8) -> list[str]:
    """Add what `doc.figures` print to `doc.blocks`, under each picture's location. Returns notes."""
    from pydantic_ai import Agent

    skipped: Counter[str] = Counter()
    todo = _eligible(doc.figures, skipped)
    notes: list[str] = []
    read = 0
    if todo:
        agent = Agent(model, output_type=Transcript, instructions=TRANSCRIBE_INSTRUCTIONS)

        async def one(figure: Figure, limiter: asyncio.Semaphore):
            async with limiter:
                try:
                    return (await agent.run(_prompt(doc, figure))).output
                except Exception as e:  # noqa: BLE001 -- one unreadable picture shouldn't sink the deck
                    return e

        async def run_all():
            limiter = asyncio.Semaphore(concurrency)
            return await asyncio.gather(*(one(copies[0], limiter) for copies in todo.values()))

        for copies, result in zip(todo.values(), asyncio.run(run_all()), strict=True):
            if isinstance(result, Exception):
                for f in copies:
                    notes.append(f"images: {f.location} not read ({type(result).__name__}: {result})")
            elif result.kind == "none" or not result.lines:
                skipped["nothing to check"] += len(copies)
            else:
                read += len(copies)
                for f in copies:
                    printed = {_norm(b.text) for b in doc.blocks if b.label == f.label}
                    lines = [line for line in result.lines if _norm(line) not in printed]
                    doc.extend(Block(0, f.location, line, "image") for line in lines)
    detail = ", ".join(f"{n} {r}" for r, n in sorted(skipped.items(), key=lambda x: (-x[1], x[0])))
    notes.append(f"images: transcribed {read} of {len(doc.figures)}" + (f" ({detail})" if detail else ""))
    return notes
