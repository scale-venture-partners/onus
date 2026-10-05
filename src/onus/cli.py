"""The `onus` command.

Exit codes follow riff and ruff: 0 every claim supported, 1 findings, 2 a
usage or file error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path

from onus import __version__
from onus import evidence as evidence_mod
from onus import settings as settings_mod
from onus.engine import check
from onus.report import as_dict, render_json, render_text
from onus.rules import RULES


def _codes(value):
    return tuple(c.strip().upper() for c in value.split(",") if c.strip()) if value else ()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="onus",
        description="A claim checker. Finds the verifiable claims in a deck or document and checks each one "
                    "against the evidence: numbers and dates deterministically, everything else with a "
                    "model that has to quote its source.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              onus deck.pptx --evidence facts.md
              onus deck.pptx --evidence session.jsonl         # an agent transcript
              onus deck.pptx --evidence ~/src/app             # a repository, for a deck about it
              onus memo.docx --evidence data.json --no-llm    # numbers only, offline
              onus deck.pptx --evidence facts.md --report claims.json
              onus --list-rules
        """),
    )
    p.add_argument("paths", nargs="*", help="documents to check (.pptx .docx .md .txt)")
    p.add_argument("-e", "--evidence", action="append", default=[], metavar="PATH",
                   help="evidence: .md .txt .json .jsonl (incl. agent transcripts) .pptx .docx, or a directory "
                        "(a git repo: its tracked text files); repeatable")
    p.add_argument("--evidence-exclude", action="append", default=[], metavar="GLOB",
                   help="skip these paths inside an evidence directory (e.g. 'tests/*'); repeatable")
    p.add_argument("--evidence-text", action="append", default=[], metavar="TEXT", help="evidence given inline")
    p.add_argument("--no-llm", action="store_true", help="numbers and dates only; no model calls")
    p.add_argument("--model", help="pydantic-ai model for extraction and judging")
    p.add_argument("--select", help="only these rule codes/prefixes (comma-separated)")
    p.add_argument("--ignore", help="disable these rule codes/prefixes")
    p.add_argument("--extend-select", help="enable these on top of the defaults/select")
    p.add_argument("--config", type=Path, help="path to an onus.toml or pyproject.toml")
    p.add_argument("--report", type=Path, metavar="FILE", help="write the fact-check report (every claim) as JSON")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("-q", "--quiet", action="store_true", help="findings only, no summary line")
    p.add_argument("--list-rules", action="store_true", help="print the rule catalog and exit")
    p.add_argument("--explain", metavar="CODE", help="print one rule's full explanation and exit")
    p.add_argument("--version", action="version", version=f"onus {__version__}")
    return p


def _missing_key(model: str) -> str | None:
    provider = model.split(":", 1)[0] if ":" in model else ""
    env = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "google-gla": "GEMINI_API_KEY"}.get(provider)
    return env if env and not os.environ.get(env) else None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_rules:
        for code, r in RULES.items():
            mark = " " if r.default else "·"
            print(f"{mark}{code}  {'model' if r.llm else 'exact':<5}  {r.name:<24}  {r.summary}")
        print("\n· off by default    exact: deterministic    model: needs the model")
        return 0
    if args.explain:
        found = RULES.get(args.explain.upper())
        if found is None:
            print(f"onus: no rule {args.explain!r}", file=sys.stderr)
            return 2
        flag = "" if found.default else ", off by default"
        print(f"{found.code} {found.name} ({found.severity}{flag})\n\n{found.summary}.\n")
        print(textwrap.fill(found.explanation, 88))
        return 0
    if not args.paths:
        print("onus: no documents given (try --help)", file=sys.stderr)
        return 2
    if not args.evidence and not args.evidence_text:
        print("onus: nothing to check against -- pass --evidence FILE or --evidence-text TEXT", file=sys.stderr)
        return 2

    for raw in args.evidence:
        if not Path(raw).exists():
            print(f"onus: evidence {raw}: no such file or directory", file=sys.stderr)
            return 2

    results, loaded = [], {}
    for raw in args.paths:
        path = Path(raw)
        if not path.is_file():
            print(f"onus: {raw}: no such file", file=sys.stderr)
            return 2
        try:
            s = settings_mod.load(args.config, start=path.parent)
        except (ValueError, OSError) as e:
            print(f"onus: config: {e}", file=sys.stderr)
            return 2
        s.select = _codes(args.select) or s.select
        s.ignore = s.ignore + _codes(args.ignore)
        s.extend_select = s.extend_select + _codes(args.extend_select)
        s.llm = s.llm and not args.no_llm
        s.model = args.model or s.model
        if s.llm and (env := _missing_key(s.model)):
            print(f"onus: the model rules need {env}. Set it, or pass --no-llm to check numbers and dates "
                  "only.", file=sys.stderr)
            return 2
        try:
            # Once per distinct config: a repository is slow to read and the same for every document.
            key = tuple(s.transcript_exclude_tools)
            if key not in loaded:
                loaded[key] = evidence_mod.load(args.evidence, args.evidence_text, s.transcript_exclude_tools,
                                                exclude=args.evidence_exclude)
            results.append(check(path, loaded[key], s))
        except (ValueError, OSError, json.JSONDecodeError) as e:
            print(f"onus: {raw}: {e}", file=sys.stderr)
            return 2
        except Exception as e:  # noqa: BLE001 -- a document onus can't read is a file error, never "findings"
            # Exit 1 means findings to every caller; an uncaught traceback exits 1 too, so a deck that
            # python-pptx couldn't open read as a deck with findings and an empty report.
            print(f"onus: {raw}: {type(e).__name__}: {e}", file=sys.stderr)
            return 2

    if args.report:
        args.report.write_text(json.dumps([as_dict(r) for r in results], indent=2) + "\n")
    if args.format == "json":
        render_json(results)
    else:
        render_text(results, summary=not args.quiet)
    return 1 if any(r.findings for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
