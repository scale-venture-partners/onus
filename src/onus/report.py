"""Findings as text or JSON, and the fact-check report: every claim, its
status, and where its support came from."""

from __future__ import annotations

import json
import os
import sys
from collections import Counter

from onus.engine import Result

_COLOR = {"error": "\033[31m", "warning": "\033[33m", "info": "\033[36m"}
_DIM, _BOLD, _RESET = "\033[2m", "\033[1m", "\033[0m"


def render_text(results: list[Result], stream=None, summary: bool = True) -> None:
    stream = stream or sys.stdout
    color = stream.isatty() and "NO_COLOR" not in os.environ
    dim, bold, reset = (_DIM, _BOLD, _RESET) if color else ("", "", "")
    counts: Counter = Counter()
    for r in results:
        for note in r.notes:
            print(f"{dim}{r.path}: {note}{reset}", file=stream)
        for f in r.sorted():
            counts[f.code] += 1
            code = f"{_COLOR.get(f.severity, '')}{f.code}{reset}" if color else f.code
            conf = f" p={f.confidence:.2f}" if f.confidence is not None else ""
            print(f"{bold}{r.path}{reset}:{f.label}: {code} {f.message}{dim}{conf}{reset}", file=stream)
            print(f"    {dim}{f.snippet}{reset}", file=stream)
    if summary:
        claims = sum(len(r.claims) for r in results)
        total = sum(counts.values())
        mode = "numbers + model" if any(r.used_model for r in results) else "numbers only"
        if not total:
            print(f"All {claims} claims supported ({mode}).", file=stream)
        else:
            top = ", ".join(f"{c} ×{n}" for c, n in counts.most_common())
            print(f"\n{total} finding{'s' if total != 1 else ''} across {claims} claims ({top}; {mode}).",
                  file=stream)


def _claim(c) -> dict:
    return {"location": c.label, "text": c.text, "kind": c.kind, "subject": c.subject, "found_by": c.source,
            "status": c.status, "numbers": [q.raw for q in c.quantities],
            "evidence": {"source": c.evidence_source, "quote": c.quote} if c.evidence_source else None,
            "derivations": c.derivations, "reason": c.reason or None, "confidence": c.confidence}


def as_dict(r: Result) -> dict:
    return {
        "path": str(r.path),
        "evidence_passages": r.evidence_passages,
        "used_model": r.used_model,
        "notes": r.notes,
        "findings": [{"code": f.code, "message": f.message, "label": f.label, "snippet": f.snippet,
                      "severity": f.severity, "confidence": f.confidence, "evidence": f.evidence}
                     for f in r.sorted()],
        "claims": [_claim(c) for c in r.claims],
    }


def render_json(results: list[Result], stream=None) -> None:
    stream = stream or sys.stdout
    json.dump([as_dict(r) for r in results], stream, indent=2)
    stream.write("\n")
