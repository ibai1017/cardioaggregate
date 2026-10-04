"""Build this week's digest.

    python -m cardioaggregate                 # normal weekly run
    python -m cardioaggregate --days 14       # wider window
    python -m cardioaggregate --no-llm        # skip Claude (heuristic triage, no summaries)
    python -m cardioaggregate --check-journals  # confirm every journal abbreviation matches PubMed
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

from .emailer import send_digest
from .fulltext import fetch_full_text
from .history import History
from .pubmed import PubMed, build_editorial_query, build_research_query
from .render import render_digest, render_index, render_text
from .select import heuristic_triage, select


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cardioaggregate")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--days", type=int, help="override lookback_days")
    parser.add_argument("--no-llm", action="store_true", help="do not call Claude")
    parser.add_argument("--no-email", action="store_true")
    parser.add_argument("--check-journals", action="store_true",
                        help="verify journal abbreviations against PubMed and exit")
    args = parser.parse_args(argv)

    cfg = yaml.safe_load(Path(args.config).read_text())
    if args.check_journals:
        return check_journals(cfg["journals"], PubMed())
    today = date.today()
    digest_date = today.isoformat()
    output_dir = Path(cfg["output_dir"])
    history = History(cfg["history_file"])
    journals = cfg["journals"]
    allowed = {j["abbr"] for j in journals}
    lookback = args.days or cfg["lookback_days"]

    use_llm = not args.no_llm and bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not use_llm and not args.no_llm:
        print("ANTHROPIC_API_KEY not set: running without triage or summaries")

    pubmed = PubMed()

    # 1. Candidates
    ids = pubmed.search_recent(build_research_query(journals), lookback, today)
    candidates = [
        a for a in pubmed.fetch(ids)
        if a.journal_abbr in allowed and not a.is_editorial and not history.has_article(a.pmid)
    ]
    print(f"{len(ids)} PubMed hits, {len(candidates)} new candidates")

    # 2. Triage and selection
    if use_llm:
        from .llm import Claude

        llm_cfg = cfg["llm"]
        claude = Claude(llm_cfg["model"], llm_cfg["triage_effort"], llm_cfg["summary_effort"])
        triage = claude.triage(candidates, llm_cfg["triage_batch_size"])
    else:
        claude = None
        triage = {a.pmid: heuristic_triage(a) for a in candidates}
    chosen, also_screened = select(candidates, triage, cfg["selection"])
    print(f"{len(chosen)} selected")

    # 3. Editorials: recent ones in the same journals plus any linked from chosen articles
    ed_ids = pubmed.search_recent(build_editorial_query(journals), cfg["editorial_lookback_days"], today)
    ed_ids += [pmid for a, _ in chosen for pmid in a.comment_in]
    editorials = [e for e in pubmed.fetch(ed_ids) if "Letter" not in e.pub_types]
    by_target: dict[str, list] = {}
    for ed in editorials:
        for target in ed.comment_on:
            by_target.setdefault(target, []).append(ed)
    # An article's own CommentIn list is authoritative even if the editorial lacks CommentOn.
    ed_by_pmid = {e.pmid: e for e in editorials}
    for a, _ in chosen:
        for pmid in a.comment_in:
            ed = ed_by_pmid.get(pmid)
            if ed and ed not in by_target.setdefault(a.pmid, []):
                by_target[a.pmid].append(ed)

    late_editorials = [
        (ed, history.articles[target])
        for target, eds in by_target.items()
        if history.has_article(target)
        for ed in eds
        if not history.has_editorial(ed.pmid)
    ]

    # 4. Summaries
    entries = []
    for i, (article, item) in enumerate(chosen, 1):
        eds = by_target.get(article.pmid, [])
        entry = {"article": article, "triage": item, "editorials": eds, "summary": None,
                 "basis": "abstract", "error": None}
        if claude:
            print(f"Summarizing {i}/{len(chosen)}: {article.title[:80]}")
            full_text = fetch_full_text(article.pmcid)
            ed_texts = [(ed, fetch_full_text(ed.pmcid)) for ed in eds]
            entry["basis"] = "full text" if full_text else "abstract"
            if any(t for _, t in ed_texts):
                entry["basis"] += " and editorial"
            try:
                entry["summary"] = claude.summarize(article, full_text, ed_texts)
            except Exception as exc:  # one bad article should not sink the digest
                entry["error"] = str(exc)
                print(f"  failed: {exc}", file=sys.stderr)
        entries.append(entry)

    # 5. Output
    html = render_digest(digest_date, entries, late_editorials, also_screened, len(candidates), use_llm)
    digest_path = output_dir / "digests" / f"{digest_date}.html"
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text(html)
    (output_dir / "index.html").write_text(render_index(output_dir))
    print(f"Wrote {digest_path}")

    for article, _ in chosen:
        history.add_article(article, digest_date)
    for e in entries:
        for ed in e["editorials"]:
            history.add_editorial(ed.pmid, digest_date)
    for ed, _ in late_editorials:
        history.add_editorial(ed.pmid, digest_date)
    history.save()

    if not args.no_email and send_digest(
        f"Cardiology digest {digest_date}: {len(entries)} articles", html, render_text(digest_date, entries)
    ):
        print("Emailed digest")
    return 0


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


if __name__ == "__main__":
    sys.exit(main())
