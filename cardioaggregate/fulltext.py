"""Open access full text from Europe PMC, when an article has a PMCID.

Most NEJM, Lancet and JACC papers are paywalled, so this usually returns
nothing and the summary falls back to the abstract. When it works it makes
the PICO summary and the editorial take far better.
"""

from __future__ import annotations

import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"

# Sections that add tokens but nothing to a clinical summary.
SKIP_SECTIONS = ("ref-list", "ack", "fn-group", "app-group")


def fetch_full_text(pmcid: str) -> str:
    if not pmcid:
        return ""
    try:
        with urllib.request.urlopen(EUROPE_PMC.format(pmcid=pmcid), timeout=60) as resp:
            raw = resp.read()
    except (urllib.error.URLError, TimeoutError):
        return ""
    return extract_body(raw)


def extract_body(xml_bytes: bytes) -> str:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""
    body = root.find(".//body")
    if body is None:
        return ""
    for tag in SKIP_SECTIONS:
        for parent in body.iter():
            for child in list(parent):
                if child.tag == tag:
                    parent.remove(child)
    paragraphs = []
    for el in body.iter():
        if el.tag in ("title", "p"):
            text = " ".join("".join(el.itertext()).split())
            if text:
                paragraphs.append(f"## {text}" if el.tag == "title" else text)
    return "\n\n".join(paragraphs)
