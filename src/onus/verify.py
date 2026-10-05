"""Check each claim against the evidence, deterministic checks first.

Tier 1 -- every quantity in a claim, parsed from its text:
    supported    the evidence states it, at the precision it was written with
    derived      a change or ratio between quantities stated in one evidence
                 sentence produces it
    unsupported  neither

Tier 2 -- a model judges what tier 1 cannot: claims with no number in them,
and small counts of listed things. A verdict must quote the evidence it
relies on, and the quote must appear in the evidence verbatim, or it is
discarded. The judge cannot clear a number, date or large count that tier 1
failed: the model does not get to overrule arithmetic.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from onus.claims import Claim
from onus.evidence import Evidence

SMALL_COUNT = 12

JUDGE_INSTRUCTIONS = """You fact-check one claim against evidence passages.

Answer:
- supported: the evidence states the claim, or it follows by simple arithmetic or counting from what the
  evidence states (for example, three items listed supports "three").
- contradicted: the evidence states something incompatible with the claim, or arithmetic on the evidence
  shows it is false.
- unsupported: the evidence says nothing that settles it. Plausible is not supported.

For supported or contradicted, quote the shortest passage that decides it, copied EXACTLY from the
evidence. Judge only the claim's facts, not its tone or wording."""


class Verdict(BaseModel):
    status: Literal["supported", "contradicted", "unsupported"]
    quote: str | None = Field(default=None, description="Verbatim from the evidence; required unless unsupported")
    reason: str = Field(description="One sentence")
    confidence: float = Field(ge=0, le=1)


@dataclass
class Failure:
    """One quantity in a claim that tier 1 could not support."""
    claim: Claim
    raw: str
    kind: str

    @property
    def code(self) -> str:
        if self.kind in ("year", "quarter", "date", "duration"):
            return "ONS002"
        if self.kind == "count" and self.value <= SMALL_COUNT:
            return "ONS003"
        return "ONS001"

    @property
    def value(self) -> float:
        q = next(q for q in self.claim.quantities if q.raw == self.raw)
        return q.value


def _snippet(text: str, start: int, end: int, pad: int = 60) -> str:
    a, b = max(0, start - pad), min(len(text), end + pad)
    return ("…" if a else "") + text[a:b].strip() + ("…" if b < len(text) else "")


def check_numbers(claim: Claim, evidence: Evidence) -> list[Failure]:
    failures, derived = [], False
    said = f"{claim.subject or ''} {claim.text}"
    for q in claim.quantities:
        hit = next(((p, e) for p in evidence.passages for e in p.quantities
                    if q.matches(e) and p.can_support(e, said)), None)
        if hit:
            if claim.evidence_source is None:
                p, e = hit
                claim.evidence_source, claim.quote = p.source, _snippet(p.text, e.start, e.end)
            continue
        made = next(((p, d) for p in evidence.passages for d in p.derived if q.matches(d)), None)
        if made:
            derived = True
            claim.derivations.append(f"{q.raw} = {made[1].raw} in {made[0].source}")
            continue
        failures.append(Failure(claim, q.raw, q.kind))
    if claim.quantities:
        claim.status = "unsupported" if failures else ("derived" if derived else "supported")
    return failures


def _judgeable(claim: Claim, failures: list[Failure]) -> bool:
    """Tier 2 may look at a claim with no quantities, or one whose only
    failures are small counts."""
    if not claim.quantities:
        return True
    return bool(failures) and all(f.code == "ONS003" for f in failures)


async def _judge_one(agent, claim: Claim, evidence: Evidence, limiter: asyncio.Semaphore) -> Verdict:
    passages = evidence.relevant(f"{claim.subject} {claim.text}")
    listing = "\n\n".join(f"<passage source={p.source!r}>\n{p.text}\n</passage>" for p in passages)
    prompt = (f"Claim ({claim.label}): {claim.text}\n"
              + (f"About: {claim.subject}\n" if claim.subject else "")
              + f"\nEvidence:\n{listing}")
    async with limiter:
        return (await agent.run(prompt)).output


def judge(claims_and_failures: list[tuple[Claim, list[Failure]]], evidence: Evidence, model,
          concurrency: int = 8) -> list[tuple[Claim, Verdict]]:
    from pydantic_ai import Agent

    todo = [(c, f) for c, f in claims_and_failures if _judgeable(c, f)]
    if not todo:
        return []
    agent = Agent(model, output_type=Verdict, instructions=JUDGE_INSTRUCTIONS)

    async def run_all():
        limiter = asyncio.Semaphore(concurrency)
        return await asyncio.gather(*(_judge_one(agent, c, evidence, limiter) for c, _ in todo))

    verdicts = asyncio.run(run_all())
    return [(c, apply(c, v, evidence)) for (c, _), v in zip(todo, verdicts, strict=True)]


def apply(claim: Claim, verdict: Verdict, evidence: Evidence) -> Verdict:
    """Record a verdict on its claim -- after checking its quote is real."""
    if verdict.status != "unsupported":
        passage = evidence.contains(verdict.quote or "")
        if passage is None:
            verdict = Verdict(status="unsupported", quote=None, confidence=verdict.confidence,
                              reason=f"the judge's quote is not in the evidence ({verdict.reason})")
        else:
            claim.evidence_source = passage.source
    claim.status, claim.quote = verdict.status, verdict.quote or claim.quote
    claim.reason, claim.confidence = verdict.reason, verdict.confidence
    return verdict
