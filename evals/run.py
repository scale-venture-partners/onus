"""Score onus against labelled cases from real agent runs.

    uv run python evals/run.py              # numbers only, offline
    uv run python evals/run.py --model      # the full pipeline (needs a key)

For each case: every `expect` entry must be flagged with its code (recall),
and no finding may land on a `clean` claim (false positives). `model` entries
and `clean_with_model` claims are scored only when the model runs. Prints a
table and exits 1 if numbers-only recall is below 100% or anything clean was
flagged -- the deterministic layer is the part that gets to refuse an answer,
so it is held to that.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from onus import evidence as evidence_mod  # noqa: E402
from onus.engine import check  # noqa: E402
from onus.settings import Settings  # noqa: E402


def _norm(s: str) -> str:
    return " ".join(s.split()).lower().strip(" .,;:")


def _same(label: str, snippet: str) -> bool:
    """A model's span may be shorter or longer than the label; overlap is the claim."""
    a, b = _norm(label), _norm(snippet)
    return a in b or b in a


def score_case(case: dict, use_model: bool, model=None) -> dict:
    evidence = evidence_mod.load([HERE / p for p in case["evidence"]])
    result = check(HERE / case["document"], evidence, Settings(llm=use_model), model=model)
    findings = result.findings

    def hit(expect):
        return any(f.code == expect["code"] and _same(expect["text"], f.snippet)
                   and (not expect.get("number") or expect["number"] in f.message) for f in findings)

    expected = [e for e in case["expect"] if use_model or not e.get("model")]
    found = [e for e in expected if hit(e)]
    clean = case["clean"] + (case.get("clean_with_model", []) if use_model else [])
    # A false positive is a flag on text that sits inside a clean claim; a flagged sentence that merely
    # contains a clean number ("1.4x ... over 18 months") is a hit on the fabrication next to it.
    false_positives = [(f.code, f.snippet) for f in findings if any(_norm(f.snippet) in _norm(c) for c in clean)]
    return {"name": case["name"], "expected": len(expected), "found": len(found),
            "missed": [e["text"] for e in expected if e not in found], "false_positives": false_positives,
            "findings": len(findings), "claims": len(result.claims)}


def run(use_model: bool, model=None, cases_file: Path = HERE / "cases.json") -> list[dict]:
    cases = json.loads(cases_file.read_text())["cases"]
    return [score_case(c, use_model, model) for c in cases]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", nargs="?", const="anthropic:claude-sonnet-5-5", default=None,
                   help="run the full pipeline, optionally naming the model")
    args = p.parse_args(argv)
    rows = run(args.model is not None, args.model)
    print(f"{'case':<38} {'recall':>8} {'false +':>8} {'claims':>7}")
    for r in rows:
        print(f"{r['name']:<38} {r['found']:>3}/{r['expected']:<4} {len(r['false_positives']):>8} {r['claims']:>7}")
        for m in r["missed"]:
            print(f"    missed: {m}")
        for code, text in r["false_positives"]:
            print(f"    false positive: {code} {text}")
    det_ok = all(not r["false_positives"] for r in rows) and (
        args.model is not None or all(r["found"] == r["expected"] for r in rows))
    return 0 if det_ok else 1


if __name__ == "__main__":
    sys.exit(main())
