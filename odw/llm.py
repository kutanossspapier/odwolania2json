"""LLM jako UZUPEŁNIENIE regexów (API zgodne z OpenAI: Ollama, vLLM, ...).

Zasady:
  * regex zawsze działa pierwszy; LLM dostaje tylko pola, których regex nie znalazł
    (albo ma niską pewność) - jedno wywołanie na dokument,
  * odpowiedź to JSON walidowany schematem Pydantic (każde pole osobno - błędne
    pole jest odrzucane, reszta zostaje),
  * wartość tekstowa, której nie ma w dokumencie, dostaje niską pewność
    (ochrona przed halucynacjami) i trafia do "do sprawdzenia",
  * każdy błąd LLM (brak serwera, zły JSON) jest tylko ostrzeżeniem - wynik
    regexowy zostaje bez zmian.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from pydantic import TypeAdapter, ValidationError

from . import settings
from .classes import DocClass, FieldSpec
from .engine import Extraction, FieldResult, set_path
from .extract import PdfText
from .locate import locate

CONF_LLM_FOUND_IN_TEXT = 0.8
CONF_LLM_NOT_IN_TEXT = 0.45
LOW_CONFIDENCE = 0.75            # poniżej tego pole jest "do sprawdzenia" (i kandydat dla LLM)

_PY_TYPE = {"string": str, "date": str, "integer": int, "number": float}

SYSTEM_PROMPT = (
    "Jesteś precyzyjnym ekstraktorem danych z polskich dokumentów zamówień publicznych. "
    "Odpowiadasz WYŁĄCZNIE jednym obiektem JSON. Dla każdego żądanego pola podaj wartość "
    "dokładnie tak, jak występuje w dokumencie, albo null, jeśli dokument jej nie zawiera. "
    "Nie zgaduj i nie wymyślaj. Daty zapisuj jako RRRR-MM-DD."
)


def make_client():
    from openai import OpenAI
    return OpenAI(base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY,
                  timeout=settings.LLM_TIMEOUT)


def eligible(spec: FieldSpec) -> bool:
    """Pola, które LLM może uzupełnić (pola per_block i słowniki wyliczamy regułami)."""
    return spec.method in {"llm", "regex", "rules"} and not spec.per_block


def needs_llm(spec: FieldSpec, res: FieldResult) -> bool:
    if not eligible(spec):
        return False
    if spec.method == "llm":
        return True
    # regex/rules: TYLKO gdy nic nie znaleziono. Niska pewność (np. zła suma kontrolna)
    # nie wystarcza - LLM nie może nadpisać wartości znalezionej regexem.
    return res.status == "brak"


def _field_type(spec: FieldSpec):
    t = _PY_TYPE.get(spec.type, str)
    return list[t] if spec.multiple else t


def build_prompt(cls: DocClass, specs: list[FieldSpec], doc: PdfText,
                 examples: list[dict] | None = None) -> str:
    lines = [f"Typ dokumentu: {cls.title}. {cls.llm_hint.strip()}", "", "Pola do wyciągnięcia:"]
    for s in specs:
        kind = {"integer": "liczba całkowita", "number": "liczba", "date": "data RRRR-MM-DD"}.get(s.type, "tekst")
        if s.multiple:
            kind = f"lista ({kind})"
        lines.append(f'- "{s.name}" ({kind}): {s.description}')
    if examples:
        lines += ["", "Przykłady poprawionych wartości z podobnych dokumentów:"]
        for ex in examples:
            lines.append(f'- pole "{ex["field"]}": fragment: «{ex["snippet"]}» -> poprawna wartość: {json.dumps(ex["value"], ensure_ascii=False)}')
    lines += ["", "Dokument:", "\"\"\"", doc.text[:settings.LLM_MAX_CHARS], "\"\"\"", "",
              "Zwróć JSON o dokładnie tych kluczach: " + ", ".join(f'"{s.name}"' for s in specs) + "."]
    return "\n".join(lines)


def parse_response(raw: str) -> dict:
    """Wyciąga obiekt JSON z odpowiedzi modelu (także gdy owinięty w ```json ... ```)."""
    raw = raw.strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("odpowiedź nie zawiera obiektu JSON")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("odpowiedź nie jest obiektem JSON")
    return data


def _in_text(value, text_norm: str) -> bool:
    if isinstance(value, (int, float)):
        return True                        # liczb nie weryfikujemy tekstowo
    return " ".join(str(value).split()).casefold() in text_norm


def fill_missing(doc: PdfText, cls: DocClass, ex: Extraction, client=None,
                 examples: list[dict] | None = None) -> list[str]:
    """Uzupełnia luki w `ex` przez LLM. Zwraca listę nazw pól wypełnionych przez LLM."""
    by_name = {f.name: f for f in ex.fields}
    specs = [s for s in cls.fields if needs_llm(s, by_name[s.name])]
    if not specs:
        return []
    try:
        client = client or make_client()
        resp = client.chat.completions.create(
            model=settings.LLM_MODEL, temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": build_prompt(cls, specs, doc, examples)}])
        data = parse_response(resp.choices[0].message.content or "")
    except Exception as exc:               # brak serwera, timeout, zły JSON...
        for s in specs:
            by_name[s.name].warnings.append(f"LLM niedostępny: {type(exc).__name__}")
        return []

    text_norm = " ".join(doc.text.split()).casefold()
    filled = []
    for s in specs:
        if s.name not in data or data[s.name] in (None, "", []):
            continue
        try:
            value = TypeAdapter(Optional[_field_type(s)]).validate_python(data[s.name])
        except ValidationError:
            by_name[s.name].warnings.append("LLM zwrócił wartość o złym typie - odrzucona")
            continue
        if s.type == "date":
            from .normalize import date_pl
            value = date_pl(value) if isinstance(value, str) else None
        if s.method == "rules" and s.type == "integer" and s.rules:
            allowed = {r.value for r in s.rules}      # np. powód odwołania tylko 1-12
            if value not in allowed:
                continue
        if value in (None, ""):
            continue
        res = by_name[s.name]
        values = value if isinstance(value, list) else [value]
        ok = all(_in_text(v, text_norm) for v in values)
        res.value, res.method, res.status = value, "llm", "ok"
        res.confidence = CONF_LLM_FOUND_IN_TEXT if ok else CONF_LLM_NOT_IN_TEXT
        res.raw = " | ".join(str(v) for v in values)
        if not ok:
            res.warnings.append("wartości nie znaleziono dosłownie w tekście - sprawdź")
        if s.type != "integer":
            res.page, res.rects = locate(doc, str(values[0]))
        if s.multiple and isinstance(value, list):
            for i, v in enumerate(value):
                set_path(ex.json, s.path, v, item=i)
        else:
            set_path(ex.json, s.path, value)
        filled.append(s.name)

    # pola słownikowe zależne od uzupełnionych (np. czynnosc_opis z czynnosc_powod_id)
    for s in cls.fields:
        if s.method == "lookup" and s.from_field in filled:
            parent = by_name[s.from_field]
            v = s.table.get(str(parent.value))
            if v is not None:
                r = by_name[s.name]
                r.value, r.confidence, r.status = v, parent.confidence, "ok"
                set_path(ex.json, s.path, v)
    return filled


def classify_with_llm(doc: PdfText, classes: list[DocClass], client=None) -> str | None:
    """Rozstrzyga niepewną klasyfikację. Zwraca nazwę klasy albo None ("żadna")."""
    options = "\n".join(f'- "{c.name}": {c.title}. {c.llm_hint.strip()}' for c in classes)
    prompt = (f"Do którego typu należy dokument? Dostępne typy:\n{options}\n"
              '- "none": dokument nie należy do żadnego z powyższych typów\n\n'
              f'Dokument:\n"""\n{doc.head(2)[:4000]}\n"""\n\nOdpowiedz JSON-em: {{"class": "<nazwa lub none>"}}')
    try:
        client = client or make_client()
        resp = client.chat.completions.create(
            model=settings.LLM_MODEL, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
        name = parse_response(resp.choices[0].message.content or "").get("class")
    except Exception:
        return None
    return name if name in {c.name for c in classes} else None
