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
import time
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
    "Nie zgaduj i nie wymyślaj: wartość musi dosłownie występować w dokumencie. "
    "Gdy pola nie ma w dokumencie, wpisz null. Daty zapisuj jako RRRR-MM-DD."
)


def make_client():
    from openai import OpenAI
    # max_retries=0: ponawianie robimy sami w chat_json (żeby kontrolować czas i komunikaty)
    return OpenAI(base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY,
                  timeout=settings.LLM_TIMEOUT, max_retries=0)


def explain_error(exc: Exception) -> str:
    """Zamienia błąd klienta na zrozumiałą wskazówkę."""
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    if status in (401, 403):
        return "serwer odrzucił klucz API (sprawdź LLM_API_KEY)"
    if status == 404:
        return "nie znaleziono modelu lub adresu (sprawdź LLM_MODEL i LLM_BASE_URL)"
    if status == 429:
        return "limit zapytań u dostawcy (spróbuj później)"
    if name in ("APIConnectionError", "APITimeoutError") or isinstance(exc, (ConnectionError, TimeoutError)):
        return "brak połączenia z serwerem LLM (adres, internet, czas oczekiwania)"
    return f"{name}: {str(exc)[:120]}"


def chat_json(client, messages: list[dict]) -> str:
    """Jedno wywołanie czatu z odpowiedzią JSON.

    - 429 / 5xx / błędy sieci: ponawiamy z rosnącą przerwą (LLM_RETRIES razy),
    - 400 przy response_format: część dostawców go nie zna, więc próbujemy bez niego,
    - pozostałe błędy (zły klucz, zły model) kończą od razu.
    """
    use_format, last = True, None
    for attempt in range(settings.LLM_RETRIES + 1):
        kw = dict(model=settings.LLM_MODEL, temperature=0, messages=messages)
        if use_format:
            kw["response_format"] = {"type": "json_object"}
        try:
            resp = client.chat.completions.create(**kw)
            return resp.choices[0].message.content or ""
        except Exception as exc:
            last = exc
            status = getattr(exc, "status_code", None)
            if status == 400 and use_format:
                use_format = False
                continue
            transient = (status in (429, 500, 502, 503, 504)
                         or type(exc).__name__ in ("APIConnectionError", "APITimeoutError")
                         or isinstance(exc, (ConnectionError, TimeoutError)))
            if not transient or attempt >= settings.LLM_RETRIES:
                raise
            time.sleep(min(2 ** attempt, 8))
    raise last


def eligible(spec: FieldSpec) -> bool:
    """Pola, które LLM może uzupełnić (słowniki wyliczamy regułami)."""
    return spec.llm_fill and spec.method in {"llm", "regex", "rules"}


def needs_llm(spec: FieldSpec, res: FieldResult) -> bool:
    if not eligible(spec):
        return False
    if spec.method == "llm":
        return True
    # regex/rules: TYLKO gdy nic nie znaleziono. Niska pewność (np. zła suma kontrolna)
    # nie wystarcza - LLM nie może nadpisać wartości znalezionej regexem.
    return res.status == "brak"


def review_candidates(cls: DocClass, by_name: dict) -> list[FieldSpec]:
    """Pola, w których regex COŚ znalazł, ale warto to sprawdzić: niska pewność, ostrzeżenia walidacji
    albo pole, które człowiek już wcześniej poprawiał. Tylko pola jednowartościowe."""
    try:
        from . import feedback
        corrected = feedback.corrected_fields(cls.name)
    except Exception:
        corrected = set()
    out = []
    for s in cls.fields:
        r = by_name[s.name]
        if not eligible(s) or s.method != "regex" or s.per_block or s.multiple:
            continue
        if s.if_found is not None or s.default is not None:      # flagi 0/1 i wartości domyślne - nie dla LLM
            continue
        if r.status != "ok" or r.value in (None, ""):
            continue
        if settings.LLM_REVIEW_ALL or r.confidence < LOW_CONFIDENCE or r.warnings or s.name in corrected:
            out.append(s)
    return out


