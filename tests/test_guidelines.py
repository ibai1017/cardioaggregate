import json
from pathlib import Path

import yaml

from cardioaggregate import __main__ as app
from cardioaggregate import run as run_mod
from cardioaggregate.guidelines import build_guideline_query, classify, collect
from cardioaggregate.pubmed import Article, PubMed, parse_articles

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


def art(pmid, title, journal="Eur Heart J", types=("Journal Article",), abstract=""):
    return Article(pmid=pmid, title=title, journal_abbr=journal, pub_types=list(types), abstract=abstract)


# Titles taken from real PubMed records.
ESC_HF = art("1", "2026 ESC Guidelines for the management of heart failure.")
PERIOP_JACC = art("2", "2026 AHA/ACC/ACS/ASNC/HRS/SCA/SCCT/SCMR/SVM Guideline for Perioperative Cardiovascular "
                  "Management for Noncardiac Surgery: A Report of the American College of Cardiology/American "
                  "Heart Association Joint Committee on Clinical Practice Guidelines.",
                  "J Am Coll Cardiol", ("Journal Article", "Practice Guideline"), "Perioperative abstract.")
PERIOP_CIRC = art("3", PERIOP_JACC.title, "Circulation", ("Journal Article", "Practice Guideline"), "Same.")
CCS = art("4", "Canadian Cardiovascular Society/Canadian Society of Cardiovascular Surgery/Canadian Association of "
          "Interventional Cardiology Clinical Practice Update on Transcatheter Mitral and Tricuspid Valve "
          "Intervention: Patient Evaluation and Management.", "Can J Cardiol", abstract="Valve abstract.")
ACC_ECDP = art("5", "Optimizing Blood Pressure Care in Patients With Resistant Hypertension: 2026 ACC Expert "
               "Consensus Decision Pathway: A Report of the American College of Cardiology Solution Set "
               "Oversight Committee.", "J Am Coll Cardiol")
AHA_STATEMENT = art("6", "Primary and Secondary Raynaud: A Scientific Statement From the American Heart Association.",
                    "Circulation", abstract="Raynaud abstract.")


def test_classify_real_titles():
    assert classify(ESC_HF) == "guideline"
    assert classify(PERIOP_JACC) == "guideline"
    assert classify(CCS) == "consensus"
    assert classify(ACC_ECDP) == "consensus"
    assert classify(AHA_STATEMENT) == "statement"


def test_classify_rejects_commentary_corrections_and_other_societies():
    assert classify(art("9", "The 'ten commandments' for the 2025 European Society of Cardiology guidelines on "
                             "myocarditis and pericarditis.")) is None
    assert classify(art("9", "Guidelines on rehabilitation and a focus on lifestyle-related cardiovascular "
                             "prevention.")) is None
    assert classify(art("9", "Correction to: 2026 ESC Guidelines on cardiac rehabilitation.")) is None
    assert classify(art("9", "2026 ESC Guidelines on cardiac rehabilitation.", types=("Published Erratum",))) is None
    assert classify(art("9", "Excimer Laser Coronary Atherectomy (ELCA) Utilization in Complex PCI: A Position Paper "
                             "of the Italian Society of Interventional Cardiology (GISE).")) is None
    assert classify(art("9", "Back to the Basics to BRIDGE Guideline-Directed Care.", types=("Editorial",))) is None


def test_collect_merges_copies_and_skips_seen():
    docs, kinds, also_in = collect([PERIOP_JACC, PERIOP_CIRC, ESC_HF, AHA_STATEMENT], set())
    assert list(docs) == ["2", "1", "6"]
    assert also_in == {"2": ["Circulation"]}
    from cardioaggregate.guidelines import title_key
    docs, _, _ = collect([PERIOP_CIRC, ESC_HF], {title_key(PERIOP_JACC.title)})
    assert list(docs) == ["1"]


def test_query_is_balanced():
    q = build_guideline_query(["Circulation", "Eur Heart J"])
    assert q.count("(") == q.count(")") and '"Eur Heart J"[ta]' in q and "NOT" in q


def test_guidelines_in_weekly_flow(tmp_path, monkeypatch, capsys):
    research = {a.pmid: a for a in parse_articles((FIXTURES / "sample.xml").read_bytes())}
    guideline_docs = {a.pmid: a for a in (ESC_HF, PERIOP_JACC, PERIOP_CIRC, AHA_STATEMENT)}
    everything = {**research, **guideline_docs}

    def search_recent(self, term, days, today=None):
        return list(guideline_docs) if "scientific statement" in term else list(research)

    monkeypatch.setattr(PubMed, "search_recent", search_recent)
    monkeypatch.setattr(PubMed, "fetch", lambda self, ids: [everything[i] for i in dict.fromkeys(ids)])
    monkeypatch.setattr(app, "fetch_full_text", lambda pmcid: "")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    cfg.update(output_dir=str(tmp_path / "docs"), history_file=str(tmp_path / "data" / "history.json"))
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))

    def cli(*args):
        return app.main(["--config", str(cfg_path), *args])

    def write(name, payload):
        (tmp_path / name).write_text(json.dumps(payload))
        return str(tmp_path / name)

    cli("prepare")
    run = run_mod.open_run(cfg)
    assert set(run.guidelines) == {"1", "2", "6"}           # Circulation copy merged
    assert run.guidelines_pending() == ["2", "6"]            # ESC guideline has no abstract

    cli("save-triage", write("t.json", [{"pmid": "40000001", "cardiology_relevant": True, "design": "rct",
                                         "subspecialty": "heart_failure", "sample_size": 6012, "impact": 5,
                                         "reason": "x"}]))
    capsys.readouterr()
    cli("status")
    assert "NEXT: guidelines" in capsys.readouterr().out
    cli("guidelines-batch")
    out = capsys.readouterr().out
    assert "Perioperative abstract." in out and "Raynaud abstract." in out and 'pmid="1"' not in out

    cli("save-guidelines", write("g.json", {"items": [
        {"pmid": "2", "summary": "Perioperative note."}, {"pmid": "6", "summary": "Raynaud note."}]}))
    capsys.readouterr()
    cli("status")
    assert "NEXT: summaries" in capsys.readouterr().out

    from cardioaggregate import emailer
    sent = []
    monkeypatch.setattr(emailer, "send_digest", lambda s, h, t: sent.append((s, h, t)) or True)
    summary = {"pmid": "40000001", "bottom_line": "b", "design": "d", "population": "p", "intervention": "i",
               "comparison": "c", "outcomes": "o", "key_results": [], "strengths": [], "limitations": [],
               "editorial_take": None, "practice_implications": "x"}
    cli("save-summary", write("s.json", summary))
    cli("finalize")

    html = next((tmp_path / "docs" / "digests").glob("*.html")).read_text()
    assert html.index("Guidelines and statements") < html.index("Randomized trials")
    assert "Perioperative note." in html and "also in Circulation" in html
    assert "No abstract in PubMed" in html                   # the ESC guideline
    subject, email_html, text = sent[0]
    assert "3 guideline documents" in subject and "2026 ESC Guidelines for the management of heart failure" in text

    # Next week the Circulation copy alone is not shown again.
    run_mod.latest_run(cfg).path.rename(tmp_path / "data" / "runs" / "2000-01-01.json")
    guideline_docs.pop("2")
    cli("prepare")
    assert set(run_mod.open_run(cfg).guidelines) == set()
