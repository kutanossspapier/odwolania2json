"""Tryb wsadowy: przetwarza wszystkie PDF-y z katalogu input/ i zapisuje JSON-y w output/.

Struktura katalogów jest odwzorowywana 1:1 (input/case1/a.pdf -> output/case1/a.json).
Typ dokumentu rozpoznajemy z TREŚCI (nie z nazwy pliku). Dokumenty spoza trzech klas
(np. negatywne) nie dostają pliku JSON - zostaje wpis w logu. Awaria jednego pliku nie
zatrzymuje reszty. Działa bez LLM (regex); LLM tylko uzupełnia luki, gdy skonfigurowany.

Użycie:  python -m odw.cli [--input input] [--output output] [--no-llm] [--workers N]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import settings


def _one(args):
    src, dst, use_llm = args
    from .pipeline import process
    t = time.time()
    try:
        res = process(src, use_llm=use_llm)
    except Exception as exc:                       # nie wywracamy całej paczki przez jeden plik
        return src, "BŁĄD", f"{type(exc).__name__}: {exc}", time.time() - t
    if not res.classification.class_name or not res.extraction:
        return src, "POMINIĘTO", "dokument nie pasuje do żadnej klasy", time.time() - t
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(res.json, ensure_ascii=False, indent=2), encoding="utf-8")
    return src, "OK", res.classification.class_name, time.time() - t


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PDF-y z input/ -> JSON-y w output/")
    ap.add_argument("--input", default=str(settings.ROOT / "input"))
    ap.add_argument("--output", default=str(settings.ROOT / "output"))
    ap.add_argument("--no-llm", action="store_true", help="tylko regexy (bez wywołań API)")
    ap.add_argument("--workers", type=int, default=2, help="liczba procesów równoległych")
    args = ap.parse_args(argv)

    inp, out = Path(args.input), Path(args.output)
    pdfs = sorted(p for p in inp.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
    out.mkdir(parents=True, exist_ok=True)
    if not pdfs:
        print(f"Brak plików PDF w {inp}")
        return 0
    use_llm = False if args.no_llm else None        # None = wg ustawień (.env)
    jobs = [(p, out / p.relative_to(inp).with_suffix(".json"), use_llm) for p in pdfs]
    print(f"Plików: {len(jobs)} | LLM: {'wyłączony' if args.no_llm else ('włączony' if settings.LLM_ENABLED else 'wyłączony (brak konfiguracji)')}",
          flush=True)

    stats = {"OK": 0, "POMINIĘTO": 0, "BŁĄD": 0}
    if args.workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            results = ex.map(_one, jobs)
            for src, st, info, dt in results:
                stats[st] += 1
                print(f"[{st}] {src.relative_to(inp)} ({info}, {dt:.1f}s)", flush=True)
    else:
        for j in jobs:
            src, st, info, dt = _one(j)
            stats[st] += 1
            print(f"[{st}] {src.relative_to(inp)} ({info}, {dt:.1f}s)", flush=True)
    print(f"Gotowe: {stats['OK']} OK, {stats['POMINIĘTO']} pominiętych, {stats['BŁĄD']} błędów")
    return 0


if __name__ == "__main__":
    sys.exit(main())