def _same_value(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) < 1e-6
    return " ".join(str(a).split()).casefold() == " ".join(str(b).split()).casefold()


def _field_type(spec: FieldSpec):
    t = _PY_TYPE.get(spec.type, str)
    if spec.per_block:       # lista: po jednym elemencie na każdy podmiot (blok)
        return list[list[t]] if spec.multiple else list[Optional[t]]
    return list[t] if spec.multiple else t


def _clean_desc(desc: str) -> str:
    """Usuwa z opisu przykładowe wartości ("..., np. ZP.039.2025") - małe modele je przepisują."""
    d = re.split(r",?\s*np\.\s", desc)[0].strip()
    if d.count("(") > d.count(")"):      # przykład był w nawiasie - domykamy go
        d += ")"
    return d if d.endswith((".", ")")) or not d else d + "."


def _normalize_value(spec: FieldSpec, value):
    """Stosuje normalizatory z YAML do tekstowych wartości z LLM (jak przy regexach)."""
    if not spec.normalize:
        return value
    from .normalize import apply

    def one(v):
        return apply(spec.normalize, v) if isinstance(v, str) else v
    if isinstance(value, list):
        return [[one(x) for x in v] if isinstance(v, list) else one(v) for v in value]
    return one(value)


def _coerce(spec: FieldSpec, v):
    """Łagodna konwersja typów: model często zwraca liczbę jako tekst ('17,00 PLN', '1')."""
    if isinstance(v, list):
        return [_coerce(spec, x) for x in v]
    if isinstance(v, str):
        if spec.type == "number":
            from .normalize import amount
            m = re.search(r"\d[\d  ]*[.,]?\d*", v)
            return amount(m.group(0)) if m else v
        if spec.type == "integer":
            m = re.fullmatch(r"\s*(\d+)\s*", v)
            return int(m.group(1)) if m else v
    return v


def _flatten(v):
    if isinstance(v, list):
        for x in v:
            yield from _flatten(x)
    elif v is not None:
        yield v


