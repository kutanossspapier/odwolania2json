"""Pamięć nauczonych reguł: wzorce zatwierdzone przez człowieka po korekcie pola.

Plik config/learned/patterns.yaml jest czytelny i edytowalny; przy ładowaniu klas wzorce stąd
trafiają NA POCZĄTEK listy `patterns` odpowiedniego pola (pierwszy pasujący wygrywa), więc poprawka
działa od razu, bez zmiany głównych plików klas i bez LLM. Każdy wpis można cofnąć.

Format:
  odwolanie:
    pelnomocnik_imie_nazwisko:
      - pattern: 'Pełnomocnik\\s+odwołującego\\s*:?\\s*(...)'     # tekst albo {regex, split, item}
        added: '2026-11-21 22:10:00'
        source: 'poprawka #3'
"""
from __future__ import annotations

import time

import yaml

from . import settings

PATH = settings.ROOT / "config" / "learned" / "patterns.yaml"
HEADER = ("# Reguły nauczone z poprawek człowieka (patrz zakładka „Baza przykładów”).\n"
          "# Wzorce z tego pliku są dopisywane na początek listy `patterns` pola. Wpis można usunąć w aplikacji lub ręcznie.\n")


def load() -> dict:
    if not PATH.exists():
        return {}
    return yaml.safe_load(PATH.read_text(encoding="utf-8")) or {}


def _save(data: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(HEADER + yaml.safe_dump(data, allow_unicode=True, sort_keys=True, width=1000),
                    encoding="utf-8")


def merge_into(d: dict) -> None:
    """Dopisuje nauczone wzorce na początek pól w słowniku klasy (wczytanym z YAML)."""
    learned = load().get(d.get("name"), {})
    if not learned:
        return
    for f in d.get("fields", []):
        extra = [e["pattern"] for e in learned.get(f["name"], []) if e.get("pattern")]
        if extra:
            f["patterns"] = extra + list(f.get("patterns", []))


def add(class_name: str, field: str, patterns: list, source: str) -> int:
    """Zapisuje wzorce (bez duplikatów). Zwraca liczbę dodanych."""
    data = load()
    items = data.setdefault(class_name, {}).setdefault(field, [])
    have = [e["pattern"] for e in items]
    n = 0
    for p in patterns:
        if p not in have:
            items.insert(0, {"pattern": p, "added": time.strftime("%Y-%m-%d %H:%M:%S"), "source": source})
            n += 1
    _save(data)
    return n


def remove(class_name: str, field: str, index: int) -> None:
    data = load()
    items = data.get(class_name, {}).get(field, [])
    if not 0 <= index < len(items):
        raise ValueError("nie ma takiego wpisu")
    items.pop(index)
    if not items:
        data[class_name].pop(field)
    if not data.get(class_name):
        data.pop(class_name, None)
    _save(data)


def listing() -> list[dict]:
    return [{"class_name": c, "field": f, "index": i, "pattern": e["pattern"], "added": e.get("added", ""),
             "source": e.get("source", "")}
            for c, fields in load().items() for f, items in fields.items() for i, e in enumerate(items)]
