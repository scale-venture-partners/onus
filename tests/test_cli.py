"""The command line, configuration, and the labelled eval as a regression test."""

import json
import sys
from pathlib import Path

import pytest

from onus import cli
from onus import settings as settings_mod
from onus.rules import active

EVALS = Path(__file__).resolve().parent.parent / "evals"


@pytest.fixture
def memo(tmp_path):
    (tmp_path / "memo.md").write_text("ARR reached $2.4B.\n\nActive as of Q4 2025.\n")
    (tmp_path / "facts.md").write_text("ARR $2.4B.")
    return tmp_path


def test_findings_exit_1_with_one_line_each(memo, capsys):
    code = cli.main([str(memo / "memo.md"), "-e", str(memo / "facts.md"), "--no-llm"])
    out = capsys.readouterr().out
    assert code == 1
    assert "memo.md:line 3: ONS002 Q4 2025: no date like it in the evidence" in out
    assert "1 finding across 2 claims (ONS002 ×1; numbers only)." in out


def test_a_supported_document_exits_0(memo, capsys):
    assert cli.main([str(memo / "memo.md"), "--evidence-text", "ARR $2.4B as of Q4 2025", "--no-llm"]) == 0
    assert "All 2 claims supported (numbers only)." in capsys.readouterr().out


def test_json_and_the_fact_check_report(memo, capsys):
    report = memo / "claims.json"
    cli.main([str(memo / "memo.md"), "-e", str(memo / "facts.md"), "--no-llm", "--format", "json",
              "--report", str(report)])
    (out,) = json.loads(capsys.readouterr().out)
    assert [f["code"] for f in out["findings"]] == ["ONS002"]
    (written,) = json.loads(report.read_text())
    arr = next(c for c in written["claims"] if "$2.4B" in c["numbers"])
    assert arr["status"] == "supported" and arr["evidence"]["source"] == "facts.md"


def test_usage_errors_exit_2(memo, capsys, monkeypatch):
    assert cli.main([]) == 2
    assert cli.main([str(memo / "memo.md")]) == 2
    assert "nothing to check against" in capsys.readouterr().err
    assert cli.main([str(memo / "absent.md"), "-e", str(memo / "facts.md")]) == 2
    (memo / "notes.pdf").write_text("")
    assert cli.main([str(memo / "notes.pdf"), "-e", str(memo / "facts.md"), "--no-llm"]) == 2
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli.main([str(memo / "memo.md"), "-e", str(memo / "facts.md")]) == 2
    assert "need ANTHROPIC_API_KEY" in capsys.readouterr().err
    (memo / "onus.toml").write_text("bogus = 1\n")
    assert cli.main([str(memo / "memo.md"), "-e", str(memo / "facts.md"), "--no-llm"]) == 2


def test_list_rules_and_explain(capsys):
    assert cli.main(["--list-rules"]) == 0
    assert "ONS001  exact" in capsys.readouterr().out
    assert cli.main(["--explain", "ons102"]) == 0
    assert "verbatim" in capsys.readouterr().out
    assert cli.main(["--explain", "ONS999"]) == 2


def test_select_and_ignore_follow_ruff_and_no_llm_drops_model_rules():
    assert active() == {"ONS001", "ONS002", "ONS003", "ONS101", "ONS102"}
    assert active(llm=False) == {"ONS001", "ONS002", "ONS003"}
    assert active(select=("ONS0",), extend_select=("ONS201",), ignore=("ONS003",)) == \
        {"ONS001", "ONS002", "ONS004", "ONS201"}


def test_config_from_onus_toml_and_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.onus]\nignore = "ONS003"\nmodel = "anthropic:claude-haiku-4-5"\n')
    s = settings_mod.load(start=tmp_path)
    assert s.ignore == ("ONS003",) and s.model == "anthropic:claude-haiku-4-5"
    (tmp_path / "onus.toml").write_text('judge-threshold = 0.9\n')
    assert settings_mod.load(start=tmp_path).judge_threshold == 0.9
    assert settings_mod.load(start=tmp_path / "nowhere") is not None


def test_the_labelled_eval_holds_without_the_model():
    # The deterministic layer is the one allowed to refuse an answer, so it is
    # held to perfect recall and no false positives on the labelled runs.
    sys.path.insert(0, str(EVALS))
    import run as eval_run

    for row in eval_run.run(use_model=False):
        assert row["found"] == row["expected"], (row["name"], row["missed"])
        assert row["false_positives"] == [], (row["name"], row["false_positives"])
    assert eval_run.main([]) == 0


def test_a_document_that_cannot_be_read_is_a_file_error_not_findings(memo, capsys, monkeypatch):
    # An uncaught exception exits 1, which callers read as "findings" -- with no JSON to show.
    import onus.cli

    def explode(*a, **kw):
        raise KeyError("no content-type for partname '/ppt/media/image74.jpg'")
    monkeypatch.setattr(onus.cli, "check", explode)
    assert cli.main([str(memo / "memo.md"), "-e", str(memo / "facts.md"), "--no-llm"]) == 2
    err = capsys.readouterr().err
    assert "memo.md: KeyError" in err and "image74.jpg" in err
