"""Wersja konsolowa: sprawdza dokument po dokumencie, co jest dobrze, a co źle (na danych UZP z oczekiwanymi JSON-ami).

Użycie:
  python -m odw.check                    # wszystkie dokumenty, tylko regexy
  python -m odw.check --only odwolanie   # jeden typ
  python -m odw.check --bad              # pokaż tylko dokumenty z błędami
  python -m odw.check --fields           # wypisz też poprawne pola
  python -m odw.check --llm              # z LLM (wg .env)
  python -m odw.check ścieżka/do/dane    # inny katalog z danymi
Kody wyjścia: 0 = wszystko zgodne, 1 = są różnice.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .classes import load_classes
from .evaluate import DEFAULT_DANE, Counts, collect_cases, compare
from .pipeline import process

OK, BAD, WARN, RST = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sprawdza dokument po dokumencie: co dobrze, co źle")
    ap.add_argument("dane", nargs="?", default=str(DEFAULT_DANE))
    ap.add_argument("--only", help="odwolanie|pelnomocnictwo|przelew")
    ap.add_argument("--bad", action="store_true", help="tylko dokumenty z błędami")
    ap.add_argument("--fields", action="store_true", help="wypisz też poprawne pola")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    ok_c, bad_c, warn_c, rst = ("", "", "", "") if args.no_color or not sys.stdout.isatty() else (OK, BAD, WARN, RST)

    classes = load_classes()
    total, docs_ok, docs_n, n_warn = Counts(), 0, 0, 0
    for pdf, cls, js in collect_cases(Path(args.dane)):
        if args.only and cls != args.only:
            continue
        res = process(pdf, classes, max_pages=None if cls else 3, use_llm=args.llm)
        exp = json.loads(js.read_text(encoding="utf-8")) if js else {}
        events = compare(exp, res.json)
        good = [e for e in events if e[1] == "tp"]
        wrong = [e for e in events if e[1] != "tp"]
        got_cls = res.classification.class_name
        cls_ok = got_cls == cls
        docs_n += 1
        perfect = cls_ok and not wrong
        docs_ok += perfect
        for _, kind, *_r in events:
            setattr(total, kind, getattr(total, kind) + 1)
        if args.bad and perfect and not any(f.warnings for f in (res.extraction.fields if res.extraction else [])):
            continue
        mark = f"{ok_c}✓{rst}" if perfect else f"{bad_c}✗{rst}"
        label = f"{pdf.parent.name}/{pdf.name}"
        print(f"{mark} {label:60s} typ: {got_cls or '—'}"
              + ("" if cls_ok else f"  {bad_c}(oczekiwano: {cls or 'brak klasy'}){rst}")
              + f"  pola OK: {len(good)}/{len(good) + len({e[4] for e in wrong})}")
        if args.fields:
            for _, _, e, _, full in good:
                print(f"    {ok_c}✓{rst} {full} = {e!r}")
        warned = [f for f in (res.extraction.fields if res.extraction else []) if f.warnings]
        n_warn += len(warned)
        for f in warned:                       # wartość może się zgadzać ze wzorcem, a walidator i tak ją podważa
            print(f"    {warn_c}⚠{rst} {f.path} = {f.value!r}: {'; '.join(f.warnings)}")
        shown = set()
        for _, kind, e, p, full in wrong:
            if full in shown:
                continue
            shown.add(full)
            print(f"    {bad_c}✗{rst} {full}: oczekiwano {e!r}, jest {p!r}")
    print(f"\nDokumenty bez błędów: {docs_ok}/{docs_n} | pola: TP={total.tp} FP={total.fp} FN={total.fn} | F1={total.f1:.3f}")
    print(f"Ostrzeżenia walidatora (wartość zgodna ze wzorcem, ale np. zła suma kontrolna): {n_warn}")
    return 0 if docs_ok == docs_n else 1


if __name__ == "__main__":
    sys.exit(main())
