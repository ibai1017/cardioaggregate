"""Remembers what earlier digests covered, so nothing repeats and late
editorials can be matched to articles from previous weeks."""

from __future__ import annotations

import json
from pathlib import Path


class History:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.articles: dict[str, dict] = data.get("articles", {})
        self.editorials: dict[str, str] = data.get("editorials", {})

    def has_article(self, pmid: str) -> bool:
        return pmid in self.articles

    def has_editorial(self, pmid: str) -> bool:
        return pmid in self.editorials

    def add_article(self, article, digest_date: str) -> None:
        self.articles[article.pmid] = {
            "digest": digest_date,
            "title": article.title,
            "journal": article.journal_abbr,
            "url": article.url,
        }

    def add_editorial(self, pmid: str, digest_date: str) -> None:
        self.editorials[pmid] = digest_date

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"articles": self.articles, "editorials": self.editorials}
        self.path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
