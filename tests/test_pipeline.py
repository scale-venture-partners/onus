"""Evidence, claim extraction and the two verification tiers, with the model
replaced by pydantic-ai's TestModel and FunctionModel."""

import json

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from onus import claims, documents, verify
from onus import evidence as ev
from onus.engine import check
from onus.settings import Settings

FACTS = ("Fare revenue $2.4B, up 42% year over year; median repair backlog 1.4x, down from 2.1x; "
         "24 active stations. Plans: east bank, night shuttle, and cargo bikes.")


def doc(tmp_path, text, name="memo.md"):
    path = tmp_path / name
    path.write_text(text)
    return documents.load(path)


def facts():
    e = ev.Evidence()
    e.add("facts.md", FACTS)
    return e


# -- documents ----------------------------------------------------------------

def test_page_numbers_and_markers_are_not_blocks(tmp_path):
    d = doc(tmp_path, "Revenue grew\n\n3\n01\nRevenue grew\n")
    assert [(b.label, b.text) for b in d.blocks] == [("line 1", "Revenue grew"), ("line 5", "Revenue grew")], \
        "two identical lines are two places; only bare numbers go"


def test_decks_are_read_per_slide(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    for text in ("ARR is $2.4B", "NRR is 118%"):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        s.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text_frame.text = text
    prs.save(tmp_path / "d.pptx")
    d = documents.load(tmp_path / "d.pptx")
    assert [(b.label, b.text) for b in d.blocks] == [("slide 1", "ARR is $2.4B"), ("slide 2", "NRR is 118%")]


def test_an_unknown_format_is_an_error(tmp_path):
    (tmp_path / "x.pdf").write_text("")
    try:
        documents.load(tmp_path / "x.pdf")
    except ValueError as e:
        assert "unsupported format" in str(e)
    else:
        raise AssertionError


# -- evidence -----------------------------------------------------------------

def transcript(tmp_path, lines):
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines))
    return path


def test_a_transcript_counts_what_came_in_not_what_the_agent_said(tmp_path):
    path = transcript(tmp_path, [
        {"role": "user", "content": "Facts: ARR $2.4B."},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Eight of 24 stations sell into regulated industries."},
            {"type": "tool_use", "id": "a", "name": "search_crm", "input": {}},
            {"type": "tool_use", "id": "b", "name": "read", "input": {"path": "out/build_deck.py"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "NRR 118% across the network"},
            {"type": "tool_result", "tool_use_id": "b", "content": "deck.cover(title='Eight of 24...')"},
            {"type": "text", "text": "<system-reminder>riff: slide 3 'Eight of our 24'</system-reminder>"},
        ]},
    ])
    e = ev.load([path])
    text = " ".join(p.text for p in e.passages)
    assert "$2.4B" in text and "118%" in text
    assert "Eight" not in text, "the agent's words, its own files read back, and hook reminders are not evidence"
    assert {p.source.split(":")[1] for p in e.passages} == {"user", "search_crm"}


def test_excluded_tools_are_configurable(tmp_path):
    path = transcript(tmp_path, [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "read", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "dataroom: 118%"}]},
    ])
    assert ev.load([path]).passages == []
    assert "118%" in ev.load([path], exclude_tools=()).passages[0].text


def test_json_and_other_files_are_flattened_to_text(tmp_path):
    (tmp_path / "d.json").write_text(json.dumps({"arr": "$2.4B", "rows": [{"nrr": 118}, None, True]}))
    (tmp_path / "g.jsonl").write_text('{"note": "backlog 1.4x"}\n')
    e = ev.load([tmp_path / "d.json", tmp_path / "g.jsonl"], texts=["24 stations"])
    text = " ".join(p.text for p in e.passages)
    assert "$2.4B" in text and "118" in text and "backlog 1.4x" in text and "24 stations" in text


def test_long_evidence_is_chunked_on_paragraphs():
    e = ev.Evidence()
    e.add("big.md", "\n\n".join(f"Paragraph {i} " + "x" * 400 for i in range(10)))
    assert len(e.passages) > 1 and e.passages[1].source == "big.md#2"
    assert all(p.text.startswith("Paragraph") for p in e.passages)


