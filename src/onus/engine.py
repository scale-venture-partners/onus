"""The pipeline: extract claims, check them, turn failures into findings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from onus import claims as claims_mod
from onus import documents, verify
from onus.evidence import Evidence
from onus.rules import RULES
from onus.settings import Settings


@dataclass
class Finding:
    code: str
    message: str
    label: str
    snippet: str
    severity: str
    confidence: float | None = None
    evidence: str | None = None  # where the deciding passage came from


@dataclass
class Result:
    path: Path
    claims: list[claims_mod.Claim]
    findings: list[Finding]
    evidence_passages: int
    used_model: bool
    notes: list[str] = field(default_factory=list)

    def sorted(self) -> list[Finding]:
        def order(f):
            n = f.label.split()[-1]
            return (int(n) if n.isdigit() else 0, f.code)
        return sorted(self.findings, key=order)


def _finding(code, message, claim, **kw) -> Finding:
    return Finding(code, message, claim.label, claim.text, RULES[code].severity, **kw)


def check(path: str | Path, evidence: Evidence, settings: Settings, model=None) -> Result:
    doc = documents.load(path)
    codes = settings.active_codes()
    use_model = settings.llm  # extraction and the judge; tier 1 runs either way
    model = model or settings.model
    notes = list(evidence.notes)

    if use_model:
        extracted = claims_mod.model_claims(doc, model)
        claims, missed, merge_notes = claims_mod.merge(doc, extracted)
        notes += merge_notes
    else:
        claims, missed = claims_mod.scan_claims(doc), []

    findings = []
    checked = []
    for claim in claims:
        failures = verify.check_numbers(claim, evidence)
        checked.append((claim, failures))

    cleared = set()
    if use_model:
        for claim, verdict in verify.judge(checked, evidence, model, settings.concurrency):
            if verdict.status == "supported":
                cleared.add(claim.id)
            elif verdict.confidence >= settings.judge_threshold and not claim.quantities:
                code = "ONS102" if verdict.status == "contradicted" else "ONS101"
                if code in codes:
                    findings.append(_finding(code, verdict.reason, claim, confidence=verdict.confidence,
                                             evidence=claim.evidence_source))

    for claim, failures in checked:
        if "ONS004" in codes:
            for working in claim.derivations:
                findings.append(_finding("ONS004", f"supported only by arithmetic: {working}", claim))
        for f in failures:
            if f.code == "ONS003" and claim.id in cleared:
                continue
            if f.code in codes:
                what = "date" if f.code == "ONS002" else "count" if f.code == "ONS003" else "number"
                findings.append(_finding(f.code, f"{f.raw}: no {what} like it in the evidence", claim))
    if "ONS201" in codes:
        for claim in missed:
            findings.append(_finding("ONS201", "the extractor missed this number; it was checked anyway", claim))

    return Result(Path(path), claims, findings, len(evidence.passages), use_model, notes)
