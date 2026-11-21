"""Silnik ekstrakcji pól według specyfikacji z YAML.

Metody pól:
  regex  - wzorce próbowane po kolei (pierwszy, który zadziała, wygrywa)
  rules  - uporządkowane reguły słów kluczowych -> wartość (np. powód odwołania)
  lookup - wartość wyliczana ze słownika na podstawie innego pola
  llm    - zostawione dla LLM (w tym kroku: puste)

Każde pole dostaje: wartość, metodę, pewność 0-1 i źródło (strona + prostokąty).
Walidatory (np. suma kontrolna NIP) tylko obniżają pewność - nie odrzucają wartości.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from . import normalize, validators
from .classes import DocClass, FieldSpec
from .extract import PdfText
from .locate import locate

CONF_PATTERN = [0.95, 0.88, 0.8]   # pewność wg numeru trafionego wzorca
CONF_RULES = 0.8
CONF_DEFAULT = 0.6                  # wartość domyślna (nic nie znaleziono)
CONF_VALIDATION_PENALTY = 0.75


@dataclass
class FieldResult:
    name: str
    path: str
    method: str                   # regex | rules | lookup | llm
    value: object = None
    confidence: float = 0.0
    status: str = "brak"          # ok | brak | domyslna | llm
    raw: str | None = None
    page: int | None = None
    rects: list = field(default_factory=list)
    pattern_index: int | None = None
    warnings: list = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Extraction:
    class_name: str
    fields: list[FieldResult]
    json: dict


# ---------------------------------------------------------------- sekcje
def get_blocks(cls: DocClass, name: str, text: str) -> list[str]:
    """Tekst sekcji; jeśli sekcja ma 'split', to lista bloków (np. dwóch zamawiających)."""
    spec = cls.sections.get(name)
    if not spec:
        return []
    m = spec.start.search(text)
    if not m:
        return []
    rest = text[m.end():]
    if spec.end:
        e = spec.end.search(rest)
        if e:
            rest = rest[:e.start()]
    rest = rest[:spec.max_chars]
    if spec.split:
        cuts = list(spec.split.finditer(rest))
        if cuts:   # tekst przed pierwszym cięciem (nagłówek sekcji) odrzucamy
            return [rest[c.end(): cuts[i + 1].start() if i + 1 < len(cuts) else len(rest)]
                    for i, c in enumerate(cuts)]
    return [rest]


# ---------------------------------------------------------------- ścieżki JSON
_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d*)\]")
ANY = -1   # '[]' w ścieżce = "kolejny element listy"


def _parse_path(path: str) -> list:
    out = []
    for key, idx in _TOKEN.findall(path):
        if key:
            out.append(key)
        else:
            out.append(int(idx) if idx != "" else ANY)
    return out


def _nullify(o):
    if isinstance(o, dict):
        return {k: _nullify(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_nullify(v) for v in o]
    return None


def set_path(root: dict, path: str, value, item: int | tuple | None = None) -> None:
    """Ustawia wartość pod ścieżką; kolejne '[]' to kolejne indeksy z `item` (liczba lub krotka)."""
    toks = _parse_path(path)
    idxs = list(item) if isinstance(item, tuple) else [item or 0]
    any_i = 0
    cur = root
    for i, t in enumerate(toks):
        last = i == len(toks) - 1
        if isinstance(t, str):
            if last:
                cur[t] = value
                return
            nxt = toks[i + 1]
            cur = cur.setdefault(t, [] if isinstance(nxt, int) else {})
        else:                      # indeks listy
            if t == ANY:
                idx = idxs[any_i] if any_i < len(idxs) else 0
                any_i += 1
            else:
                idx = t
            while len(cur) <= idx:
                cur.append(_nullify(copy.deepcopy(cur[0])) if cur else {})
            if last:
                cur[idx] = value
                return
            cur = cur[idx]


# ---------------------------------------------------------------- pola
def _confidence(idx: int) -> float:
    return CONF_PATTERN[min(idx, len(CONF_PATTERN) - 1)]


def _finish_value(spec: FieldSpec, raw: str):
    value = normalize.apply(spec.normalize, raw) if spec.normalize else normalize.ws(raw)
    if spec.type == "number" and not isinstance(value, float):
        value = normalize.amount(value) if value is not None else None
    elif spec.type == "integer" and not isinstance(value, int):
        value = normalize.to_int(value) if value is not None else None
    elif spec.type == "date" and value is not None and not spec.normalize:
        value = normalize.date_pl(value)
    return value


def _validate(spec: FieldSpec, value, res: FieldResult) -> None:
    for v in spec.validators:
        ok, msg = validators.REGISTRY[v](value)
        if not ok:
            res.confidence = round(res.confidence * CONF_VALIDATION_PENALTY, 3)
            if msg not in res.warnings:
                res.warnings.append(msg)


def _matches(spec: FieldSpec, text: str):
    """Pierwszy wzorzec, który zadziałał, zwraca [(pozycja, raw, wartość, indeks_wzorca)]."""
    for pi, p in enumerate(spec.patterns):
        found = []
        for m in (p.regex.finditer(text) if spec.multiple else [p.regex.search(text)]):
            if not m:
                continue
            groups = p.groups or [p.group]
            for g in groups:
                raw = m.group(g) if (m.lastindex or 0) >= g else m.group(0)
                if raw is None:
                    continue
                v = raw if spec.if_found is not None else _finish_value(spec, raw)
                if v not in (None, ""):
                    found.append((m.start(g) if (m.lastindex or 0) >= g else m.start(), raw, v, pi))
        if found:
            return found
    return []


def _matches_multi(spec: FieldSpec, text: str):
    """Dla pól 'multiple': suma trafień ze WSZYSTKICH wzorców, w kolejności występowania."""
    found = []
    for pi, p in enumerate(spec.patterns):
        for m in p.regex.finditer(text):
            for g in (p.groups or [p.group]):
                if (m.lastindex or 0) < g:
                    continue
                raw = m.group(g)
                v = _finish_value(spec, raw) if raw is not None else None
                if v not in (None, "") and all(v != f[2] for f in found):
                    found.append((m.start(g), raw, v, pi))
    return sorted(found)


def _apply_found(spec: FieldSpec, found: list, res: FieldResult) -> None:
    """Wpisuje wynik wyszukiwania do pola (dla zwykłych pól i pól 'multiple')."""
    if spec.multiple:
        res.value = [f[2] for f in found]
        res.raw = " | ".join(f[1] for f in found)
        res.pattern_index = found[0][3]
        res.confidence, res.status = _confidence(found[0][3]), "ok"
        for v in res.value:
            _validate(spec, v, res)
        return
    _, raw, value, pi = found[0]
    res.raw, res.pattern_index = raw, pi
    if spec.if_found is not None:
        res.value, res.confidence, res.status = spec.if_found, 0.9, "ok"
        return
    res.value, res.confidence, res.status = value, _confidence(pi), "ok"
    _validate(spec, value, res)


def extract(doc: PdfText, cls: DocClass) -> Extraction:
    text = doc.text
    out = copy.deepcopy(cls.output)
    results: list[FieldResult] = []
    by_name: dict[str, FieldResult] = {}

    for spec in cls.fields:
        res = FieldResult(spec.name, spec.path, spec.method, description=spec.description)
        blocks = [text]
        if spec.section:
            blocks = get_blocks(cls, spec.section, text)
            if not blocks:
                res.warnings.append(f"nie znaleziono sekcji „{spec.section}”")
        scope = blocks[0] if blocks else ""

        if spec.method == "llm":
            res.status = "llm"

        elif spec.method == "regex" and spec.per_block:
            # osobne uruchomienie w każdym bloku; element listy = numer bloku
            if len(blocks) >= spec.min_blocks:
                values = []
                for b in blocks:
                    f = _matches_multi(spec, b) if spec.multiple else _matches(spec, b)
                    sub = FieldResult(spec.name, spec.path, "regex")
                    if f:
                        _apply_found(spec, f, sub)
                    elif spec.default is not None:
                        sub.value, sub.confidence, sub.status = spec.default, CONF_DEFAULT, "domyslna"
                    values.append(sub)
                res.value = [s.value for s in values]
                done = [s for s in values if s.status != "brak"]
                res.confidence = min((s.confidence for s in done), default=0.0)
                res.status = "ok" if done else "brak"
                res.raw = " | ".join(s.raw for s in values if s.raw) or None
                for s in values:
                    res.warnings += [w for w in s.warnings if w not in res.warnings]
                for i, s in enumerate(values):
                    if s.value is None:
                        continue
                    if spec.multiple:      # lista w każdym bloku: [blok][element]
                        for j, v in enumerate(s.value):
                            set_path(out, spec.path, v, item=(i, j))
                    else:
                        set_path(out, spec.path, s.value, item=i)
            results.append(res)
            by_name[spec.name] = res
            if res.raw:
                res.page, res.rects = locate(doc, res.raw.split(" | ")[0])
            continue

        elif spec.method == "regex":
            found = (_matches_multi(spec, scope) if spec.multiple else _matches(spec, scope)) if scope else []
            if found:
                _apply_found(spec, found, res)
            elif spec.default is not None:
                res.value, res.confidence, res.status = spec.default, CONF_DEFAULT, "domyslna"

        elif spec.method == "rules":
            src = scope
            if spec.source_pattern:
                m = spec.source_pattern.search(scope)
                src = (m.group(1) if m and m.lastindex else m.group(0)) if m else ""
            res.raw = normalize.ws(src)[:200] if src else None
            for r in spec.rules:
                if r.regex.search(src or ""):
                    res.value, res.confidence, res.status = r.value, CONF_RULES, "ok"
                    break

        elif spec.method == "lookup":
            parent = by_name.get(spec.from_field or "")
            if parent and parent.value is not None:
                v = spec.table.get(str(parent.value))
                if v is not None:
                    res.value, res.confidence, res.status = v, parent.confidence, "ok"
                    res.raw = f"{spec.from_field} = {parent.value}"

        # źródło w PDF (strona + współrzędne)
        if res.raw and res.method == "regex":
            res.page, res.rects = locate(doc, res.raw.split(" | ")[0])

        by_name[spec.name] = res
        results.append(res)

        if res.value is not None:
            if spec.multiple and isinstance(res.value, list):
                for i, v in enumerate(res.value):
                    set_path(out, spec.path, v, item=i)
            else:
                set_path(out, spec.path, res.value)

    return Extraction(cls.name, results, out)