def test_a_quote_is_found_verbatim_or_not_at_all():
    e = facts()
    assert e.contains("median repair backlog 1.4x,   down from 2.1x") is not None
    assert e.contains("repair backlog of 1.4") is None
    assert e.contains("") is None


def test_a_quote_ignores_markup_and_punctuation_but_not_words_or_numbers():
    e = ev.load(texts=["**Run 4 ran out of turns on a bug in the skill.** the linter kept reporting the "
                             "`c.pill()` overflow. The audience is the city's planners, in a workshop. "
                             "ARR grew to $2.4B, up 38%."])
    assert e.contains("Run 4 ran out of turns on a bug in the skill. the linter kept reporting the c.pill() overflow")
    assert e.contains("The audience is the city\u2019s planners in a workshop")
    assert e.contains("ARR grew to $2.4B -- up 38%")
    assert e.contains("ARR grew to $2.5B") is None, "a number must match exactly"
    assert e.contains("ARR grew to $2.4B, up 3%") is None
    assert e.contains("Run 5 ran out of turns") is None
    assert e.contains("ran out of turns on a bug in the skills") is None, "whole words only"
    assert e.contains("in the skill the linter kept reporting"), "across a markup boundary"
    assert e.contains("... ** ``") is None, "punctuation alone quotes nothing"


# -- tier 1 -------------------------------------------------------------------

def scanned(tmp_path, text):
    return claims.scan_claims(doc(tmp_path, text))


def test_stated_numbers_pass_and_invented_ones_fail(tmp_path):
    good, bad = scanned(tmp_path, "ARR is $2.4B, up 42%.\n\nNRR is above 120% since 2023.")
    assert verify.check_numbers(good, facts()) == [] and good.status == "supported"
    assert good.evidence_source == "facts.md" and "$2.4B" in good.quote
    failures = verify.check_numbers(bad, facts())
    assert [(f.raw, f.code) for f in failures] == [("120%", "ONS001"), ("2023", "ONS002")]
    assert bad.status == "unsupported"


def test_arithmetic_on_one_sentence_supports_a_number(tmp_path):
    (c,) = scanned(tmp_path, "Repair turnaround improved 33%.")
    assert verify.check_numbers(c, facts()) == [] and c.status == "derived"
    assert c.derivations == ["33% = 1.4x vs 2.1x in facts.md"], "|1.4 - 2.1| / 2.1"


def test_small_counts_are_their_own_code(tmp_path):
    (c,) = scanned(tmp_path, "Eight of our companies sell to banks.")
    (f,) = verify.check_numbers(c, facts())
    assert f.code == "ONS003"


# -- extraction and merge -----------------------------------------------------

def test_model_spans_must_be_verbatim_and_missed_numbers_are_still_checked(tmp_path):
    d = doc(tmp_path, "ARR reached $2.4B.\n\n"
                      "Customers buy more because onboarding is faster. Churn is 3% and NRR 118%.")
    extracted = [
        claims.ExtractedClaim(block=0, text="ARR reached $2.4B", kind="quantitative", subject="ARR"),
        claims.ExtractedClaim(block=1, text="Customers buy more because onboarding is faster", kind="factual",
                              subject="expansion"),
        claims.ExtractedClaim(block=1, text="Customers love the product", kind="factual", subject="sentiment"),
        claims.ExtractedClaim(block=9, text="NRR 118%", kind="quantitative", subject="NRR"),  # wrong block id
    ]
    out, missed, notes = claims.merge(d, extracted)
    texts = [c.text for c in out]
    assert "Customers love the product" not in texts and "not found verbatim" in notes[0]
    assert "NRR 118%" in texts, "a wrong block id is repaired when the text matches once elsewhere"
    (miss,) = missed
    assert miss.text == "Churn is 3% and NRR 118%." and [q.raw for q in miss.quantities] == ["3%"], \
        "118% sits inside the repaired model claim; 3% is the one the model missed"


def test_one_scan_claim_per_sentence_however_many_numbers(tmp_path):
    d = doc(tmp_path, "Confirm Q3 timing by 15 March.")
    out, missed, _ = claims.merge(d, [])
    assert len(out) == len(missed) == 1 and [q.raw for q in out[0].quantities] == ["Q3", "15 March"]


