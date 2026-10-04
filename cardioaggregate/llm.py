"""Claude calls: triage of candidates and PICO summaries of selected articles."""

from __future__ import annotations

from typing import Literal, Optional

import anthropic
from pydantic import BaseModel, Field

from .pubmed import Article

FALLBACK_BETA = "server-side-fallback-2026-07-01"

Design = Literal[
    "rct",
    "rct_secondary",
    "meta_analysis",
    "systematic_review",
    "observational",
    "other",
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
observational studies. Rate conservatively; most papers are a 2 or 3. Base every judgement only on \
the title, journal and abstract provided."""

SUMMARY_SYSTEM = """You write evidence summaries for a cardiologist. Use only the material \
provided. Copy numbers (effect sizes, confidence intervals, P values, event rates, sample sizes) \
exactly; never compute or invent figures. If something is not reported, say "not reported". \
Strengths and limitations are your own critical appraisal (randomization, blinding, endpoints, \
follow-up, generalizability, multiplicity, confounding, funding) unless an editorial is provided, \
in which case draw on it and summarize the editorialist's view in editorial_take. Be concise and \
clinically precise."""


class Claude:
    def __init__(self, model: str, triage_effort: str = "low", summary_effort: str = "medium"):
        self.client = anthropic.Anthropic()
        self.model = model
        self.triage_effort = triage_effort
        self.summary_effort = summary_effort

    def _parse(self, system: str, content: str, schema: type[BaseModel], effort: str):
        response = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=schema,
            output_config={"effort": effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise RuntimeError(f"Claude returned no usable output (stop_reason={response.stop_reason})")
        return response.parsed_output

    def triage(self, articles: list[Article], batch_size: int = 40) -> dict[str, TriageItem]:
        results: dict[str, TriageItem] = {}
        for i in range(0, len(articles), batch_size):
            batch = articles[i : i + batch_size]
            blocks = [
                f"<article pmid=\"{a.pmid}\">\nJournal: {a.journal_abbr}\n"
                f"Publication types: {', '.join(a.pub_types)}\nTitle: {a.title}\n"
                f"Abstract:\n{a.abstract}\n</article>"
                for a in batch
            ]
            content = (
                "Classify every article below. Return exactly one item per article, "
                "using its pmid.\n\n" + "\n\n".join(blocks)
            )
            parsed: TriageResult = self._parse(TRIAGE_SYSTEM, content, TriageResult, self.triage_effort)
            for item in parsed.items:
                results[item.pmid] = item
        return results

    def summarize(self, article: Article, full_text: str, editorials: list[tuple[Article, str]]) -> PicoSummary:
        parts = [
            f"<article>\nJournal: {article.journal_title}\nTitle: {article.title}\n"
            f"Abstract:\n{article.abstract}\n</article>"
        ]
        if full_text:
            parts.append(f"<full_text>\n{full_text}\n</full_text>")
        for ed, ed_text in editorials:
            body = ed_text or ed.abstract
            if body:
                parts.append(
                    f"<editorial title=\"{ed.title}\" authors=\"{ed.author_line}\">\n{body}\n</editorial>"
                )
        parts.append("Write the PICO summary for this article.")
        return self._parse(SUMMARY_SYSTEM, "\n\n".join(parts), PicoSummary, self.summary_effort)
