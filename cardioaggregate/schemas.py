"""Shared schemas and rubrics, used both by the API driver and by a Claude
Code routine working through the steps by hand."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Design = Literal[
    "rct",
    "rct_secondary",
    "meta_analysis",
    "systematic_review",
    "observational",
    "other",
]

Subspecialty = Literal[
    "heart_failure",
    "electrophysiology",
    "interventional",
    "structural_valve",
    "imaging",
    "prevention_lipids",
    "hypertension",
    "thrombosis_vascular",
    "acute_critical_care",
    "cardiac_surgery",
    "congenital",
    "cardio_oncology",
    "general",
]


class TriageItem(BaseModel):
    pmid: str
    cardiology_relevant: bool = Field(description="Primarily about cardiovascular medicine")
    design: Design = Field(
        description=(
            "rct: primary report of a randomized trial. rct_secondary: post hoc, "
            "subgroup or secondary analysis of a trial. meta_analysis / "
            "systematic_review: pooled evidence (Cochrane reviews are systematic_review). "
            "observational: cohort, registry, case-control or cross-sectional. "
            "other: anything else (narrative review, methods, basic science, guideline)."
        )
    )
    subspecialty: Subspecialty = Field(description="Main cardiology subspecialty the article belongs to")
    sample_size: Optional[int] = Field(
        description="Total participants analysed as stated in the abstract, or null if not stated"
    )
    impact: int = Field(
        ge=1,
        le=5,
        description=(
            "Likely effect on cardiology practice or guidelines. 5 = practice "
            "changing landmark, 4 = important, 3 = solid and relevant, "
            "2 = incremental, 1 = niche."
        ),
    )
    reason: str = Field(description="One short sentence justifying the rating")


class TriageResult(BaseModel):
    items: list[TriageItem]


class PicoSummary(BaseModel):
    bottom_line: str = Field(description="One or two sentence take-home message for a cardiologist")
    design: str = Field(description="Design in a few words, e.g. 'Double-blind placebo-controlled RCT, 4.2 y median follow-up'")
    population: str
    intervention: str = Field(description="Intervention, or exposure for observational studies")
    comparison: str
    outcomes: str = Field(description="Primary outcome(s) with effect estimates, 95% CI and P values exactly as reported")
    key_results: list[str] = Field(description="Important secondary and safety results, verbatim numbers")
    strengths: list[str]
    limitations: list[str]
    editorial_take: Optional[str] = Field(
        description="Summary of the accompanying editorial's view if editorial text was provided, otherwise null"
    )
    practice_implications: str


TRIAGE_SYSTEM = """You screen newly published cardiology literature for a busy cardiologist who \
reads one curated digest a week. They want the studies most likely to matter: primary results of \
randomized trials, strong meta-analyses and systematic reviews (including Cochrane), and very large \
observational studies, across every subspecialty (heart failure, electrophysiology, interventional \
and structural, imaging, prevention, hypertension, surgery and others). A trial that changes practice \
within a subspecialty deserves a high rating even if it appeared in a subspecialty journal. Rate \
conservatively: most papers are a 2 or 3, and in a typical week only a handful across all these \
journals merit a 4 or 5. Weeks of major meetings (ESC, AHA, ACC, TCT, HRS, EuroPCR) can have many \
more, so rate each paper on its own merit rather than against a quota. Base every judgement only on \
the title, journal and abstract provided."""

SUMMARY_SYSTEM = """You write evidence summaries for a cardiologist. Use only the material \
provided. Copy numbers (effect sizes, confidence intervals, P values, event rates, sample sizes) \
exactly; never compute or invent figures. If something is not reported, say "not reported". \
Strengths and limitations are your own critical appraisal (randomization, blinding, endpoints, \
follow-up, generalizability, multiplicity, confounding, funding) unless an editorial is provided, \
in which case draw on it and summarize the editorialist's view in editorial_take. Be concise and \
clinically precise."""


def format_triage_input(articles) -> str:
    blocks = [
        f"<article pmid=\"{a.pmid}\">\nJournal: {a.journal_abbr}\n"
        f"Publication types: {', '.join(a.pub_types)}\nTitle: {a.title}\n"
        f"Abstract:\n{a.abstract}\n</article>"
        for a in articles
    ]
    return (
        "Classify every article below. Return exactly one item per article, "
        "using its pmid.\n\n" + "\n\n".join(blocks)
    )


def format_summary_input(article, full_text: str, editorials) -> str:
    """editorials is a list of (Article, text) pairs; text may be empty."""
    parts = [
        f"<article>\nJournal: {article.journal_title}\nTitle: {article.title}\n"
        f"Abstract:\n{article.abstract}\n</article>"
    ]
    if full_text:
        parts.append(f"<full_text>\n{full_text}\n</full_text>")
    for ed, ed_text in editorials:
        body = ed_text or ed.abstract
        if body:
            parts.append(f"<editorial title=\"{ed.title}\" authors=\"{ed.author_line}\">\n{body}\n</editorial>")
    parts.append("Write the PICO summary for this article.")
    return "\n\n".join(parts)
