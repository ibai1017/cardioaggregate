"""A week's run as a resumable state file.

Each step saves to data/runs/<date>.json, which is committed to the repo, so
the work can stop at any point (a usage limit, a closed session) and pick up
later, even on another day:

    prepare   (free, GitHub Actions)  fetch PubMed, prescore, preliminary digest
    triage    (Claude)                rate the top candidates in small batches
    summaries (Claude)                PICO summary for each selected paper
    finalize  (free)                  final digest, history, email
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .history import History
from .pubmed import Article, PubMed, build_editorial_query, build_research_query
from .render import render_digest, render_index, render_text
from .schemas import PicoSummary, TriageItem
from .select import heuristic_triage, prescore, select


@dataclass
class Run:
    path: Path
    date: str
    candidates: dict[str, Article]
    queue: list[str]                     # pmids for Claude to triage, best prescore first
    editorials: dict[str, Article]
    triage: dict[str, TriageItem] = field(default_factory=dict)
    selected: list[str] = field(default_factory=list)
    summaries: dict[str, PicoSummary] = field(default_factory=dict)
    basis: dict[str, str] = field(default_factory=dict)
    finalized: bool = False
    emailed: bool = False

    # ---- persistence -------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "Run":
        d = json.loads(path.read_text())
        return cls(
            path=path,
            date=d["date"],
            candidates={a["pmid"]: Article(**a) for a in d["candidates"]},
            queue=d["queue"],
            editorials={a["pmid"]: Article(**a) for a in d["editorials"]},
            triage={k: TriageItem(**v) for k, v in d["triage"].items()},
            selected=d["selected"],
            summaries={k: PicoSummary(**v) for k, v in d["summaries"].items()},
            basis=d["basis"],
            finalized=d["finalized"],
            emailed=d.get("emailed", False),
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "date": self.date,
            "finalized": self.finalized,
            "emailed": self.emailed,
            "queue": self.queue,
            "selected": self.selected,
            "triage": {k: v.model_dump() for k, v in self.triage.items()},
            "summaries": {k: v.model_dump() for k, v in self.summaries.items()},
            "basis": self.basis,
            "candidates": [a.to_dict() for a in self.candidates.values()],
            "editorials": [a.to_dict() for a in self.editorials.values()],
        }
        self.path.write_text(json.dumps(payload, indent=1) + "\n")

    # ---- state -------------------------------------------------------------

    def untriaged(self) -> list[str]:
        return [p for p in self.queue if p not in self.triage]

    def unsummarized(self) -> list[str]:
        return [p for p in self.selected if p not in self.summaries]

    @property
    def stage(self) -> str:
        if self.finalized:
            return "done"
        if self.untriaged():
            return "triage"
        if self.unsummarized():
            return "summaries"
        return "finalize"

    def editorials_by_target(self) -> dict[str, list[Article]]:
        out: dict[str, list[Article]] = {}
        for ed in self.editorials.values():
            for target in ed.comment_on:
                out.setdefault(target, []).append(ed)
        # An article's own CommentIn list counts even if the editorial lacks CommentOn.
        for a in self.candidates.values():
            for pmid in a.comment_in:
                ed = self.editorials.get(pmid)
                if ed and ed not in out.setdefault(a.pmid, []):
                    out[a.pmid].append(ed)
        return out

    def effective_triage(self) -> dict[str, TriageItem]:
        """Claude's ratings where available, the free heuristic elsewhere."""
        eds = self.editorials_by_target()
        out = {p: heuristic_triage(self.candidates[p], bool(eds.get(p))) for p in self.queue}
        out.update(self.triage)
        return out

    def reselect(self, rules: dict) -> None:
        queued = [self.candidates[p] for p in self.queue]
        chosen, _ = select(queued, self.effective_triage(), rules)
        self.selected = [a.pmid for a, _ in chosen]

    # ---- steps -------------------------------------------------------------

    def add_triage(self, items: list[TriageItem], rules: dict) -> list[str]:
        """Store ratings; returns pmids that were not in the queue (ignored)."""
        unknown = [i.pmid for i in items if i.pmid not in self.candidates]
        for item in items:
            if item.pmid in self.candidates:
                self.triage[item.pmid] = item
        if not self.untriaged():
            self.reselect(rules)
        return unknown

    def add_summary(self, pmid: str, summary: PicoSummary, basis: str) -> None:
        if pmid not in self.selected:
            raise ValueError(f"{pmid} is not one of this week's selected articles")
        self.summaries[pmid] = summary
        self.basis[pmid] = basis


def runs_dir(cfg: dict) -> Path:
    return Path(cfg["history_file"]).parent / "runs"


def open_run(cfg: dict) -> Run | None:
    paths = sorted(runs_dir(cfg).glob("*.json"))
    for path in reversed(paths):
        run = Run.load(path)
        if not run.finalized:
            return run
    return None


def latest_run(cfg: dict) -> Run | None:
    paths = sorted(runs_dir(cfg).glob("*.json"))
    return Run.load(paths[-1]) if paths else None