def test_the_extractor_reads_numbered_blocks(tmp_path):
    seen = {}

    def answer(messages, info: AgentInfo):
        seen["prompt"] = messages[0].parts[-1].content
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"claims": [
            {"block": 0, "text": "ARR reached $2.4B", "kind": "quantitative", "subject": "ARR"}]})])

    out = claims.model_claims(doc(tmp_path, "ARR reached $2.4B."), FunctionModel(answer))
    assert out[0].text == "ARR reached $2.4B" and "[0] (line 1) ARR reached $2.4B." in seen["prompt"]


# -- tier 2 -------------------------------------------------------------------

def verdict(status, quote=None, confidence=0.9):
    return {"status": status, "quote": quote, "reason": "because", "confidence": confidence}


def test_a_verdict_without_a_real_quote_is_discarded(tmp_path):
    d = doc(tmp_path, "Revenue doubled.")
    c = claims.Claim(0, d.blocks[0], "Revenue doubled", "factual", "revenue", "model")
    v = verify.apply(c, verify.Verdict(**verdict("supported", "revenue doubled last year")), facts())
    assert v.status == "unsupported" and "not in the evidence" in v.reason
    v = verify.apply(c, verify.Verdict(**verdict("contradicted", "median repair backlog 1.4x")), facts())
    assert v.status == "contradicted" and c.evidence_source == "facts.md"


def test_the_judge_may_clear_a_small_count_but_never_a_number(tmp_path):
    three, invented = scanned(tmp_path, "We hold three plans.\n\nNRR is above 120%.")
    pairs = [(c, verify.check_numbers(c, facts())) for c in (three, invented)]
    assert verify._judgeable(*pairs[0]) and not verify._judgeable(*pairs[1])
    listed = "Plans: east bank, night shuttle, and cargo bikes"
    model = TestModel(custom_output_args=verdict("supported", listed))
    ((claim, v),) = verify.judge(pairs, facts(), model)
    assert claim is three and v.status == "supported"


def test_the_full_pipeline_with_a_scripted_model(tmp_path):
    (tmp_path / "memo.md").write_text(
        "ARR reached $2.4B.\n\nEvery repair hour clears more than an hour of damage.\n\nWe hold three plans.\n\n"
        "Active as of Q4 2025.")

    def answer(messages, info: AgentInfo):
        tool = info.output_tools[0].name
        prompt = messages[0].parts[-1].content
        if tool.endswith("Extraction") or "Document:" in prompt:
            return ModelResponse(parts=[ToolCallPart(tool, {"claims": [
                {"block": 1, "text": "Every repair hour clears more than an hour of damage", "kind": "factual",
                 "subject": "repair capacity"},
                {"block": 2, "text": "We hold three plans", "kind": "quantitative", "subject": "plans"},
            ]})])
        if "three plans" in prompt:
            return ModelResponse(parts=[ToolCallPart(tool, verdict(
                "supported", "Plans: east bank, night shuttle, and cargo bikes"))])
        return ModelResponse(parts=[ToolCallPart(tool, verdict("contradicted", "median repair backlog 1.4x", 0.95))])

    result = check(tmp_path / "memo.md", facts(), Settings(), model=FunctionModel(answer))
    codes = sorted((f.code, f.label) for f in result.findings)
    assert codes == [("ONS002", "line 7"), ("ONS102", "line 3")]
    assert result.used_model


def test_word_documents_read_paragraphs_and_tables_and_serve_as_evidence(tmp_path):
    import docx

    d = docx.Document()
    d.add_paragraph("ARR reached $2.4B.")
    table = d.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "NRR", "118%"
    d.save(tmp_path / "memo.docx")
    loaded = documents.load(tmp_path / "memo.docx")
    assert [(b.label, b.text) for b in loaded.blocks] == [
        ("paragraph 1", "ARR reached $2.4B."), ("table 1", "NRR"), ("table 1", "118%")]
    e = ev.load([tmp_path / "memo.docx"])
    assert "118%" in e.passages[0].text and e.passages[0].source == "memo.docx"
