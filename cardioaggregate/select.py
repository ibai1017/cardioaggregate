"""Turn triage ratings into the week's reading list, with transparent rules."""

from __future__ import annotations

import re

from .llm import TriageItem
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
    overflow = chosen[rules["max_articles"] :]
    chosen = chosen[: rules["max_articles"]]
    rest = overflow + rest
    rest.sort(key=lambda p: -p[1].impact)
    return chosen, rest


def section_of(item: TriageItem) -> str:
    return _DESIGN_TO_SECTION.get(item.design, "observational")


# Used when no API key is configured: a rough design guess from metadata so the
# pipeline still produces a (less curated) digest.
def heuristic_triage(article: Article) -> TriageItem:
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
    return TriageItem(
        pmid=article.pmid,
        cardiology_relevant=True,
        design=design,
        sample_size=n,
        impact=3,
        reason="Heuristic classification (no API key configured)",
    )
