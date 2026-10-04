"""HTML and plain text output."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from .select import SECTIONS, section_of

DESIGN_LABELS = {
    "rct": "RCT",
    "rct_secondary": "RCT secondary analysis",
    "meta_analysis": "Meta-analysis",
    "systematic_review": "Systematic review",
    "observational": "Observational",
    "other": "Other",
}

SUBSPECIALTY_LABELS = {
    "heart_failure": "Heart failure",
    "electrophysiology": "Electrophysiology",
    "interventional": "Interventional",
    "structural_valve": "Structural and valve",
    "imaging": "Imaging",
    "prevention_lipids": "Prevention and lipids",
    "hypertension": "Hypertension",
    "thrombosis_vascular": "Thrombosis and vascular",
    "acute_critical_care": "Acute and critical care",
    "cardiac_surgery": "Cardiac surgery",
    "congenital": "Congenital",
    "cardio_oncology": "Cardio-oncology",
    "general": "General cardiology",
}

_env = Environment(
    loader=PackageLoader("cardioaggregate", "templates"),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_digest(digest_date, entries, late_editorials, also_screened, screened_count, used_llm,
                  target_articles=10) -> str:
    grouped = {key: [] for key, _ in SECTIONS}
    for e in entries:
        grouped[section_of(e["triage"])].append(e)
    return _env.get_template("digest.html.j2").render(
        digest_date=digest_date,
        sections=SECTIONS,
        grouped=grouped,
        entries=entries,
        late_editorials=late_editorials,
        also_screened=also_screened,
        screened_count=screened_count,
        used_llm=used_llm,
        target_articles=target_articles,
        design_labels=DESIGN_LABELS,
        subspecialty_labels=SUBSPECIALTY_LABELS,
    )


def render_index(output_dir: Path) -> str:
    digests = sorted((p.stem for p in (output_dir / "digests").glob("*.html")), reverse=True)
    return _env.get_template("index.html.j2").render(digests=digests)


def render_text(digest_date, entries) -> str:
    lines = [f"Cardiology digest, week ending {digest_date}", ""]
    for e in entries:
        a, s = e["article"], e["summary"]
        lines.append(f"* {a.title} ({a.journal_abbr})")
        lines.append(f"  {a.url}")
        if s:
            lines.append(f"  {s.bottom_line}")
        lines.append("")
    return "\n".join(lines)
