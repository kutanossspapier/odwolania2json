"""Wczytywanie klas dokumentów z config/classes/*.yaml.

Nowy typ dokumentu = nowy plik YAML. Kod nie zna listy klas ani pól na sztywno.

Plik klasy zawiera:
  recognition  - słowa kluczowe do rozpoznania typu
  sections     - fragmenty tekstu (np. blok "Odwołujący:") do zawężania wzorców
  fields       - pola do wyciągnięcia (regex / rules / lookup / llm)
  output       - szkielet wynikowego JSON-a (z null-ami) w formacie UZP
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import settings


@dataclass
class Keyword:
    regex: re.Pattern
    weight: float


@dataclass
class PatternSpec:
    regex: re.Pattern
    group: int = 1
    groups: list[int] | None = None   # kilka wartości z jednego trafienia (np. dwa podpisy)


@dataclass
class RuleSpec:
    regex: re.Pattern
    value: object


@dataclass
class SectionSpec:
    name: str
    start: re.Pattern
    end: re.Pattern | None
    split: re.Pattern | None = None   # dzieli sekcję na bloki (np. dwóch zamawiających)
    max_chars: int = 4000


@dataclass
class FieldSpec:
    name: str
    path: str                      # ścieżka w wynikowym JSON, np. Odwolujacy[0].nip
    type: str = "string"           # string | date | number | integer
    method: str = "regex"          # regex | rules | lookup | llm
    description: str = ""
    section: str | None = None
    patterns: list[PatternSpec] = field(default_factory=list)
    normalize: list[str] = field(default_factory=list)
    validators: list[str] = field(default_factory=list)
    multiple: bool = False         # wiele wartości -> lista (ścieżka z [])
    per_block: bool = False        # uruchom osobno w każdym bloku sekcji (element listy = blok)
    min_blocks: int = 1            # pomiń pole, jeśli bloków jest mniej
    if_found: object = None        # wartość stała, gdy wzorzec znaleziony
    default: object = None         # wartość, gdy nie znaleziono
    source_pattern: re.Pattern | None = None   # rules: skąd brać tekst do reguł
    rules: list[RuleSpec] = field(default_factory=list)
    from_field: str | None = None  # lookup: z którego pola
    table: dict = field(default_factory=dict)


@dataclass
class DocClass:
    name: str
    title: str
    description: str
    min_score: float
    llm_hint: str
    keywords: list[Keyword]
    sections: dict[str, SectionSpec]
    fields: list[FieldSpec]
    output: dict
    raw: dict
    file: str


_macros_cache: dict[str, str] | None = None


def load_macros() -> dict[str, str]:
    global _macros_cache
    if _macros_cache is None:
        p = settings.ROOT / "config" / "macros.yaml"
        _macros_cache = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _macros_cache


def expand(pattern: str) -> str:
    """<<DATE>> -> wzorzec z config/macros.yaml"""
    macros = load_macros()
    return re.sub(r"<<(\w+)>>", lambda m: macros[m.group(1)], pattern)


def _compile(pattern: str, case_sensitive: bool = False) -> re.Pattern:
    flags = re.MULTILINE | (0 if case_sensitive else re.IGNORECASE)
    return re.compile(expand(pattern), flags)


def _parse_field(d: dict) -> FieldSpec:
    cs = bool(d.get("case_sensitive", False))
    pats = []
    for p in d.get("patterns", []):
        if isinstance(p, str):
            pats.append(PatternSpec(_compile(p, cs)))
        else:
            pats.append(PatternSpec(_compile(p["regex"], cs), int(p.get("group", 1)),
                                    p.get("groups")))
    rules = [RuleSpec(_compile(r["pattern"], cs), r["value"]) for r in d.get("rules", [])]
    sp = d.get("source_pattern")
    return FieldSpec(
        name=d["name"], path=d["path"], type=d.get("type", "string"),
        method=d.get("method", "regex"), description=d.get("description", "").strip(),
        section=d.get("section"), patterns=pats, normalize=d.get("normalize", []),
        validators=d.get("validators", []), multiple=bool(d.get("multiple", False)),
        per_block=bool(d.get("per_block", False)), min_blocks=int(d.get("min_blocks", 1)),
        if_found=d.get("if_found"), default=d.get("default"),
        source_pattern=_compile(sp, cs) if sp else None, rules=rules,
        from_field=d.get("from"), table={str(k): v for k, v in d.get("table", {}).items()},
    )


def load_class(path: Path) -> DocClass:
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    rec = d.get("recognition", {})
    flags = re.IGNORECASE | re.MULTILINE
    kws = [Keyword(re.compile(k["pattern"], flags), float(k.get("weight", 1)))
           for k in rec.get("keywords", [])]
    sections = {
        n: SectionSpec(n, _compile(s["start"], bool(s.get("case_sensitive"))),
                       _compile(s["end"], bool(s.get("case_sensitive"))) if s.get("end") else None,
                       _compile(s["split"], bool(s.get("case_sensitive"))) if s.get("split") else None,
                       int(s.get("max_chars", 4000)))
        for n, s in (d.get("sections") or {}).items()
    }
    fields = [_parse_field(f) for f in d.get("fields", [])]
    return DocClass(d["name"], d.get("title", d["name"]), d.get("description", ""),
                    float(rec.get("min_score", 5)), rec.get("llm_hint", ""),
                    kws, sections, fields, d.get("output", {}), d, path.name)


def load_classes(directory: Path | None = None) -> list[DocClass]:
    directory = directory or settings.CLASSES_DIR
    return [load_class(p) for p in sorted(Path(directory).glob("*.yaml"))]
