"""Klasyfikacja dokumentu na podstawie słów kluczowych z plików YAML.

Zwraca klasę, pewność (0-1), punkty wszystkich klas i szczegóły: które wzorce
zadziałały (z cytatem z dokumentu) - dzięki temu widać, DLACZEGO taka decyzja.
- żadna klasa nie przekroczyła progu -> class_name=None (dokument "negatywny"),
- wynik niewyraźny -> uncertain=True (tu w kolejnym kroku wejdzie LLM).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .classes import DocClass, load_classes
from .extract import PdfText

HEAD_PAGES = 2


@dataclass
class Classification:
    class_name: str | None
    confidence: float
    scores: dict[str, float] = field(default_factory=dict)
    uncertain: bool = False
    matched: list[str] = field(default_factory=list)
    details: dict[str, list[dict]] = field(default_factory=dict)   # klasa -> wzorce
    thresholds: dict[str, float] = field(default_factory=dict)     # klasa -> min_score
    reason: str = ""


def score_class(cls: DocClass, text: str) -> tuple[float, list[dict]]:
    total, details = 0.0, []
    for kw in cls.keywords:
        m = kw.regex.search(text)        # każdy wzorzec liczymy raz
        if m:
            total += kw.weight
        details.append({
            "pattern": kw.regex.pattern,
            "weight": kw.weight,
            "matched": bool(m),
            "snippet": " ".join(m.group(0).split())[:60] if m else None,
        })
    return total, details


def _reason(best: DocClass | None, best_s: float, second: DocClass | None,
            second_s: float, conf: float, near: bool, min_s: float) -> str:
    if best is None:
        return "Brak klas."
    if best_s < best.min_score:
        txt = (f"Najlepsza klasa to „{best.title}”, ale zdobyła tylko {best_s:g} pkt, "
               f"a próg wynosi {best.min_score:g}. Dokument nie pasuje do żadnego znanego typu, "
               "więc wynik jest pusty (tak ma być dla dokumentów negatywnych).")
        if near:
            txt += " Wynik jest blisko progu, więc w przyszłości zdecyduje o nim LLM."
        return txt
    txt = (f"„{best.title}” zdobyło {best_s:g} pkt (próg {best.min_score:g}). "
           f"Najbliższa konkurencja: „{second.title}” z {second_s:g} pkt." if second
           else f"„{best.title}” zdobyło {best_s:g} pkt.")
    txt += f" Pewność = {best_s:g} / ({best_s:g} + {second_s:g}) = {conf:.0%}."
    if conf < 0.7:
        txt += " To mało — dokument trafiłby do LLM."
    return txt


def classify(doc: PdfText, classes: list[DocClass] | None = None) -> Classification:
    classes = classes or load_classes()
    text = doc.head(HEAD_PAGES)
    scores, details = {}, {}
    for c in classes:
        scores[c.name], details[c.name] = score_class(c, text)
    thresholds = {c.name: c.min_score for c in classes}
    ranked = sorted(classes, key=lambda c: scores[c.name], reverse=True)
    best, best_s = ranked[0], scores[ranked[0].name]
    second = ranked[1] if len(ranked) > 1 else None
    second_s = scores[second.name] if second else 0.0

    if best_s < best.min_score:
        near = best_s >= best.min_score * 0.6      # blisko progu = niepewne
        return Classification(None, 0.5 if near else 1.0, scores, uncertain=near,
                              details=details, thresholds=thresholds,
                              reason=_reason(best, best_s, second, second_s, 0, near, best.min_score))
    conf = best_s / (best_s + second_s) if (best_s + second_s) else 0.0
    return Classification(best.name, round(conf, 3), scores, uncertain=conf < 0.7,
                          matched=[d["pattern"] for d in details[best.name] if d["matched"]],
                          details=details, thresholds=thresholds,
                          reason=_reason(best, best_s, second, second_s, conf, False, best.min_score))
