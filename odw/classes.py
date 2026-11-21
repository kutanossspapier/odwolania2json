"""Wczytywanie klas dokumentów z config/classes/*.yaml.

Nowy typ dokumentu = nowy plik YAML. Kod nie zna listy klas na sztywno.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import settings


@dataclass
class Keyword:
    regex: re.Pattern
    weight: float


@dataclass
class DocClass:
    name: str
    title: str
    description: str
    min_score: float
    llm_hint: str
    keywords: list[Keyword]
    raw: dict            # cały YAML (pola dojdą w kolejnych krokach)
    file: str


def load_class(path: Path) -> DocClass:
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    rec = d.get("recognition", {})
    flags = re.IGNORECASE | re.MULTILINE
    kws = [Keyword(re.compile(k["pattern"], flags), float(k.get("weight", 1)))
           for k in rec.get("keywords", [])]
    return DocClass(d["name"], d.get("title", d["name"]), d.get("description", ""),
                    float(rec.get("min_score", 5)), rec.get("llm_hint", ""),
                    kws, d, path.name)


def load_classes(directory: Path | None = None) -> list[DocClass]:
    directory = directory or settings.CLASSES_DIR
    return [load_class(p) for p in sorted(Path(directory).glob("*.yaml"))]
