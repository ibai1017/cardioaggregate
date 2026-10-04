"""PubMed E-utilities client: query building, fetching and XML parsing."""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, timedelta

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# Publication types that are never primary research we want to summarize.
EXCLUDED_PUB_TYPES = {
    "Editorial",
    "Comment",
    "Letter",
    "News",
    "Published Erratum",
    "Retraction of Publication",
    "Retracted Publication",
    "Case Reports",
    "Expression of Concern",
}

# New PubMed records usually have no MeSH terms or curated publication types
# yet (indexing lags by weeks), so the topic and design filters lean on
# title/abstract words and only use MeSH/[pt] as a bonus.
CARDIO_TERMS = [
    '"Cardiovascular Diseases"[MeSH]',
    "heart[tiab]",
    "cardiac[tiab]",
    "cardio*[tiab]",
    "coronary[tiab]",
    "myocardial[tiab]",
    "atrial[tiab]",
    "ventricular[tiab]",
    "aortic[tiab]",
    "valve[tiab]",
    "valvular[tiab]",
    "arrhythmi*[tiab]",
    "hypertensi*[tiab]",
    '"blood pressure"[tiab]',
    "stroke[tiab]",
    "thrombo*[tiab]",
    "embolism[tiab]",
    "anticoagula*[tiab]",
    "lipid*[tiab]",
    "cholesterol[tiab]",
    "lipoprotein*[tiab]",
    "statin*[tiab]",
    "atheroscler*[tiab]",
    '"peripheral artery"[tiab]',
    "cardiomyopath*[tiab]",
]

DESIGN_TERMS = [
    '"Randomized Controlled Trial"[pt]',
    '"Clinical Trial"[pt]',
    '"Meta-Analysis"[pt]',
    '"Systematic Review"[pt]',
    '"Observational Study"[pt]',
    "randomi*[tiab]",
    '"randomly assigned"[tiab]',
    "trial[ti]",
    '"meta-analysis"[tiab]',
    '"systematic review"[tiab]',
    "cohort[tiab]",
    "registry[tiab]",
    "nationwide[tiab]",
    '"population-based"[tiab]',
]


@dataclass
class Article:
    pmid: str
    title: str
    abstract: str = ""
    journal_abbr: str = ""
    journal_title: str = ""
    pub_date: str = ""
    authors: list[str] = field(default_factory=list)
    doi: str = ""
    pmcid: str = ""
    pub_types: list[str] = field(default_factory=list)
    comment_in: list[str] = field(default_factory=list)
    comment_on: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        if self.doi:
            return f"https://doi.org/{self.doi}"
        return self.pubmed_url

    @property
    def pubmed_url(self) -> str:
        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/"

    @property
    def author_line(self) -> str:
        if not self.authors:
            return ""
        if len(self.authors) <= 3:
            return ", ".join(self.authors)
        return f"{self.authors[0]} et al."

    @property
    def is_editorial(self) -> bool:
        return "Editorial" in self.pub_types

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _or(terms: list[str]) -> str:
    return "(" + " OR ".join(terms) + ")"


def build_research_query(journals: list[dict]) -> str:
    """One PubMed query covering every journal group with its own filters."""
    groups: dict[tuple[bool, bool], list[str]] = {}
    for j in journals:
        key = (bool(j.get("topic_filter")), bool(j.get("design_filter")))
        groups.setdefault(key, []).append(f'"{j["abbr"]}"[ta]')

    clauses = []
    for (topic, design), tas in sorted(groups.items()):
        parts = [_or(tas)]
        if topic:
            parts.append(_or(CARDIO_TERMS))
        if design:
            parts.append(_or(DESIGN_TERMS))
        clauses.append("(" + " AND ".join(parts) + ")")

    excluded = _or([f'"{pt}"[pt]' for pt in sorted(EXCLUDED_PUB_TYPES)])
    return f"{_or(clauses)} AND hasabstract NOT {excluded}"


def build_editorial_query(journals: list[dict]) -> str:
    tas = _or([f'"{j["abbr"]}"[ta]' for j in journals])
    return f'{tas} AND ("Editorial"[pt] OR "Comment"[pt]) NOT "Letter"[pt]'