def prepare(cfg: dict, pubmed: PubMed, today: date, days: int | None = None, force: bool = False) -> Run | None:
    """Free step: fetch this week's candidates and editorials and publish a
    preliminary digest. Any unfinished earlier run is finalized first, with
    whatever Claude managed to do for it."""
    target = runs_dir(cfg) / f"{today.isoformat()}.json"
    if target.exists() and not force:
        print(f"A run for {today.isoformat()} already exists; not overwriting it (use --force).")
        return None
    history = History(cfg["history_file"])
    previous = open_run(cfg)
    if previous and previous.path == target:
        previous = None  # being replaced with --force
    if previous:
        print(f"Finalizing unfinished run {previous.date} before starting a new one")
        finalize(previous, cfg, send_email=False)
        history = History(cfg["history_file"])

    journals = cfg["journals"]
    allowed = {j["abbr"]: j.get("tier", 2) for j in journals}
    lookback = days or cfg["lookback_days"]

    ids = pubmed.search_recent(build_research_query(journals), lookback, today)
    candidates = {
        a.pmid: a for a in pubmed.fetch(ids)
        if a.journal_abbr in allowed and not a.is_editorial and not history.has_article(a.pmid)
    }
    ed_ids = pubmed.search_recent(build_editorial_query(journals), cfg["editorial_lookback_days"], today)
    ed_ids += [p for a in candidates.values() for p in a.comment_in]
    editorials = {e.pmid: e for e in pubmed.fetch(ed_ids) if "Letter" not in e.pub_types}

    run = Run(
        path=target,
        date=today.isoformat(),
        candidates=candidates,
        queue=[],
        editorials=editorials,
    )
    eds = run.editorials_by_target()
    ranked = sorted(
        candidates.values(),
        key=lambda a: -prescore(a, bool(eds.get(a.pmid)), allowed[a.journal_abbr]),
    )
    run.queue = [a.pmid for a in ranked[: cfg["selection"]["max_triage"]]]
    run.reselect(cfg["selection"])
    run.save()
    write_digest(run, cfg, preliminary=True)
    print(f"{len(ids)} PubMed hits, {len(candidates)} new candidates, "
          f"{len(run.queue)} queued for Claude, {len(editorials)} editorials")
    return run


def build_entries(run: Run) -> list[dict]:
    triage = run.effective_triage()
    eds = run.editorials_by_target()
    return [
        {
            "article": run.candidates[p],
            "triage": triage[p],
            "editorials": eds.get(p, []),
            "summary": run.summaries.get(p),
            "basis": run.basis.get(p, "abstract"),
            "error": None,
        }
        for p in run.selected
    ]


def late_editorials(run: Run, history: History) -> list:
    return [
        (ed, history.articles[target])
        for target, eds in run.editorials_by_target().items()
        if history.has_article(target) and history.articles[target]["digest"] != run.date
        for ed in eds
        if not history.has_editorial(ed.pmid)
    ]


def write_digest(run: Run, cfg: dict, preliminary: bool) -> str:
    history = History(cfg["history_file"])
    rules = cfg["selection"]
    triage = run.effective_triage()
    selected = set(run.selected)
    others = [(run.candidates[p], triage[p]) for p in run.queue if p not in selected]
    others.sort(key=lambda pair: -pair[1].impact)
    html = render_digest(
        run.date,
        build_entries(run),
        late_editorials(run, history),
        others[: rules["max_also_screened"]],
        len(run.candidates),
        used_llm=bool(run.triage),
        target_articles=rules["target_articles"],
        preliminary=preliminary,
    )
    output_dir = Path(cfg["output_dir"])
    path = output_dir / "digests" / f"{run.date}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
    (output_dir / "index.html").write_text(render_index(output_dir))
    return html


def finalize(run: Run, cfg: dict, send_email: bool = True) -> None:
    if run.untriaged():
        # Claude did not get through the queue: select with what we have.
        run.reselect(cfg["selection"])
    html = write_digest(run, cfg, preliminary=False)

    history = History(cfg["history_file"])
    late = late_editorials(run, history)
    entries = build_entries(run)
    for e in entries:
        history.add_article(e["article"], run.date)
        for ed in e["editorials"]:
            history.add_editorial(ed.pmid, run.date)
    for ed, _ in late:
        history.add_editorial(ed.pmid, run.date)
    history.save()
    run.finalized = True
    run.save()
    print(f"Finalized digest {run.date}: {len(entries)} articles, {len(run.summaries)} summarized")

    if send_email:
        email_run(run, cfg, html)


def email_run(run: Run, cfg: dict, html: str | None = None) -> bool:
    """Send a finalized digest once. Returns True if an email went out."""
    from .emailer import send_digest

    if run.emailed or not run.finalized:
        return False
    entries = build_entries(run)
    if html is None:
        html = (Path(cfg["output_dir"]) / "digests" / f"{run.date}.html").read_text()
    if not send_digest(f"Cardiology digest {run.date}: {len(entries)} articles", html,
                       render_text(run.date, entries)):
        return False
    run.emailed = True
    run.save()
    print("Emailed digest")
    return True
