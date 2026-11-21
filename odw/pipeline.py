"""Cały potok dla jednego PDF: tekst -> rozpoznanie typu -> pola (regex) -> LLM (luki) -> JSON.

Bez LLM (LLM_ENABLED=0 albo use_llm=False) wynik pochodzi wyłącznie z regexów i reguł.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import llm, settings
from .classes import DocClass, load_classes
from .classify import Classification, classify
from .engine import Extraction, extract
from .extract import PdfText, extract_pdf


@dataclass
class Result:
    doc: PdfText
    classification: Classification
    extraction: Extraction | None
    llm_fields: list[str] = field(default_factory=list)   # pola uzupełnione przez LLM

    @property
    def json(self) -> dict:
        """Wynikowy JSON; pusty słownik, gdy dokument nie należy do żadnej klasy."""
        return self.extraction.json if self.extraction else {}


def process(path: str | Path, classes: list[DocClass] | None = None,
            max_pages: int | None = None, use_llm: bool | None = None, client=None) -> Result:
    classes = classes or load_classes()
    use_llm = settings.LLM_ENABLED if use_llm is None else use_llm
    doc = extract_pdf(path, max_pages=max_pages)
    cl = classify(doc, classes)

    if use_llm and cl.uncertain:       # słowa kluczowe niepewne -> decyduje LLM
        decided = llm.classify_with_llm(doc, classes, client)
        if decided != cl.class_name:
            cl.class_name, cl.confidence = decided, 0.7
            cl.reason += " Decyzję podjął LLM (słowa kluczowe były niepewne)."

    ex, filled = None, []
    if cl.class_name:
        cls = next(c for c in classes if c.name == cl.class_name)
        ex = extract(doc, cls)
        if use_llm:
            filled = llm.fill_missing(doc, cls, ex, client)
    return Result(doc, cl, ex, filled)
