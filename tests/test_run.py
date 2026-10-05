"""The resumable weekly flow: prepare, triage, summaries, finalize."""

import json
from pathlib import Path

import pytest
import yaml

from cardioaggregate import __main__ as app
from cardioaggregate import run as run_mod
from cardioaggregate.pubmed import PubMed, parse_articles
from cardioaggregate.select import prescore

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"

SUMMARY = {
    "pmid": "40000001",
    "bottom_line": "Drug X cut CV death or HF events by 20%.",
    "design": "RCT", "population": "HFpEF", "intervention": "Drug X", "comparison": "Placebo",
    "outcomes": "HR 0.80 (0.71 to 0.90)", "key_results": [], "strengths": ["Large"],
    "limitations": ["Short"], "editorial_take": None, "practice_implications": "Consider it.",
}
RATING = {"pmid": "40000001", "cardiology_relevant": True, "design": "rct",
          "subspecialty": "heart_failure", "sample_size": 6012, "impact": 5, "reason": "Landmark"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    articles = {a.pmid: a for a in parse_articles((FIXTURES / "sample.xml").read_bytes())}
    monkeypatch.setattr(PubMed, "search_recent", lambda self, term, days, today=None: list(articles))
    monkeypatch.setattr(PubMed, "fetch", lambda self, ids: [articles[i] for i in dict.fromkeys(ids) if i in articles])
    monkeypatch.setattr(app, "fetch_full_text", lambda pmcid: "")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    cfg.update(output_dir=str(tmp_path / "docs"), history_file=str(tmp_path / "data" / "history.json"))
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))

    def cli(*args):
        return app.main(["--config", str(cfg_path), *args])

    def write(name, payload):
        path = tmp_path / name
        path.write_text(json.dumps(payload))
        return str(path)

    return cli, write, cfg, tmp_path


def digest_html(tmp_path):
    return next((tmp_path / "docs" / "digests").glob("*.html")).read_text()


def test_full_routine_flow(env, capsys):
    cli, write, cfg, tmp_path = env

    assert cli("prepare") == 0
    assert "Preliminary" in digest_html(tmp_path)
    cli("status")
    assert "NEXT: triage" in capsys.readouterr().out

    cli("triage-batch")
    out = capsys.readouterr().out
    assert "=== RUBRIC ===" in out and 'pmid="40000001"' in out
    assert 'pmid="40000002"' not in out  # the editorial is not a candidate

    assert cli("save-triage", write("t.json", {"items": [RATING]})) == 0
    out = capsys.readouterr().out
    assert "Triage complete: 1 articles selected" in out and "commit" in out

    cli("summary-next")
    out = capsys.readouterr().out
    assert "Drug X in Heart Failure" in out
    assert "A New Option for HFpEF?" in out  # editorial exists but has no text

    assert cli("save-summary", write("s.json", SUMMARY)) == 0
    cli("status")
    assert "NEXT: finalize" in capsys.readouterr().out

    assert cli("finalize", "--no-email") == 0
    capsys.readouterr()
    html = digest_html(tmp_path)
    assert "Preliminary" not in html
    assert "Drug X cut CV death" in html and "Heart failure" in html
    history = json.loads((tmp_path / "data" / "history.json").read_text())
    assert "40000001" in history["articles"] and "40000002" in history["editorials"]

    cli("status")
    assert capsys.readouterr().out.startswith("DONE")

    # Same day: refuses to overwrite.
    cli("prepare")
    assert "already exists" in capsys.readouterr().out

    # Next week: the same paper is not offered again.
    done = run_mod.latest_run(cfg)
    done.path.rename(done.path.with_name("2000-01-01.json"))
    assert cli("prepare") == 0
    run = run_mod.open_run(cfg)
    assert run.candidates == {}


def test_progress_survives_between_sessions(env):
    cli, write, cfg, _ = env
    cli("prepare")
    cli("save-triage", write("t.json", [RATING]))
    # A new session only sees what is on disk.
    run = run_mod.open_run(cfg)
    assert run.stage == "summaries" and run.selected == ["40000001"]
    assert run.triage["40000001"].impact == 5


def test_invalid_json_is_rejected_and_nothing_saved(env, capsys):
    cli, write, cfg, _ = env
    cli("prepare")
    bad = dict(RATING, impact=9, design="cohort")
    assert cli("save-triage", write("t.json", [bad])) == 1
    assert "Invalid triage JSON" in capsys.readouterr().out
    assert run_mod.open_run(cfg).triage == {}

    cli("save-triage", write("t.json", [RATING]))
    assert cli("save-summary", write("s.json", dict(SUMMARY, pmid="999"))) == 1
    assert cli("save-summary", write("s.json", {"pmid": "40000001", "bottom_line": "x"})) == 1


def test_unfinished_run_is_finalized_with_heuristics(env):
    cli, _, cfg, tmp_path = env
    cli("prepare")  # the routine never runs this week
    first = run_mod.open_run(cfg)
    first.path.rename(first.path.with_name("2000-01-01.json"))  # pretend it was last week

    cli("prepare")
    history = json.loads((tmp_path / "data" / "history.json").read_text())
    # The trial has an editorial, so the free heuristic still picks it.
    assert "40000001" in history["articles"]


def test_api_driver(env, monkeypatch):
    cli, _, cfg, tmp_path = env
    from cardioaggregate import llm
    from cardioaggregate.schemas import PicoSummary, TriageItem

    class FakeClaude:
        def __init__(self, *a):
            pass

        def triage(self, articles):
            return []  # skips everything: heuristic ratings must fill in

        def summarize(self, article, full_text, editorials):
            fields = {k: v for k, v in SUMMARY.items() if k != "pmid"}
            return PicoSummary(**fields)

    monkeypatch.setattr(llm, "Claude", FakeClaude)
    cli("prepare")
    assert cli("api", "--no-email") == 0
    assert "Drug X cut CV death" in digest_html(tmp_path)
    assert run_mod.open_run(cfg) is None


def test_prescore_rewards_editorials_and_flagship_journals():
    trial, _ = parse_articles((FIXTURES / "sample.xml").read_bytes())
    assert prescore(trial, True, 1) > prescore(trial, False, 1) > prescore(trial, False, 2)
