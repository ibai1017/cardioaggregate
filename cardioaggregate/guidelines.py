"""New guidelines and statements from ACC/AHA, ESC, CCS and SCAI.

Found with one PubMed query on title words, then sorted with free title rules:
brand new records rarely carry the Guideline publication type yet, and many
editorials and letters mention guidelines in their title without being one.
"""

from __future__ import annotations

import re

from .pubmed import Article, _or

TITLE_TERMS = [
    "guideline[ti]",
    "guidelines[ti]",
    '"focused update"[ti]',
    '"scientific statement"[ti]',
    '"expert consensus"[ti]',
    '"consensus statement"[ti]',
    '"consensus document"[ti]',
    '"decision pathway"[ti]',
    '"position statement"[ti]',
    '"position paper"[ti]',
    '"clinical practice update"[ti]',
    '"Practice Guideline"[pt]',
    '"Guideline"[pt]',
]

NOT_DOCUMENTS = {"Published Erratum", "Editorial", "Comment", "Letter", "News", "Retraction of Publication"}

# The societies asked for, including the ESC's own associations (EHRA, HFA,
# EAPCI, EACVI, EAPC, ACVC), whose papers are ESC documents.
SOCIETY = re.compile(
    r"\b(ACC|AHA|American College of Cardiology|American Heart Association|"
    r"ESC|European Society of Cardiology|EHRA|HFA|EAPCI|EACVI|EAPC|ACVC|"
    r"CCS|Canadian Cardiovascular Society|SCAI|Society for Cardiovascular Angiography)\b"
)

KINDS = [
    ("guideline", "Guidelines"),
    ("consensus", "Consensus documents and decision pathways"),
    ("statement", "Scientific statements"),
]

_CONSENSUS = re.compile(
    r"expert consensus|decision pathway|consensus statement|consensus document|"
    r"position (paper|statement)|clinical practice update",
    re.I,
)


def build_guideline_query(journals: list[str]) -> str:
    excluded = _or([f'"{pt}"[pt]' for pt in sorted(NOT_DOCUMENTS)])
    tas = _or([f'"{j}"[ta]' for j in journals])
    return f"{tas} AND {_or(TITLE_TERMS)} NOT {excluded}"


def classify(article: Article) -> str | None:
    """'guideline', 'consensus', 'statement', or None when the record is not a
    guideline document from one of the societies (a commentary about one, a
    correction, another society's paper)."""
    if NOT_DOCUMENTS & set(article.pub_types):
        return None
    title = article.title
    if re.match(r"(correction|author corrections?|erratum)\b", title, re.I) or not SOCIETY.search(title):
        return None
    formal = {"Practice Guideline", "Guideline"} & set(article.pub_types)
    # Formal guidelines are titled "2026 ESC Guidelines for ..." or
    # "2026 AHA/ACC/... Guideline for ...", or carry the Guideline type.
    if re.search(r"focused update", title, re.I) or (
        re.search(r"\bguidelines?\b", title, re.I) and (re.match(r"\d{4} ", title) or formal)
    ):
        return "guideline"
    if _CONSENSUS.search(title):
        return "consensus"
    if re.search(r"scientific statement", title, re.I):
        return "statement"
    return None


def title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.lower())


def collect(articles: list[Article], seen_titles: set[str]) -> tuple[dict[str, Article], dict[str, str], dict[str, list[str]]]:
    """Keep guideline documents, merge copies published in several journals
    (ACC/AHA guidelines appear in both JACC and Circulation), and skip ones
    already covered. Returns (documents, kind by pmid, other journals by pmid)."""
    docs: dict[str, Article] = {}
    kinds: dict[str, str] = {}
    also_in: dict[str, list[str]] = {}
    by_title: dict[str, str] = {}
    for a in articles:
        kind = classify(a)
        key = title_key(a.title)
        if kind is None or key in seen_titles:
            continue
        if key in by_title:
            first = by_title[key]
            if a.journal_abbr != docs[first].journal_abbr:
                also_in.setdefault(first, []).append(a.journal_abbr)
            continue
        by_title[key] = a.pmid
        docs[a.pmid] = a
        kinds[a.pmid] = kind
    return docs, kinds, also_in
