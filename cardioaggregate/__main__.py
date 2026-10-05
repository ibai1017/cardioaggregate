"""CardioAggregate command line. See ROUTINE.md for how the weekly routine uses it.

    python -m cardioaggregate prepare            # free: fetch PubMed, preliminary digest
    python -m cardioaggregate status             # what to do next
    python -m cardioaggregate triage-batch       # print the next candidates to rate
    python -m cardioaggregate save-triage FILE   # store ratings (JSON)
    python -m cardioaggregate summary-next       # print the next article to summarize
    python -m cardioaggregate save-summary FILE  # store a PICO summary (JSON)
    python -m cardioaggregate finalize           # free: final digest, history, email
    python -m cardioaggregate api                # do triage and summaries with the API instead
    python -m cardioaggregate check-journals     # confirm journal abbreviations match PubMed
    python -m cardioaggregate email              # email the latest finished digest once
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml
from pydantic import ValidationError

from .fulltext import fetch_full_text
from .pubmed import PubMed
from .run import Run, email_run, finalize, latest_run, open_run, prepare, write_digest
from .schemas import (
    SUMMARY_SYSTEM,
    TRIAGE_SYSTEM,
    PicoSummary,
    TriageItem,
    format_summary_input,
    format_triage_input,
)
from .select import heuristic_triage

COMMIT_HINT = "Now commit data/ and docs/ and push to main, so the progress is saved."

TRIAGE_FORMAT = """Write a JSON file shaped like this, one item per article above:
{"items": [{"pmid": "12345678", "cardiology_relevant": true, "design": "rct",
            "subspecialty": "heart_failure", "sample_size": 4812, "impact": 4,
            "reason": "One short sentence."}]}
design: rct | rct_secondary | meta_analysis | systematic_review | observational | other
  (rct = primary report of a randomized trial; rct_secondary = post hoc, subgroup or
  secondary analysis; observational = cohort, registry, case-control, cross-sectional)
subspecialty: heart_failure | electrophysiology | interventional | structural_valve |
  imaging | prevention_lipids | hypertension | thrombosis_vascular | acute_critical_care |
  cardiac_surgery | congenital | cardio_oncology | general
sample_size: total analysed as stated in the abstract, or null
impact: 1 to 5 (5 practice changing landmark, 4 important, 3 solid and relevant,
  2 incremental, 1 niche)"""

SUMMARY_FORMAT = """Write a JSON file with these keys:
{"pmid": "...", "bottom_line": "...", "design": "...", "population": "...",
 "intervention": "...", "comparison": "...", "outcomes": "...",
 "key_results": ["..."], "strengths": ["..."], "limitations": ["..."],
 "editorial_take": null, "practice_implications": "..."}
intervention means exposure for observational studies. outcomes: primary outcome(s)
with effect estimates, 95% CI and P values exactly as reported. editorial_take: the
editorialist's view if editorial text is included above, otherwise null."""


def load_config(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text())


def require_run(cfg: dict) -> Run:
    run = open_run(cfg)
    if run is None:
        print("No open run. Nothing to do until the next `prepare` (Saturday, on GitHub Actions).")
        sys.exit(0)
    return run


def cmd_status(cfg: dict, _args) -> int:
    run = open_run(cfg)
    if run is None:
        last = latest_run(cfg)
        when = f" (last digest {last.date})" if last else ""
        print(f"DONE: no open run{when}. Nothing to do this week. Stop here.")
        return 0
    stage = run.stage
    print(f"Run {run.date}: {len(run.candidates)} candidates, {len(run.queue)} queued for triage, "
          f"{len(run.triage)} triaged, {len(run.selected)} selected, {len(run.summaries)} summarized.")
    if stage == "triage":
        print(f"NEXT: triage. {len(run.untriaged())} left. "
              "Run `python -m cardioaggregate triage-batch`.")
    elif stage == "summaries":
        print(f"NEXT: summaries. {len(run.unsummarized())} left. "
              "Run `python -m cardioaggregate summary-next`.")
    else:
        print("NEXT: finalize. Run `python -m cardioaggregate finalize`, then commit and push.")
    return 0


