"""Claude API driver: triage of candidates and PICO summaries of selected articles.

Only used when ANTHROPIC_API_KEY is set. The routine path (see ROUTINE.md) uses
the same schemas and rubrics without calling the API.
"""

from __future__ import annotations

import anthropic
from pydantic import BaseModel

from .pubmed import Article
from .schemas import (  # noqa: F401  (re-exported)
    SUMMARY_SYSTEM,
    TRIAGE_SYSTEM,
    PicoSummary,
    TriageItem,
    TriageResult,
    format_summary_input,
    format_triage_input,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


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

    def triage(self, articles: list[Article]) -> list[TriageItem]:
        content = format_triage_input(articles)
        return self._parse(TRIAGE_SYSTEM, content, TriageResult, self.triage_effort).items

    def summarize(self, article: Article, full_text: str, editorials: list[tuple[Article, str]]) -> PicoSummary:
        content = format_summary_input(article, full_text, editorials)
        return self._parse(SUMMARY_SYSTEM, content, PicoSummary, self.summary_effort)
