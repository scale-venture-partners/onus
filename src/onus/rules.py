"""The rule catalog. onus checks claims in one pipeline rather than one rule at
a time, so a rule here is a code, what it means, and whether it is on by
default; the pipeline (verify.py) decides which code a failed check earns.

  ONS0xx  numbers and dates, checked deterministically against the evidence
  ONS1xx  claims a model judged, with the passage it relied on
  ONS2xx  the checker checking itself
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    code: str
    name: str
    summary: str
    explanation: str
    severity: str = "error"
    default: bool = True
    llm: bool = False  # needs the model


RULES = {r.code: r for r in [
    Rule("ONS001", "unsupported-number", "A number the evidence does not contain or imply",
         "Money, percentages, multiples and counts are parsed from the text and matched against every quantity "
         "in the evidence at the precision they were written with, and against changes and ratios between "
         "quantities stated in one evidence sentence. A model cannot overrule this check."),
    Rule("ONS002", "unsupported-date", "A year, quarter, date or duration the evidence does not contain",
         "Dates are where invented specificity hides: 'in 2023', 'as of Q4 2025', 'over 18 months'. Checked "
         "exactly; a model cannot overrule it."),
    Rule("ONS003", "unsupported-small-count", "A small count (12 or under) the evidence does not state",
         "Small counts are often counts of things the evidence lists without numbering ('three theses'), "
         "which no string match can verify, so they are reported apart from ONS001. With the model on, a "
         "judge that cites the passage can clear one.", severity="warning"),
    Rule("ONS004", "derived-number", "A number supported only by arithmetic on the evidence",
         "'Down 33%' from '1.4x, down from 2.1x' is honest arithmetic, but a reader cannot see it. Off by default; "
         "turn it on to list every number that needs its working shown.", severity="info", default=False),
    Rule("ONS101", "unsupported-claim", "A factual claim the evidence does not support",
         "A model judged the claim against the most relevant evidence passages and found nothing that states or "
         "implies it.", severity="warning", llm=True),
    Rule("ONS102", "contradicted-claim", "A claim the evidence contradicts",
         "The judge must quote the contradicting passage, and the quote must appear in the evidence verbatim, "
         "or the verdict is discarded.", llm=True),
    Rule("ONS201", "unextracted-number", "A number the claim extractor missed",
         "Every number in the document is found by a deterministic scan, so a number the model failed to "
         "extract is still checked. This reports the miss itself -- a measure of the extractor, for evals.",
         severity="info", default=False, llm=True),
]}


def active(select=(), ignore=(), extend_select=(), llm=True) -> set[str]:
    def matches(code, patterns):
        return any(code == p or code.startswith(p) for p in patterns)

    chosen = set()
    for code, rule in RULES.items():
        on = matches(code, select) if select else rule.default
        if matches(code, extend_select):
            on = True
        if matches(code, ignore) or (rule.llm and not llm):
            on = False
        if on:
            chosen.add(code)
    return chosen