def cmd_triage_batch(cfg: dict, args) -> int:
    run = require_run(cfg)
    batch = [run.candidates[p] for p in run.untriaged()[: args.size]]
    if not batch:
        print("Triage is complete. Run `python -m cardioaggregate status`.")
        return 0
    print(f"=== RUBRIC ===\n{TRIAGE_SYSTEM}\n")
    print(f"=== ARTICLES ({len(batch)} of {len(run.untriaged())} remaining) ===")
    print(format_triage_input(batch))
    print(f"\n=== OUTPUT ===\n{TRIAGE_FORMAT}")
    print("\nThen run `python -m cardioaggregate save-triage <file>`.")
    return 0


def cmd_save_triage(cfg: dict, args) -> int:
    run = require_run(cfg)
    raw = json.loads(Path(args.file).read_text())
    raw_items = raw["items"] if isinstance(raw, dict) else raw
    try:
        items = [TriageItem(**i) for i in raw_items]
    except (ValidationError, TypeError) as exc:
        print(f"Invalid triage JSON, nothing saved:\n{exc}")
        return 1
    unknown = run.add_triage(items, cfg["selection"])
    run.save()
    write_digest(run, cfg, preliminary=True)
    if unknown:
        print(f"Ignored unknown pmids: {', '.join(unknown)}")
    print(f"Saved {len(items) - len(unknown)} ratings; {len(run.untriaged())} left to triage.")
    if not run.untriaged():
        print(f"Triage complete: {len(run.selected)} articles selected for summaries.")
    print(COMMIT_HINT)
    return 0


def cmd_summary_next(cfg: dict, _args) -> int:
    run = require_run(cfg)
    if run.stage != "summaries":
        print(f"Not at the summary stage (stage: {run.stage}). Run `python -m cardioaggregate status`.")
        return 0
    pmid = run.unsummarized()[0]
    article = run.candidates[pmid]
    full_text = fetch_full_text(article.pmcid)
    eds = run.editorials_by_target().get(pmid, [])
    ed_texts = [(ed, fetch_full_text(ed.pmcid)) for ed in eds]
    basis = "full text" if full_text else "abstract"
    if any(text for _, text in ed_texts):
        basis += " and editorial"
    run.basis[pmid] = basis
    run.save()
    print(f"=== RUBRIC ===\n{SUMMARY_SYSTEM}\n")
    print(f"=== ARTICLE {pmid} ({len(run.unsummarized())} left; source: {basis}) ===")
    print(format_summary_input(article, full_text, ed_texts))
    if eds and not any(t for _, t in ed_texts):
        print("\n(Accompanying editorial exists but its text is not available: "
              + "; ".join(e.title for e in eds) + ". Set editorial_take to null.)")
    print(f"\n=== OUTPUT ===\n{SUMMARY_FORMAT}")
    print("\nThen run `python -m cardioaggregate save-summary <file>`.")
    return 0


def cmd_save_summary(cfg: dict, args) -> int:
    run = require_run(cfg)
    raw = json.loads(Path(args.file).read_text())
    pmid = str(raw.pop("pmid", ""))
    try:
        summary = PicoSummary(**raw)
        run.add_summary(pmid, summary, run.basis.get(pmid, "abstract"))
    except (ValidationError, ValueError, TypeError) as exc:
        print(f"Invalid summary, nothing saved:\n{exc}")
        return 1
    run.save()
    write_digest(run, cfg, preliminary=True)
    print(f"Saved summary for {pmid}; {len(run.unsummarized())} left.")
    print(COMMIT_HINT)
    return 0


def cmd_finalize(cfg: dict, args) -> int:
    run = require_run(cfg)
    finalize(run, cfg, send_email=not args.no_email)
    print(COMMIT_HINT)
    return 0


def cmd_email(cfg: dict, _args) -> int:
    """Email the latest finished digest if it has not been sent (used by GitHub Actions)."""
    run = latest_run(cfg)
    if run is None or not email_run(run, cfg):
        print("Nothing to email.")
    return 0


