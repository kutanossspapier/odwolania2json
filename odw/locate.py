"""Znajduje współrzędne wyciągniętego fragmentu w PDF (do podświetlania w UI)."""
from __future__ import annotations

from .extract import PdfText

_STRIP = ".,;:()„”\"«»'"


def _norm(w: str) -> str:
    return w.strip(_STRIP).casefold()


def _rects(words) -> list[dict]:
    """Jeden prostokąt na linię tekstu."""
    lines: dict[int, list] = {}
    for w in words:
        lines.setdefault(round(w.top / 3), []).append(w)
    return [{"x0": round(min(w.x0 for w in ws), 1), "top": round(min(w.top for w in ws), 1),
             "x1": round(max(w.x1 for w in ws), 1), "bottom": round(max(w.bottom for w in ws), 1)}
            for ws in lines.values()]


def locate(doc: PdfText, raw: str) -> tuple[int | None, list[dict]]:
    """Zwraca (numer strony, lista prostokątów) dla pierwszego trafienia lub (None, [])."""
    tokens = [_norm(t) for t in str(raw).split() if _norm(t)]
    if not tokens:
        return None, []
    n = len(tokens)
    for page in doc.pages:
        ws = page.words
        norm = [_norm(w.text) for w in ws]
        for i in range(len(ws) - n + 1):
            if norm[i:i + n] == tokens:
                return page.number, _rects(ws[i:i + n])
        if n == 1:   # np. 'KRS:9999' - wartość wewnątrz dłuższego słowa
            for i, w in enumerate(norm):
                if tokens[0] and tokens[0] in w:
                    return page.number, _rects([ws[i]])
    return None, []
