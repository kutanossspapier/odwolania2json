"""Ocena jakości: F1 ogólnie, per typ dokumentu i per pole, na zbiorze treningowym UZP.

Użycie:
  python -m odw.evaluate                      # podsumowanie
  python -m odw.evaluate --errors 3           # + po 3 przykłady błędów na pole
  python -m odw.evaluate --only przelew       # tylko jeden typ

Zasady liczenia (na poziomie pojedynczych wartości w JSON-ie):
  oczekiwane null  i wynik null     -> nic (poprawnie puste)
  oczekiwane wartość i wynik równy  -> TP
  oczekiwane null  i wynik wartość  -> FP
  oczekiwane wartość i wynik null   -> FN
  obie wartości, ale różne          -> FP + FN
Dokument negatywny: oczekiwany pusty JSON, każda zwrócona wartość to FP.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from . import settings
from .classes import load_classes
from .pipeline import process

DEFAULT_DANE = settings.ROOT.parent / "dane-uzp" / "dane"
KINDS = {"odwolanie.pdf": ("odwolanie", "odwolanie.json"),
         "pelnomocnictwo.pdf": ("pelnomocnictwo", "pelnomocnictwo.json"),
         "potwierdzenie_przelewu.pdf": ("przelew", "przelew.json")}
IMPLEMENTED = {"regex", "rules", "lookup"}


def flatten(o, prefix: str = "") -> dict:
    out = {}
    if isinstance(o, dict):
        for k, v in o.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = o
    return out


def generic(path: str) -> str:
    return re.sub(r"\[\d+\]", "[]", path)


def same(a, b) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) < 1e-6
    return " ".join(str(a).split()) == " ".join(str(b).split())


class Counts:
    def __init__(self):
        self.tp = self.fp = self.fn = 0

    def add(self, other: "Counts"):
        self.tp += other.tp; self.fp += other.fp; self.fn += other.fn

    @property
    def p(self): return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0
    @property
    def r(self): return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0
    @property
    def f1(self):
        p, r = self.p, self.r
        return 2 * p * r / (p + r) if p + r else 0.0


def compare(exp: dict, pred: dict):
    """Zwraca listę (ścieżka_generyczna, 'tp'|'fp'|'fn', exp, pred, ścieżka_pełna)."""
    fe, fp_ = flatten(exp), flatten(pred)
    events = []
    for path in sorted(set(fe) | set(fp_)):
        e, p = fe.get(path), fp_.get(path)
        g = generic(path)
        if e is None and p is None:
            continue
        if e is not None and p is not None and same(e, p):
            events.append((g, "tp", e, p, path))
        elif e is not None and p is not None:
            events.append((g, "fp", e, p, path)); events.append((g, "fn", e, p, path))
        elif p is not None:
            events.append((g, "fp", e, p, path))
        else:
            events.append((g, "fn", e, p, path))
    return events


def collect_cases(dane: Path):
    """(pdf, klasa_oczekiwana|None, plik_json|None)"""
    cases = []
    for d in sorted(dane.glob("przyklad_*")):
        for pdf in sorted(d.glob("*.pdf")):
            if pdf.name in KINDS:
                cls, js = KINDS[pdf.name]
                cases.append((pdf, cls, d / js))
            else:
                cases.append((pdf, None, None))
    for pdf in sorted((dane / "negatywne").glob("*.pdf")):
        cases.append((pdf, None, None))
    return cases


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dane", nargs="?", default=str(DEFAULT_DANE))
    ap.add_argument("--only", help="tylko ten typ (odwolanie|pelnomocnictwo|przelew)")
    ap.add_argument("--errors", type=int, default=0, help="ile przykładów błędów na pole")
    ap.add_argument("--save", default="reports/ostatni_eval.json")
    ap.add_argument("--llm", action="store_true",
                    help="włącz LLM jako uzupełnienie (wymaga działającego serwera z .env)")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    classes = load_classes()
    method_of = {}
    for c in classes:
        for f in c.fields:
            method_of[(c.name, generic(f.path))] = f.method

    per_field: dict[tuple, Counts] = defaultdict(Counts)
    per_type: dict[str, Counts] = defaultdict(Counts)
    errors: dict[tuple, list] = defaultdict(list)
    cls_ok = cls_total = llm_filled = 0

    for pdf, cls, js in collect_cases(Path(args.dane)):
        if args.only and cls != args.only:
            continue
        res = process(pdf, classes, max_pages=None if cls else 3, use_llm=args.llm)
        llm_filled += len(res.llm_fields)
        cls_total += 1
        cls_ok += res.classification.class_name == cls
        exp = json.loads(js.read_text(encoding="utf-8")) if js else {}
        name = cls or "negatyw"
        for g, kind, e, p, full in compare(exp, res.json):
            key = (name, g)
            c = per_field[key]
            setattr(c, kind, getattr(c, kind) + 1)
            if kind != "tp":
                errors[key].append(f"{pdf.parent.name}: oczekiwano {e!r}, wynik {p!r}")

    # --- agregacja
    overall, overall_impl = Counts(), Counts()
    for (t, g), c in per_field.items():
        per_type[t].add(c)
        overall.add(c)
        if method_of.get((t, g), "llm") in IMPLEMENTED or t == "negatyw":
            overall_impl.add(c)

    print(f"\nTryb: {'regex + LLM' if args.llm else 'tylko regex (bez LLM)'}"
          + (f"; pól uzupełnionych przez LLM: {llm_filled}" if args.llm else ""))
    print(f"Klasyfikacja: {cls_ok}/{cls_total} poprawnie")
    print(f"F1 WSZYSTKIE pola: {overall.f1:.3f}  "
          f"(P={overall.p:.3f} R={overall.r:.3f})")
    print(f"F1 pola z regex/regułami:                      {overall_impl.f1:.3f}  "
          f"(P={overall_impl.p:.3f} R={overall_impl.r:.3f})")
    print("\nPer typ dokumentu:")
    for t, c in sorted(per_type.items()):
        print(f"  {t:15s} F1={c.f1:.3f}  P={c.p:.3f} R={c.r:.3f}  (TP={c.tp} FP={c.fp} FN={c.fn})")
    print("\nPer pole (od najsłabszych):")
    rows = sorted(per_field.items(), key=lambda kv: kv[1].f1)
    for (t, g), c in rows:
        m = method_of.get((t, g), "llm")
        print(f"  {c.f1:5.2f}  {t:15s} {g:50s} [{m:6s}] TP={c.tp:3d} FP={c.fp:3d} FN={c.fn:3d}")
        for line in errors[(t, g)][:args.errors]:
            print(f"           ! {line[:170]}")

    out = Path(settings.ROOT / args.save)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "classification": {"ok": cls_ok, "total": cls_total},
        "f1_all": overall.f1, "f1_regex": overall_impl.f1,
        "per_type": {t: {"f1": c.f1, "p": c.p, "r": c.r} for t, c in per_type.items()},
        "per_field": [{"type": t, "field": g, "method": method_of.get((t, g), "llm"),
                       "f1": c.f1, "tp": c.tp, "fp": c.fp, "fn": c.fn}
                      for (t, g), c in rows],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nZapisano: {out}")


if __name__ == "__main__":
    main()