def cmd_prepare(cfg: dict, args) -> int:
    prepare(cfg, PubMed(), date.today(), args.days, args.force)
    return 0


def cmd_api(cfg: dict, args) -> int:
    """Do triage and summaries with the Anthropic API (needs ANTHROPIC_API_KEY)."""
    from .llm import Claude

    run = require_run(cfg)
    llm = cfg["llm"]
    claude = Claude(llm["model"], llm["triage_effort"], llm["summary_effort"])
    while run.stage == "triage":
        batch = run.untriaged()[: llm["triage_batch_size"]]
        items = claude.triage([run.candidates[p] for p in batch])
        rated = {i.pmid for i in items}
        # Anything the model skipped keeps its heuristic rating, so the loop always ends.
        eds = run.editorials_by_target()
        items += [heuristic_triage(run.candidates[p], bool(eds.get(p))) for p in batch if p not in rated]
        run.add_triage(items, cfg["selection"])
        run.save()
        print(f"Triaged {len(batch)}; {len(run.untriaged())} left")
    for pmid in run.unsummarized():
        article = run.candidates[pmid]
        eds = run.editorials_by_target().get(pmid, [])
        full_text = fetch_full_text(article.pmcid)
        ed_texts = [(ed, fetch_full_text(ed.pmcid)) for ed in eds]
        basis = ("full text" if full_text else "abstract") + (
            " and editorial" if any(t for _, t in ed_texts) else "")
        try:
            run.add_summary(pmid, claude.summarize(article, full_text, ed_texts), basis)
            run.save()
            print(f"Summarized {pmid}")
        except Exception as exc:  # one bad article should not sink the digest
            print(f"Summary failed for {pmid}: {exc}", file=sys.stderr)
    finalize(run, cfg, send_email=not args.no_email)
    return 0


def cmd_check_journals(cfg: dict, _args) -> int:
    return check_journals(cfg["journals"], PubMed())


def check_journals(journals: list[dict], pubmed: PubMed) -> int:
    """A wrong abbreviation silently yields nothing, so check each one returns
    recent records whose MEDLINE abbreviation matches exactly."""
    today = date.today()
    problems = 0
    for j in journals:
        ids = pubmed.search(f'"{j["abbr"]}"[ta]', today - timedelta(days=365), today, retmax=1)
        found = pubmed.fetch(ids)[0].journal_abbr if ids else None
        if found == j["abbr"]:
            print(f"ok       {j['abbr']}")
            continue
        problems += 1
        detail = f"PubMed returned '{found}'" if found else "no records in the last year"
        # ::warning:: shows up as an annotation on the GitHub Actions run.
        print(f"::warning::Journal abbreviation '{j['abbr']}' ({j['name']}): {detail}")
    return 1 if problems else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cardioaggregate", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare")
    p.add_argument("--days", type=int, help="override lookback_days")
    p.add_argument("--force", action="store_true", help="replace a run already prepared today")
    p.set_defaults(func=cmd_prepare)
    sub.add_parser("status").set_defaults(func=cmd_status)
    p = sub.add_parser("triage-batch")
    p.add_argument("--size", type=int, default=20)
    p.set_defaults(func=cmd_triage_batch)
    p = sub.add_parser("save-triage")
    p.add_argument("file")
    p.set_defaults(func=cmd_save_triage)
    sub.add_parser("summary-next").set_defaults(func=cmd_summary_next)
    p = sub.add_parser("save-summary")
    p.add_argument("file")
    p.set_defaults(func=cmd_save_summary)
    for name, func in (("finalize", cmd_finalize), ("api", cmd_api)):
        p = sub.add_parser(name)
        p.add_argument("--no-email", action="store_true")
        p.set_defaults(func=func)
    sub.add_parser("check-journals").set_defaults(func=cmd_check_journals)
    sub.add_parser("email").set_defaults(func=cmd_email)

    args = parser.parse_args(argv)
    return args.func(load_config(args.config), args)


if __name__ == "__main__":
    sys.exit(main())
