from pathlib import Path

import yaml

from cardioaggregate.fulltext import extract_body
from cardioaggregate.llm import PicoSummary, TriageItem
from cardioaggregate.pubmed import build_editorial_query, build_research_query, parse_articles
from cardioaggregate.render import render_digest
from cardioaggregate.select import heuristic_triage, passes, select

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text())
RULES = CONFIG["selection"]


def load():
    return parse_articles((FIXTURES / "sample.xml").read_bytes())


def triage(pmid="1", design="rct", impact=3, n=None, relevant=True):
    return TriageItem(pmid=pmid, cardiology_relevant=relevant, design=design,
                      sample_size=n, impact=impact, reason="test")


def test_parse_article_fields():
    trial, editorial = load()
    assert trial.pmid == "40000001"
    assert trial.title == "Drug X in Heart Failure with Preserved Ejection Fraction."
    assert trial.journal_abbr == "N Engl J Med"
    assert trial.doi == "10.1056/NEJMoa0000001"
    assert trial.pmcid == "PMC9999999"
    assert trial.pub_date == "2026-09-29"
    assert trial.abstract.startswith("BACKGROUND: Unknown benefit.")
    assert trial.author_line == "Smith J et al."
    assert trial.comment_in == ["40000002"]
    assert not trial.is_editorial

    assert editorial.is_editorial
    assert editorial.comment_on == ["40000001"]
    assert editorial.pub_date == "2026-10-02"
    assert editorial.url == "https://doi.org/10.1056/NEJMe0000002"


def test_queries_cover_every_journal():
    q = build_research_query(CONFIG["journals"])
    for j in CONFIG["journals"]:
        assert f'"{j["abbr"]}"[ta]' in q
    assert "hasabstract" in q and 'NOT ("Case Reports"[pt]' in q
    assert q.count("(") == q.count(")")
    eq = build_editorial_query(CONFIG["journals"])
    assert '"Editorial"[pt]' in eq and eq.count("(") == eq.count(")")


def test_selection_rules():
    assert passes(triage(design="rct", impact=3), RULES)
    assert not passes(triage(design="rct", impact=2), RULES)
    assert not passes(triage(design="rct_secondary", impact=3), RULES)
    assert passes(triage(design="rct_secondary", impact=4), RULES)
    assert passes(triage(design="observational", impact=3, n=50_000), RULES)
    assert not passes(triage(design="observational", impact=4, n=2_000), RULES)
    assert not passes(triage(design="rct", impact=5, relevant=False), RULES)
    assert not passes(triage(design="other", impact=5), RULES)


def test_select_orders_by_impact_and_caps():
    trial, _ = load()
    arts = []
    for i in range(30):
        a = parse_articles((FIXTURES / "sample.xml").read_bytes())[0]
        a.pmid = str(i)
        arts.append(a)
    tri = {str(i): triage(pmid=str(i), impact=3 + (i % 3)) for i in range(30)}
    chosen, rest = select(arts, tri, RULES)
    assert len(chosen) == RULES["max_articles"]
    assert chosen[0][1].impact == 5
    assert len(rest) == 5


def test_heuristic_triage():
    trial, _ = load()
    item = heuristic_triage(trial)
    assert item.design == "rct"
    assert item.sample_size == 6012


def test_extract_body_skips_references():
    text = extract_body((FIXTURES / "europepmc.xml").read_bytes())
    assert "Patients were randomized." in text
    assert "## Results" in text
    assert "Ref" not in text


def test_render_digest():
    trial, editorial = load()
    summary = PicoSummary(
        bottom_line="Drug X cut events by 20%.",
        design="RCT", population="HFpEF", intervention="Drug X", comparison="Placebo",
        outcomes="HR 0.80 (0.71 to 0.90)", key_results=["No safety signal"],
        strengths=["Large"], limitations=["Short follow-up"],
        editorial_take=None, practice_implications="Consider drug X.",
    )
    entries = [{"article": trial, "triage": triage(pmid=trial.pmid, n=6012), "summary": summary,
                "editorials": [editorial], "basis": "abstract", "error": None}]
    html = render_digest("2026-10-04", entries, [], [], 12, True)
    assert "Drug X cut events by 20%." in html
    assert "A New Option for HFpEF?" in html
    assert "n = 6,012" in html
    assert "Randomized trials" in html
    assert "<i>" not in html  # titles are plain text, markup is escaped or stripped


def test_end_to_end_without_llm(tmp_path, monkeypatch):
    from cardioaggregate import __main__ as app
    from cardioaggregate.pubmed import PubMed

    articles = {a.pmid: a for a in load()}
    monkeypatch.setattr(PubMed, "search_recent", lambda self, term, days, today=None: list(articles))
    monkeypatch.setattr(PubMed, "fetch", lambda self, ids: [articles[i] for i in dict.fromkeys(ids)])
    cfg = dict(CONFIG, output_dir=str(tmp_path / "docs"), history_file=str(tmp_path / "history.json"))
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))

    assert app.main(["--config", str(cfg_path), "--no-llm", "--no-email"]) == 0
    digest = next((tmp_path / "docs" / "digests").glob("*.html")).read_text()
    assert "Drug X in Heart Failure" in digest
    assert "A New Option for HFpEF?" in digest  # editorial linked to its trial
    assert "digests/" in (tmp_path / "docs" / "index.html").read_text()

    # Second run: the trial is remembered, so nothing is repeated.
    assert app.main(["--config", str(cfg_path), "--no-llm", "--no-email"]) == 0
    digest = next((tmp_path / "docs" / "digests").glob("*.html")).read_text()
    assert "Nothing met the selection rules" in digest
