"""Turn a document into claims.

Two sources, merged:

  scan   every quantity numbers.find sees, deterministic and complete
  model  a pydantic-ai agent that reads the blocks and returns typed claims:
         the exact span, what kind of claim, what it is about

The model adds what a scan cannot: claims with no number in them ("customers
are buying more, not renewing out of inertia"), and the subject each number
is about. The scan keeps the model honest. A model's span must appear in the
document verbatim or it is dropped, its numbers are re-parsed from that span
rather than taken from the model, and any number the model missed becomes a
claim of its own -- so a missed extraction is still a checked number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from onus import numbers
from onus.documents import Block, Document

EXTRACT_INSTRUCTIONS = """You extract checkable factual claims from a document, for fact-checking.

You get the document as numbered blocks. Return EVERY claim a fact-checker could verify against source
material -- not only the ones with numbers. That includes:
- quantities, including ones written in words ("most of our customers", "every dollar spent returns two")
- dates, periods and sequences ("since the 2021 raise", "before the pivot")
- causes and explanations ("churn fell because onboarding moved in-house")
- comparisons and trends ("the fastest-growing segment", "margins moved with revenue")
- factual statements about companies, customers, markets or people, and attributions to a source

Copy each claim's text EXACTLY as it appears in its block -- a verbatim substring, not a paraphrase -- and
keep it to the shortest span that states the claim. A block can hold several claims; return each.

Do not extract: opinions or theses ("we believe", "the future is"), predictions, recommendations, requests
or asks, headings that assert nothing, contact details, page numbers, or legal boilerplate."""


class ExtractedClaim(BaseModel):
    block: int = Field(description="The block id the claim is in")
    text: str = Field(description="The claim, copied verbatim from the block")
    kind: Literal["quantitative", "temporal", "factual", "attribution"]
    subject: str = Field(description="What the claim is about, in a few words")


class Extraction(BaseModel):
    claims: list[ExtractedClaim]


@dataclass
class Claim:
    id: int
    block: Block
    text: str
    kind: str
    subject: str
    source: str  # "model" | "scan"
    quantities: list[numbers.Quantity] = field(default_factory=list)
    status: str = "unchecked"  # supported | derived | unsupported | contradicted | unchecked
    evidence_source: str | None = None
    quote: str | None = None
    reason: str = ""
    confidence: float | None = None
    derivations: list[str] = field(default_factory=list)  # "33% = 1.4x vs 2.1x in facts.md"

    @property
    def label(self) -> str:
        return self.block.label


def _sentence_around(text: str, start: int, end: int) -> str:
    for sentence in numbers.sentences(text):
        offset = text.find(sentence)
        if offset <= start and end <= offset + len(sentence):
            return sentence.strip()
    return text.strip()


def _locate(span: str, text: str) -> int:
    """Where `span` starts in `text`, tolerating whitespace and quote-mark drift."""
    def norm(s):
        return " ".join(s.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"').split())
    i = norm(text).find(norm(span))
    return i


def scan_claims(doc: Document) -> list[Claim]:
    """One claim per sentence that holds a quantity."""
    claims: list[Claim] = []
    for block in doc.blocks:
        by_sentence: dict[str, list] = {}
        for q in numbers.find(block.text):
            by_sentence.setdefault(_sentence_around(block.text, q.start, q.end), []).append(q)
        for sentence, qs in by_sentence.items():
            kind = "temporal" if all(q.temporal for q in qs) else "quantitative"
            claims.append(Claim(len(claims), block, sentence, kind, "", "scan", numbers.find(sentence)))
    return claims


def merge(doc: Document, extracted: list[ExtractedClaim]) -> tuple[list[Claim], list[Claim], list[str]]:
    """Model claims checked against the document, plus every quantity the
    model missed. Returns (claims, missed, notes)."""
    blocks = {b.id: b for b in doc.blocks}
    claims: list[Claim] = []
    notes: list[str] = []
    covered: dict[int, list[tuple[int, int]]] = {}
    for e in extracted:
        block = blocks.get(e.block)
        start = _locate(e.text, block.text) if block else -1
        if start < 0:
            # Models cite the wrong block more often than they misquote; one
            # exact match elsewhere is a repair, not a guess.
            elsewhere = [b for b in doc.blocks if _locate(e.text, b.text) >= 0]
            if len(elsewhere) > 1 and block is not None:  # prefer a match where the model said it was
                elsewhere = [b for b in elsewhere if b.label == block.label] or elsewhere
            if len(elsewhere) == 1:
                block, start = elsewhere[0], _locate(e.text, elsewhere[0].text)
        if block is None or start < 0:
            notes.append(f"dropped a model claim not found verbatim in the document: {e.text[:60]!r}")
            continue
        span = " ".join(block.text.split())[start:start + len(" ".join(e.text.split()))]
        covered.setdefault(block.id, []).append((start, start + len(span)))
        claims.append(Claim(len(claims), block, span, e.kind, e.subject, "model", numbers.find(span)))

    missed = []
    for block in doc.blocks:
        spans = covered.get(block.id, [])
        text = " ".join(block.text.split())
        by_sentence: dict[str, list] = {}
        for q in numbers.find(text):
            if not any(s <= q.start and q.end <= e for s, e in spans):
                by_sentence.setdefault(_sentence_around(text, q.start, q.end), []).append(q)
        for sentence, qs in by_sentence.items():  # one claim per sentence, however many numbers it holds
            kind = "temporal" if all(q.temporal for q in qs) else "quantitative"
            claim = Claim(len(claims), block, sentence, kind, "", "scan", qs)
            claims.append(claim)
            missed.append(claim)
    return claims, missed, notes


def model_claims(doc: Document, model, batch: int = 60) -> list[ExtractedClaim]:
    from pydantic_ai import Agent

    agent = Agent(model, output_type=Extraction, instructions=EXTRACT_INSTRUCTIONS)
    out = []
    for i in range(0, len(doc.blocks), batch):
        chunk = doc.blocks[i:i + batch]
        listing = "\n".join(f"[{b.id}] ({b.label}) {b.text}" for b in chunk)
        out += agent.run_sync(f"Document: {doc.path.name}\n\n{listing}").output.claims
    return out
