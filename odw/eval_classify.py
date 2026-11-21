"""Sprawdza klasyfikator na zbiorze treningowym UZP.

Użycie:  python -m odw.eval_classify [ścieżka do folderu 'dane']
Prawdziwa klasa wynika z nazwy pliku (tylko do oceny - klasyfikator jej nie widzi).
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from . import settings
from .classes import load_classes
from .classify import classify
from .extract import extract_pdf

TRUE = {"odwolanie.pdf": "odwolanie", "pelnomocnictwo.pdf": "pelnomocnictwo",
        "potwierdzenie_przelewu.pdf": "przelew"}
DEFAULT_DANE = settings.ROOT.parent / "dane-uzp" / "dane"


def main(dane: Path) -> None:
    classes = load_classes()
    pdfs = sorted(dane.glob("przyklad_*/*.pdf")) + sorted((dane / "negatywne").glob("*.pdf"))
    rows, warns = [], 0
    for pdf in pdfs:
        doc = extract_pdf(pdf, max_pages=3)
        warns += len(doc.warnings)
        rows.append((pdf, TRUE.get(pdf.name), classify(doc, classes)))
    ok = sum(1 for _, t, p in rows if t == p.class_name)
    print(f"Poprawnie: {ok}/{len(rows)} ({ok / len(rows):.1%})")
    for (t, p), n in sorted(Counter((t or "NEGATYW", p.class_name or "NEGATYW")
                                    for _, t, p in rows).items()):
        print(f"  prawda={t:15s} wynik={p:15s} {n}")
    print("niepewne (trafią do LLM):", sum(p.uncertain for *_, p in rows))
    print("ostrzeżenia OCR:", warns)
    for pdf, t, p in rows:
        if t != p.class_name:
            print("BŁĄD:", f"{pdf.parent.name}/{pdf.name}", "prawda:", t, "wynik:", p.class_name, p.scores)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DANE)