def build_prompt(cls: DocClass, specs: list[FieldSpec], doc: PdfText,
                 examples: list[dict] | None = None, hints: list[dict] | None = None,
                 current: dict | None = None) -> str:
    lines = [f"Typ dokumentu: {cls.title}. {cls.llm_hint.strip()}", "", "Pola do wyciągnięcia:"]
    for s in specs:
        kind = {"integer": "liczba całkowita", "number": "liczba", "date": "data RRRR-MM-DD"}.get(s.type, "tekst")
        if s.per_block and s.multiple:
            kind = f"lista list ({kind}) - po jednej liście na każdy podmiot"
        elif s.per_block:
            kind = f"lista ({kind}) - po jednej wartości na każdy podmiot (np. każdego zamawiającego), null gdy brak"
        elif s.multiple:
            kind = f"lista ({kind})"
        line = f'- "{s.name}" ({kind}): {_clean_desc(s.description)}'
        if current and s.name in current:     # tryb weryfikacji: model widzi, co odczytały reguły
            line += (f' Reguły odczytały: {json.dumps(current[s.name], ensure_ascii=False)}. Sprawdź to z dokumentem: '
                     'jeśli to błąd (zła osoba, ucięty lub za długi fragment), podaj poprawną wartość; jeśli jest dobrze, powtórz ją.')
        lines.append(line)
    if examples:
        lines += ["", "Przykłady poprawionych wartości z podobnych dokumentów:"]
        for ex in examples:
            lines.append(f'- pole "{ex["field"]}": fragment: «{ex["snippet"]}» -> poprawna wartość: {json.dumps(ex["value"], ensure_ascii=False)}'
                         + (f' (uwaga człowieka: {ex["reason"]})' if ex.get("reason") else ""))
    if hints:
        lines += ["", "Wskazówki od człowieka, nauczone z wcześniejszych poprawek (stosuj je):"]
        lines += [f'- pole "{h["field"]}": {h["hint"]}' for h in hints]
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
                 examples: list[dict] | None = None, hints: list[dict] | None = None,
                 review: bool = False) -> list[str]:
    """Uzupełnia luki w `ex` przez LLM. Zwraca listę nazw pól wypełnionych przez LLM."""
    by_name = {f.name: f for f in ex.fields}
    specs = [s for s in cls.fields if needs_llm(s, by_name[s.name])]
    current = {}
    if review:                             # opcjonalnie: LLM sprawdza także to, co regex znalazł (i może poprawić)
        for s in review_candidates(cls, by_name):
            if s not in specs:
                specs.append(s)
                current[s.name] = by_name[s.name].value
    if not specs:
        return []
    if examples is None:                   # poprawki człowieka oznaczone jako przykłady (pamięć aplikacji)
        try:
            from . import feedback
            examples = feedback.examples_for(cls.name, [s.name for s in specs], doc.text)
            hints = feedback.hints_for(cls.name, [s.name for s in specs]) if hints is None else hints
        except Exception:
            examples, hints = [], hints or []
    try:
        client = client or make_client()
        data = parse_response(chat_json(client, [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(cls, specs, doc, examples, hints, current)}]))
    except Exception as exc:               # brak serwera, zły klucz, timeout, zły JSON...
        for s in specs:
            by_name[s.name].warnings.append(f"LLM niedostępny: {explain_error(exc)}")
        return []

    text_norm = " ".join(doc.text.split()).casefold()
    filled = []
    for s in specs:
        if s.name not in data or data[s.name] in (None, "", []):
            continue
        try:
            value = TypeAdapter(Optional[_field_type(s)]).validate_python(_coerce(s, data[s.name]))
        except ValidationError:
            by_name[s.name].warnings.append("LLM zwrócił wartość o złym typie - odrzucona")
            continue
        if s.type == "date":
            from .normalize import date_pl
            if isinstance(value, list):
                value = [date_pl(v) if isinstance(v, str) else None for v in value]
            else:
                value = date_pl(value) if isinstance(value, str) else None
        if s.method == "rules" and s.type == "integer" and s.rules and not isinstance(value, list):
            allowed = {r.value for r in s.rules}      # np. powód odwołania tylko 1-12
            if value not in allowed:
                continue
        if value in (None, ""):
            continue
        res = by_name[s.name]
        values = list(_flatten(value))
        if not values:
            continue
        # ochrona przed halucynacjami: tekstowa wartość musi dosłownie być w dokumencie
        # (sprawdzamy PRZED normalizacją; daty i liczby pomijamy - model podaje je w innym zapisie)
        if s.type == "string" and not all(_in_text(v, text_norm) for v in values):
            res.warnings.append("LLM podał wartość, której nie ma w dokumencie - odrzucona")
            continue
        value = _normalize_value(s, value)
        values = list(_flatten(value))
        if not values:
            continue
        if s.name in current:
            if _same_value(current[s.name], value):
                continue                 # model potwierdził wartość z reguł
            res.warnings.append(f"LLM poprawił wartość z reguł (było: {current[s.name]})")
        res.value, res.method, res.status = value, "llm", "ok"
        res.confidence = CONF_LLM_FOUND_IN_TEXT
        res.raw = " | ".join(str(v) for v in values)
        if s.type != "integer":
            res.page, res.rects = locate(doc, str(values[0]))
        if s.per_block:
            for i, v in enumerate(value):
                if s.multiple:
                    for j, x in enumerate(v or []):
                        set_path(ex.json, s.path, x, item=(i, j))
                elif v is not None:
                    set_path(ex.json, s.path, v, item=i)
        elif s.multiple and isinstance(value, list):
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
        name = parse_response(chat_json(client, [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}])).get("class")
    except Exception:
        return None
    return name if name in {c.name for c in classes} else None