class PubMed:
    def __init__(self, api_key: str | None = None, email: str | None = None):
        self.api_key = api_key or os.environ.get("NCBI_API_KEY") or None
        self.email = email or os.environ.get("NCBI_EMAIL") or None
        # NCBI allows 3 requests/s without a key and 10/s with one.
        self._min_interval = 0.11 if self.api_key else 0.35
        self._last = 0.0

    def _request(self, endpoint: str, params: dict) -> bytes:
        params = dict(params, tool="cardioaggregate")
        if self.api_key:
            params["api_key"] = self.api_key
        if self.email:
            params["email"] = self.email
        data = urllib.parse.urlencode(params).encode()
        url = f"{EUTILS}/{endpoint}"
        for attempt in range(5):
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                with urllib.request.urlopen(url, data=data, timeout=60) as resp:
                    return resp.read()
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == 4:
                    raise
                print(f"PubMed {endpoint} failed ({exc}); retrying")
                time.sleep(2 ** (attempt + 1))
        raise AssertionError("unreachable")

    def search(self, term: str, mindate: date, maxdate: date, retmax: int = 1000) -> list[str]:
        raw = self._request(
            "esearch.fcgi",
            {
                "db": "pubmed",
                "term": term,
                "datetype": "edat",
                "mindate": mindate.strftime("%Y/%m/%d"),
                "maxdate": maxdate.strftime("%Y/%m/%d"),
                "retmax": retmax,
            },
        )
        root = ET.fromstring(raw)
        return [el.text for el in root.findall("./IdList/Id") if el.text]

    def count(self, term: str, mindate: date, maxdate: date) -> int:
        raw = self._request(
            "esearch.fcgi",
            {
                "db": "pubmed",
                "term": term,
                "datetype": "edat",
                "mindate": mindate.strftime("%Y/%m/%d"),
                "maxdate": maxdate.strftime("%Y/%m/%d"),
                "retmax": 0,
            },
        )
        return int(ET.fromstring(raw).findtext("Count") or 0)

    def search_recent(self, term: str, days: int, today: date | None = None) -> list[str]:
        today = today or date.today()
        return self.search(term, today - timedelta(days=days), today)

    def fetch(self, pmids: list[str]) -> list[Article]:
        articles: list[Article] = []
        unique = list(dict.fromkeys(pmids))
        for i in range(0, len(unique), 200):
            raw = self._request(
                "efetch.fcgi",
                {"db": "pubmed", "id": ",".join(unique[i : i + 200]), "retmode": "xml"},
            )
            articles.extend(parse_articles(raw))
        return articles


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return " ".join("".join(el.itertext()).split())


_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _date(el: ET.Element | None) -> str:
    if el is None:
        return ""
    year = el.findtext("Year")
    if not year:
        return _text(el.find("MedlineDate"))
    month = el.findtext("Month") or ""
    month = str(_MONTHS.get(month[:3], month)) if month else ""
    day = el.findtext("Day") or ""
    parts = [year] + [p.zfill(2) for p in (month, day) if p]
    return "-".join(parts)


def parse_articles(xml_bytes: bytes) -> list[Article]:
    root = ET.fromstring(xml_bytes)
    out = []
    for pa in root.findall("./PubmedArticle"):
        mc = pa.find("MedlineCitation")
        art = mc.find("Article")
        journal = art.find("Journal")

        abstract_parts = []
        for at in art.findall("./Abstract/AbstractText"):
            text = _text(at)
            label = at.get("Label")
            abstract_parts.append(f"{label}: {text}" if label else text)

        authors = []
        for a in art.findall("./AuthorList/Author"):
            if a.findtext("CollectiveName"):
                authors.append(_text(a.find("CollectiveName")))
            elif a.findtext("LastName"):
                authors.append(f'{a.findtext("LastName")} {a.findtext("Initials") or ""}'.strip())

        ids = {aid.get("IdType"): (aid.text or "").strip()
               for aid in pa.findall("./PubmedData/ArticleIdList/ArticleId")}
        doi = ids.get("doi", "")
        if not doi:
            for loc in art.findall("ELocationID"):
                if loc.get("EIdType") == "doi":
                    doi = (loc.text or "").strip()

        comments: dict[str, list[str]] = {"CommentIn": [], "CommentOn": []}
        for cc in mc.findall("./CommentsCorrectionsList/CommentsCorrections"):
            ref_type = cc.get("RefType")
            pmid = cc.findtext("PMID")
            if ref_type in comments and pmid:
                comments[ref_type].append(pmid.strip())

        pub_date = _date(art.find("ArticleDate")) or _date(journal.find("./JournalIssue/PubDate"))

        out.append(
            Article(
                pmid=mc.findtext("PMID").strip(),
                title=_text(art.find("ArticleTitle")),
                abstract="\n".join(abstract_parts),
                journal_abbr=mc.findtext("./MedlineJournalInfo/MedlineTA")
                or journal.findtext("ISOAbbreviation")
                or "",
                journal_title=journal.findtext("Title") or "",
                pub_date=pub_date,
                authors=authors,
                doi=doi,
                pmcid=ids.get("pmc", ""),
                pub_types=[_text(pt) for pt in art.findall("./PublicationTypeList/PublicationType")],
                comment_in=comments["CommentIn"],
                comment_on=comments["CommentOn"],
            )
        )
    return out
