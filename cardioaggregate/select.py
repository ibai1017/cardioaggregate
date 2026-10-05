"""Turn triage ratings into the week's reading list, with transparent rules."""

from __future__ import annotations

import re

from .schemas import TriageItem
from .pubmed import Article

_RANDOMIZED = re.compile(r"randomi[sz]|randomly assigned")

SECTIONS = [
    ("rct", "Randomized trials"),
    ("meta", "Meta-analyses and systematic reviews"),
    ("observational", "Large observational studies"),
]

_DESIGN_TO_SECTION = {
    "rct": "rct",
    "rct_secondary": "rct",
    "meta_analysis": "meta",
    "systematic_review": "meta",
    "observational": "observational",
}

_DESIGN_PRIORITY = {"rct": 0, "meta_analysis": 1, "systematic_review": 1, "rct_secondary": 2, "observational": 3}


def passes(item: TriageItem, rules: dict) -> bool:
    if not item.cardiology_relevant:
        return False
    if item.design == "rct":
        return item.impact >= rules["min_impact_rct"]
    if item.design == "rct_secondary":
        return item.impact >= rules["min_impact_rct_secondary"]
    if item.design in ("meta_analysis", "systematic_review"):
        return item.impact >= rules["min_impact_meta"]
    if item.design == "observational":
        large = (item.sample_size or 0) >= rules["large_observational_n"]
        threshold = rules["min_impact_observational_large"] if large else rules["min_impact_observational_other"]
        return item.impact >= threshold
    return False


def select(articles: list[Article], triage: dict[str, TriageItem], rules: dict):
    """Return (selected, also_screened); both are lists of (article, triage)."""
    chosen, rest = [], []
    for a in articles:
        item = triage.get(a.pmid)
        if item is None:
            continue
        (chosen if passes(item, rules) else rest).append((a, item))
    chosen.sort(key=lambda p: (-p[1].impact, _DESIGN_PRIORITY.get(p[1].design, 9)))

    # High impact papers always get in (up to the ceiling), so a conference week
    # grows on its own; lower rated papers only fill up to the normal target.
    high = [p for p in chosen if p[1].impact >= rules["surge_min_impact"]]
    fill = [p for p in chosen if p[1].impact < rules["surge_min_impact"]]
    selected = high[: rules["max_articles"]]
    room = max(0, rules["target_articles"] - len(selected))
    selected += fill[:room]
    overflow = high[rules["max_articles"] :] + fill[room:]
    chosen = selected
    rest = overflow + rest
    rest.sort(key=lambda p: -p[1].impact)
    return chosen, rest[: rules.get("max_also_screened", len(rest))]


def section_of(item: TriageItem) -> str:
    return _DESIGN_TO_SECTION.get(item.design, "observational")


# Free heuristics. They pick which candidates are worth Claude's attention
# (prescore), and stand in for Claude's triage when it never ran.

_DESIGN_SCORE = {"rct": 5, "meta_analysis": 4, "systematic_review": 4, "rct_secondary": 2, "observational": 1}


def _guess(article: Article) -> tuple[str, int | None]:
    text = f"{article.title} {article.abstract}".lower()
    pts = set(article.pub_types)
    if "Meta-Analysis" in pts or "meta-analysis" in text:
        design = "meta_analysis"
    elif "Systematic Review" in pts or "systematic review" in text or article.journal_abbr.startswith("Cochrane"):
        design = "systematic_review"
    elif re.search(r"post hoc|secondary analysis|prespecified analysis|subgroup", text):
        design = "rct_secondary" if _RANDOMIZED.search(text) else "observational"
    elif "Randomized Controlled Trial" in pts or _RANDOMIZED.search(text):
        design = "rct"
    else:
        design = "observational"
    n = None
    for m in re.finditer(r"(\d{1,3}(?:[ ,]\d{3})+|\d+)\s+(?:patients|participants|adults|individuals|people)", text):
        n = max(n or 0, int(re.sub(r"[ ,]", "", m.group(1))))
    return design, n


def prescore(article: Article, has_editorial: bool, tier: int) -> float:
    """Higher is more likely to matter. An accompanying editorial is the
    strongest free signal: journals rarely commission one for a minor paper."""
    design, n = _guess(article)
    score = _DESIGN_SCORE[design] + (2 if tier == 1 else 0) + (4 if has_editorial else 0)
    if n:
        score += min(len(str(n)) - 2, 4) * 0.5  # roughly log10(n): 1,000 -> +1, 100,000 -> +2
    return score


def heuristic_triage(article: Article, has_editorial: bool = False) -> TriageItem:
    design, n = _guess(article)
    return TriageItem(
        pmid=article.pmid,
        cardiology_relevant=True,
        design=design,
        subspecialty="general",
        sample_size=n,
        impact=4 if has_editorial else 3,
        reason="Heuristic classification (not reviewed by Claude)",
    )
