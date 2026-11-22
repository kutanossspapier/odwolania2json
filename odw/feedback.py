"""Ręczna korekta pola -> diagnoza, dlaczego reguły nie zadziałały, i propozycja poprawki konfiguracji YAML.

Użytkownik podaje poprawną wartość pola (i opcjonalnie powód). Moduł:
  1. sprawdza, czy ta wartość w ogóle jest w tekście dokumentu i czy leży w sekcji pola,
  2. testuje po kolei każdy istniejący wzorzec (który nie pasuje, który wyciąga co innego),
  3. BEZ LLM buduje nowy wzorzec z otoczenia wartości (etykieta przed nią) i go weryfikuje,
  4. opcjonalnie prosi LLM o wzorzec (też weryfikowany, z jedną poprawką po błędzie),
  5. sprawdza na zbiorze treningowym, czy poprawka niczego nie psuje (F1 pola przed/po),
  6. zwraca log "co dopisać w którym pliku YAML".
Poprawki NIE są wprowadzane automatycznie - to człowiek decyduje, co trafi do pliku.
"""
from __future__ import annotations

import dataclasses
import json
import re
import sqlite3
import time
from pathlib import Path

from . import engine, settings
from .classes import DocClass, FieldSpec, PatternSpec, _compile, expand, load_classes
from .engine import extract, get_blocks
from .evaluate import collect_cases, compare, generic
from .extract import Page, PdfText, extract_pdf

DB_PATH = settings.ROOT / "data" / "corrections.db"
DANE = settings.ROOT.parent / "dane-uzp" / "dane"


# ---------------------------------------------------------------- pomocnicze
def build_doc(pages: list[dict]) -> PdfText:
    """Dokument z samego tekstu stron (bez współrzędnych) - wystarcza do testowania wzorców."""
    return PdfText("reczne.pdf", [Page(int(p.get("number", i + 1)), 595, 842, p.get("text", ""), [])
                                  for i, p in enumerate(pages)])


def _ws(s: str) -> str:
    return " ".join(str(s).split()).casefold()


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) < 1e-6
    return _ws(a) == _ws(b)


def _flat(v) -> list:
    """Wartość pola (skalar / lista / lista list per blok) -> płaska lista niepustych wartości."""
    if isinstance(v, list):
        return [x for item in v for x in _flat(item)]
    return [] if v in (None, "") else [v]


def _spec_and_raw(cls: DocClass, field: str):
    spec = next((f for f in cls.fields if f.name == field), None)
    raw = next((f for f in cls.raw.get("fields", []) if f.get("name") == field), {})
    return spec, raw


def _norm_expected(spec: FieldSpec, text: str):
    """Wartość wpisana przez użytkownika -> lista wartości po normalizacji pola (jak z regexa)."""
    items = [x.strip() for x in re.split(r"\n|;", text) if x.strip()] if spec.multiple else [text.strip()]
    out = []
    for it in items:
        v = engine._finish_value(spec, it)
        out.append(v if v not in (None, "") else it)
    return out


def _pattern_label(raw_pattern) -> str:
    return raw_pattern if isinstance(raw_pattern, str) else raw_pattern.get("regex", "")


# ---------------------------------------------------------------- szukanie wartości w tekście
_DATE_RE = re.compile(expand(r"<<DATE>>"), re.I)
_AMOUNT_RE = re.compile(expand(r"<<AMOUNT>>"))
_ACCOUNT_RE = re.compile(expand(r"<<ACCOUNT>>"))
_NUM_RE = re.compile(r"\d[\d\s]*\d|\d")
_NAME_LIKE = re.compile(r"[A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźż’'-]+(?: [A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźż’'-]+){1,2}")


def find_literal(spec: FieldSpec, text: str, expected) -> tuple[int, int, str] | None:
    """Gdzie w tekście stoi wartość? Najpierw dosłownie (spacje elastyczne), potem przez normalizację
    (np. wartość 2026-08-12 stoi w tekście jako '12 sierpnia 2026')."""
    words = [re.escape(w) for w in str(expected).split()]
    if words:
        m = re.search(r"\s+".join(words), text, re.I)
        if m:
            return m.start(), m.end(), m.group(0)
    for rx in (_DATE_RE, _AMOUNT_RE, _ACCOUNT_RE, _NUM_RE):
        for m in rx.finditer(text):
            try:
                if _same(engine._finish_value(spec, m.group(0)), expected):
                    return m.start(), m.end(), m.group(0)
            except Exception:
                continue
    return None


# ---------------------------------------------------------------- budowanie wzorca (bez LLM)
def _shape(literal: str, text_after: str) -> str:
    """Wzorzec dla samej wartości. Zgeneralizowany, żeby działał dla innych dokumentów."""
    lit = literal.strip()
    if _DATE_RE.fullmatch(lit):
        return r"<<DATE>>"
    if _ACCOUNT_RE.fullmatch(lit):
        return r"<<ACCOUNT>>"
    if re.fullmatch(expand(r"<<EMAIL>>"), lit):
        return r"<<EMAIL>>"
    if _AMOUNT_RE.fullmatch(lit):
        return r"<<AMOUNT>>"
    if re.fullmatch(r"\d+", lit):
        return rf"\d{{{len(lit)}}}"
    if " " not in lit and "\n" not in lit:        # jeden token: litery/cyfry/znaki w klasach
        out, i = "", 0
        while i < len(lit):
            ch = lit[i]
            j = i
            if ch.isdigit():
                while j < len(lit) and lit[j].isdigit(): j += 1
                out += r"\d+"
            elif ch.isupper():
                while j < len(lit) and lit[j].isupper(): j += 1
                out += r"[A-ZĄĆĘŁŃÓŚŹŻ]+"
            elif ch.isalpha():
                while j < len(lit) and lit[j].isalpha() and not lit[j].isupper(): j += 1
                out += r"[a-ząćęłńóśźż]+"
            else:
                j = i + 1
                out += re.escape(ch)
            i = j
        return out
    if _NAME_LIKE.fullmatch(lit):                  # imię i nazwisko: ogólny wzorzec zamiast "[^\n]+"
        return r"(?-i:<<NAME>>)"
    # tekst z odstępami: do znaku kończącego (przecinek, nawias, kropka, koniec linii) albo następnego słowa
    rest = text_after.lstrip(" \t")
    if not rest or rest[0] == "\n":
        return r"[^\n]+?(?=[ \t]*$)"
    if rest[0] in ",;(.:)":
        return rf"[^\n]+?(?=\s*{re.escape(rest[0])})"
    nxt = re.match(r"\S{1,12}", rest)
    return rf"[^\n]+?(?=\s+{re.escape(nxt.group(0))})" if nxt else r"[^\n]+"


def _label_words(prefix: str, max_words: int = 3) -> list[str]:
    """Ostatnie słowa przed wartością w tej samej linii (bez tokenów z cyframi - te się zmieniają)."""
    toks = prefix.replace(":", " : ").split()
    out = []
    for t in reversed(toks):
        if t == ":":
            continue
        if re.search(r"\d", t) or len(out) >= max_words:
            break
        out.append(t)
    return list(reversed(out))


def build_candidates(text: str, start: int, end: int, literal: str) -> list[tuple[str, str]]:
    """Lista (wzorzec, opis) - od najbardziej do najmniej pewnych. Pierwsza grupa = wartość."""
    line_start = text.rfind("\n", 0, start) + 1
    prefix = text[line_start:start]
    shape = _shape(literal, text[end:end + 30])
    cands = []
    words = _label_words(prefix)
    if words:
        label = r"\s+".join(re.escape(w) for w in words)
        colon = r"\s*:?\s*" if prefix.rstrip().endswith(":") else r"\s+"
        cands.append((rf"{label}{colon}({shape})", f"po etykiecie „{' '.join(words)}”"))
        if len(words) > 1:
            label1 = re.escape(words[-1])
            cands.append((rf"{label1}{colon}({shape})", f"po słowie „{words[-1]}”"))
    if not prefix.strip():                              # wartość zaczyna linię - etykieta w linii powyżej
        prev = text[:max(line_start - 1, 0)].rstrip("\n")
        prev_line = prev[prev.rfind("\n") + 1:].strip()
        if prev_line and len(prev_line) <= 60:
            lab = r"\s+".join(re.escape(w) for w in prev_line.rstrip(":").split())
            cands.append((rf"{lab}\s*:?\s*\n\s*({shape})", f"pod linią „{prev_line}”"))
    after = text[end:end + 40].split("\n")[0]
    if after.strip():
        nxt = re.escape(after.strip()[:10]).replace(r"\ ", r"\s+")
        cands.append((rf"({shape})\s*{nxt}", f"przed tekstem „{after.strip()[:10]}”"))
    return cands


def build_list_candidate(text: str, positions: list[tuple[int, int, str]]) -> dict | None:
    """Kilka wartości w jednej linii ("A oraz B", "A, B") -> jeden wzorzec z podziałem listy."""
    if len(positions) < 2:
        return None
    positions = sorted(positions)
    line_start = text.rfind("\n", 0, positions[0][0]) + 1
    if any("\n" in text[positions[0][0]:p[1]] for p in positions):
        return None
    words = _label_words(text[line_start:positions[0][0]])
    shapes = {_shape(p[2], "") for p in positions}
    if not words or len(shapes) != 1:
        return None
    seps = []
    for a, b in zip(positions, positions[1:]):
        sep = text[a[1]:b[0]].strip()
        seps.append(re.escape(sep) if sep else "")
    alts = sorted({x for x in seps if x} | {","} , key=len, reverse=True)
    prefix = text[line_start:positions[0][0]]
    label = r"\s+".join(re.escape(w) for w in words)
    colon = r"\s*:?\s*" if prefix.rstrip().endswith(":") else r"\s+"
    return {"regex": rf"{label}{colon}([^\n]+)",
            "split": r"\s*(?:" + "|".join(alts) + r")\s*",
            "item": f"({shapes.pop()})"}


# ---------------------------------------------------------------- weryfikacja kandydata
def _mk_pattern(p, cs: bool) -> PatternSpec:
    if isinstance(p, str):
        return PatternSpec(_compile(p, cs))
    return PatternSpec(_compile(p["regex"], cs), 1, None,
                       _compile(p["split"], cs) if p.get("split") else None,
                       _compile(p["item"], cs) if p.get("item") else None)


def _with_patterns(cls: DocClass, spec: FieldSpec, new_patterns: list, case_sensitive: bool) -> DocClass:
    added = [_mk_pattern(p, case_sensitive) for p in new_patterns]
    new_spec = dataclasses.replace(spec, patterns=added + spec.patterns)
    return dataclasses.replace(cls, fields=[new_spec if f.name == spec.name else f for f in cls.fields])


def _field_values(doc: PdfText, cls: DocClass, name: str) -> list:
    ex = extract(doc, cls)
    res = next((f for f in ex.fields if f.name == name), None)
    return _flat(res.value) if res else []


def _satisfied(values: list, expected: list) -> bool:
    return all(any(_same(v, e) for v in values) for e in expected)


def verify(doc: PdfText, cls: DocClass, spec: FieldSpec, raw_field: dict, patterns: list[str], expected: list):
    """Czy po dopisaniu wzorców na początku listy pole zwraca oczekiwane wartości?"""
    try:
        cls2 = _with_patterns(cls, spec, patterns, bool(raw_field.get("case_sensitive")))
    except re.error as e:
        return False, [], f"niepoprawny regex: {e}"
    values = _field_values(doc, cls2, spec.name)
    return _satisfied(values, expected), values, None


# ---------------------------------------------------------------- regresja na zbiorze treningowym
_TRAIN: dict[str, list] = {}


def _training_docs(class_name: str):
    if class_name not in _TRAIN:
        docs = []
        if DANE.exists():
            for pdf, cls_name, js in collect_cases(DANE):
                if cls_name == class_name and js and Path(js).exists():
                    docs.append((extract_pdf(pdf), json.loads(Path(js).read_text(encoding="utf-8"))))
        _TRAIN[class_name] = docs
    return _TRAIN[class_name]


def _field_score(cls: DocClass, spec: FieldSpec, docs) -> dict:
    g = generic(spec.path)
    tp = fp = fn = 0
    for doc, truth in docs:
        for gp, kind, *_ in compare(truth, extract(doc, cls).json):
            if gp == g:
                tp += kind == "tp"; fp += kind == "fp"; fn += kind == "fn"
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0}


def regression(cls: DocClass, spec: FieldSpec, raw_field: dict, patterns: list[str]) -> dict:
    docs = _training_docs(cls.name)
    if not docs:
        return {"available": False, "note": "brak danych treningowych na tym komputerze - nie sprawdzono regresji"}
    before = _field_score(cls, spec, docs)
    after = _field_score(_with_patterns(cls, spec, patterns, bool(raw_field.get("case_sensitive"))), spec, docs)
    return {"available": True, "docs": len(docs), "before": before, "after": after,
            "ok": after["fp"] <= before["fp"] and after["fn"] <= before["fn"]}


# ---------------------------------------------------------------- diagnoza istniejących wzorców
def diagnose_patterns(cls: DocClass, spec: FieldSpec, raw_field: dict, doc: PdfText, expected: list) -> list[dict]:
    """Dla każdego wzorca: czy pasuje i co wyciąga (testowany osobno, w sekcji pola)."""
    text = doc.text
    scopes = get_blocks(cls, spec.section, text) if spec.section else [text]
    rows = []
    for i, p in enumerate(spec.patterns):
        raw_p = raw_field.get("patterns", [])[i] if i < len(raw_field.get("patterns", [])) else ""
        single = dataclasses.replace(spec, patterns=[p])
        got = []
        for sc in scopes:
            found = engine._matches_multi(single, sc) if spec.multiple else engine._matches(single, sc)
            got += [f[2] for f in found] if spec.multiple else [f[2] for f in found[:1]]   # zwykłe pole bierze pierwszą wartość
        if not got:
            status = "nie pasuje do tekstu"
        elif _satisfied(got, expected):
            status = "pasuje i wyciąga poprawną wartość"
        else:
            status = "pasuje, ale wyciąga co innego"
        rows.append({"index": i + 1, "pattern": _pattern_label(raw_p), "status": status, "values": got})
    return rows


# ---------------------------------------------------------------- LLM
LLM_SYSTEM = ("Jesteś ekspertem od wyrażeń regularnych Pythona (moduł re) i polskich dokumentów zamówień publicznych. "
              "Odpowiadasz WYŁĄCZNIE jednym obiektem JSON: {\"pattern\": \"...\", \"explanation\": \"...\"}, a dla wartości będących listą w jednej linii dodatkowo \"split\" i \"item\".")


def _llm_prompt(cls, spec, raw_field, text, literal_pos, current, expected, reason, patterns, problem=None) -> str:
    exp = expected[0] if len(expected) == 1 else expected
    if literal_pos:
        a, b = literal_pos
        excerpt = text[max(0, a - 500): b + 300]
    else:
        excerpt = text[:3500]
    cur_pat = "\n".join(f"  {i + 1}. {_pattern_label(p)}" for i, p in enumerate(raw_field.get("patterns", []))) or "  (brak)"
    ci = "" if raw_field.get("case_sensitive") else " + IGNORECASE"
    msg = (f"Dokument typu „{cls.title}”. Pole „{spec.name}” ({spec.description}).\n"
           f"Obecne wzorce pola (pierwszy, który pasuje, wygrywa):\n{cur_pat}\n"
           f"Obecna (zła) wartość: {current!r}\nPoprawna wartość: {expected!r}\n"
           f"INSTRUKCJA UŻYTKOWNIKA (nadrzędna - wykonaj ją dosłownie, to jest czego aplikacja ma się nauczyć): {reason or '(brak)'}\n\n"
           f"Fragment dokumentu:\n\"\"\"\n{excerpt}\n\"\"\"\n\n"
           "Napisz JEDEN nowy wzorzec regex (Python, flagi MULTILINE" + ci + "), który wyciągnie poprawną wartość "
           "w PIERWSZEJ grupie przechwytującej (dokładnie jedna taka grupa; pozostałe grupy nieprzechwytujące (?:...)). "
           "Wzorzec musi być ogólny: oprzyj go na etykiecie/otoczeniu, NIE wpisuj na sztywno samej wartości. "
           "Możesz użyć makr: <<DATE>>, <<AMOUNT>>, <<ACCOUNT>>, <<EMAIL>>, <<PERSON>>, <<NAME>>, <<TITLE>>. "
           "Jeśli instrukcja mówi, że wartości stoją po przecinkach / w liście (np. \"szukaj dalej po przecinku\"), "
           "zwróć dodatkowo \"split\" (regex separatora, np. \"\\s*(?:,|;|\\s+oraz\\s+)\\s*\") i \"item\" "
           "(regex jednego elementu listy, grupa 1 = wartość); wtedy \"pattern\" ma przechwycić CAŁĄ listę w jednej grupie.")
    if problem:
        msg += f"\n\nPoprzednia propozycja nie zadziałała: {problem}. Popraw ją."
    return msg


def llm_proposal(cls, spec, raw_field, doc, text, literal_pos, current, expected, reason, client):
    """Do 2 prób: pierwsza propozycja, a po błędzie jedna poprawka z opisem problemu."""
    from . import llm
    problem, last = None, None
    for _ in range(2):
        prompt = _llm_prompt(cls, spec, raw_field, text, literal_pos, current, expected, reason,
                             raw_field.get("patterns", []), problem)
        try:
            data = llm.parse_response(llm.chat_json(client, [{"role": "system", "content": LLM_SYSTEM},
                                                            {"role": "user", "content": prompt}]))
            pat = str(data.get("pattern", "")).strip()
            if pat and str(data.get("split", "")).strip():          # wzorzec listy: przechwyt + podział + element
                pat = {"regex": pat, "split": str(data["split"]).strip(), "item": str(data.get("item") or "(.+)").strip()}
        except Exception as e:                       # brak połączenia itp. - zostaje wersja bez LLM
            return {"source": "llm", "verified": False, "pattern": None, "error": llm.explain_error(e)}
        if not pat:
            problem = "pusty wzorzec"
            continue
        ok, values, err = verify(doc, cls, spec, raw_field, [pat], expected)
        last = {"source": "llm", "pattern": pat, "explanation": str(data.get("explanation", "")),
                "verified": ok, "values": values}
        if ok:
            return last
        problem = err or f"wyciągnął {values!r} zamiast {expected!r}"
    return last or {"source": "llm", "verified": False, "pattern": None, "error": problem}


# ---------------------------------------------------------------- główna funkcja
def yaml_quote(p: str) -> str:
    return "'" + p.replace("'", "''") + "'"


def diagnose(class_name: str, field: str, pages: list[dict], expected_text: str, reason: str = "",
             use_llm: bool = False, client=None) -> dict:
    classes = {c.name: c for c in load_classes()}
    if class_name not in classes:
        raise ValueError(f"nieznana klasa: {class_name}")
    cls = classes[class_name]
    spec, raw_field = _spec_and_raw(cls, field)
    if spec is None:
        raise ValueError(f"klasa {class_name} nie ma pola {field}")
    doc = build_doc(pages)
    text = doc.text
    expected = _norm_expected(spec, expected_text)
    if not expected or expected == [""]:
        raise ValueError("wpisz poprawną wartość pola")

    current = _field_values(doc, cls, field)
    out = {"class_name": class_name, "field": field, "path": spec.path, "file": f"config/classes/{cls.file}",
           "expected": expected, "current": current, "reason": reason, "findings": [], "proposals": [],
           "already_ok": _satisfied(current, expected)}
    f = out["findings"]
    if out["already_ok"]:
        f.append("Pole już zwraca tę wartość - sprawdź, czy błąd nie dotyczy innego pola lub innego dokumentu.")

    # 1) gdzie jest wartość w tekście
    positions = {}
    for e in expected:
        pos = find_literal(spec, text, e)
        positions[str(e)] = pos
        if pos is None:
            f.append(f"Wartości „{e}” nie ma w tekście wyciągniętym z PDF. Przyczyny: literówka, inna forma niż w dokumencie "
                     f"(np. dopełniacz), zły odczyt OCR lub wartość stoi w obrazku. Wpisz ją dokładnie tak, jak w dokumencie.")
    # 2) sekcja
    if spec.section:
        blocks = get_blocks(cls, spec.section, text)
        if not blocks:
            f.append(f"Sekcja „{spec.section}” nie została znaleziona w dokumencie, więc wzorce pola nie mają gdzie szukać "
                     f"(popraw `sections.{spec.section}.start` w {cls.file}).")
        else:
            for e, pos in positions.items():
                if pos and not any(_ws(pos[2]) in _ws(b) for b in blocks):
                    f.append(f"Wartość „{e}” leży POZA sekcją „{spec.section}” (sekcja jest ucinana po `max_chars` albo kończy się za wcześnie). "
                             f"Zwiększ `sections.{spec.section}.max_chars` albo usuń `section:` z pola.")
    # 3) istniejące wzorce
    out["patterns"] = diagnose_patterns(cls, spec, raw_field, doc, expected)
    if not out["patterns"]:
        f.append("Pole nie ma żadnych wzorców.")
    elif all(p["status"] == "nie pasuje do tekstu" for p in out["patterns"]):
        f.append("Żaden z istniejących wzorców nie pasuje do tego fragmentu - potrzebny jest nowy wzorzec.")
    elif any(p["status"].startswith("pasuje, ale") for p in out["patterns"]):
        f.append("Któryś wzorzec pasuje, ale wyciąga inną wartość (patrz tabela) - nowy wzorzec musi stać przed nim.")
    if spec.multiple:
        f.append("Pole jest listą (`multiple`): wzorce ze wszystkich pozycji są sumowane, więc kolejność nie ma znaczenia.")

    # 4) propozycje bez LLM (wersja regex)
    rule_patterns, notes = [], []
    if spec.multiple and len(expected) > 1 and all(positions.get(str(e)) for e in expected):
        cand = build_list_candidate(text, [positions[str(e)] for e in expected])
        if cand and verify(doc, cls, spec, raw_field, [cand], expected)[0]:
            rule_patterns.append(cand)
            notes.append("lista wartości w jednej linii, dzielona separatorami")
    for e in expected:
        pos = positions.get(str(e))
        if pos is None:
            continue
        if _satisfied(_field_values(doc, _with_patterns(cls, spec, rule_patterns, bool(raw_field.get("case_sensitive"))), field), [e]):
            continue                                  # ta wartość jest już łapana (np. przez wcześniej dodany wzorzec)
        for pat, why in build_candidates(text, *pos):
            ok, values, err = verify(doc, cls, spec, raw_field, rule_patterns + [pat], [e])
            if ok:
                rule_patterns.append(pat)
                notes.append(why)
                break
    if rule_patterns:
        ok, values, _ = verify(doc, cls, spec, raw_field, rule_patterns, expected)
        out["proposals"].append({"source": "reguły (bez LLM)", "patterns": rule_patterns, "verified": ok,
                                 "values": values, "explanation": "Wzorzec zbudowany z otoczenia wartości: " + "; ".join(notes) + "."})
    elif not out["already_ok"]:
        out["proposals"].append({"source": "reguły (bez LLM)", "patterns": [], "verified": False, "values": current,
                                 "explanation": "Nie udało się automatycznie zbudować wzorca. Zaznacz wartość w tekście lub użyj podpowiedzi LLM."})

    # 5) propozycja LLM
    if use_llm:
        a_pos = next((p for p in positions.values() if p), None)
        lp = llm_proposal(cls, spec, raw_field, doc, text, (a_pos[0], a_pos[1]) if a_pos else None,
                          current, expected, reason, client)
        if lp.get("pattern"):
            lp["patterns"] = [lp["pattern"]]
        lp["source"] = "LLM"
        out["proposals"].append(lp)

    # 6) regresja dla każdej zweryfikowanej propozycji
    for pr in out["proposals"]:
        if pr.get("verified") and pr.get("patterns"):
            pr["regression"] = regression(cls, spec, raw_field, pr["patterns"])
    first = next((p for p in positions.values() if p), None)
    if first:                                   # fragment wokół wartości: baza podobieństwa dla przykładów LLM
        out["context"] = " ".join(text[max(0, first[0] - 160): first[1] + 60].split())
    out["log"] = build_log(out)
    return out


def build_log(d: dict) -> str:
    """Czytelny log: co dopisać w pliku konfiguracyjnym (wersja bez LLM)."""
    L = [f"POPRAWKA KONFIGURACJI - pole „{d['field']}” ({d['path']})",
         f"Plik: {d['file']}",
         f"Obecna wartość: {d['current'] if d['current'] else '(brak)'}",
         f"Oczekiwana wartość: {d['expected']}"]
    if d["reason"]:
        L.append(f"Uwagi: {d['reason']}")
    L.append("")
    L.append("DLACZEGO NIE DZIAŁA:")
    L += [f"  - {x}" for x in d["findings"]] or ["  - (brak dodatkowych ustaleń)"]
    for p in d.get("patterns", []):
        L.append(f"  - wzorzec nr {p['index']}: {p['status']}" + (f" -> {p['values']}" if p["values"] else ""))
    good = [p for p in d["proposals"] if p.get("verified") and p.get("patterns")]
    L.append("")
    if not good:
        L.append("CO ZROBIĆ: brak zweryfikowanej propozycji - popraw wzorce ręcznie (patrz wyżej).")
        return "\n".join(L)
    for p in good:
        L.append(f"CO ZROBIĆ ({p['source']}): w pliku {d['file']} znajdź pole `name: {d['field']}` "
                 f"i wpisz NA POCZĄTKU listy `patterns:` (pierwszy pasujący wzorzec wygrywa):")
        for x in p["patterns"]:
            if isinstance(x, str):
                L.append(f"      - {yaml_quote(x)}")
            else:
                L += [f"      - regex: {yaml_quote(x['regex'])}", f"        split: {yaml_quote(x['split'])}",
                      f"        item: {yaml_quote(x['item'])}"]
        if p.get("explanation"):
            L.append(f"  Uzasadnienie: {p['explanation']}")
        L.append(f"  Test na tym dokumencie: pole zwraca {p['values']}")
        rg = p.get("regression")
        if rg and rg.get("available"):
            L.append(f"  Zbiór treningowy ({rg['docs']} dok.): F1 pola {rg['before']['f1']} -> {rg['after']['f1']}"
                     + ("  OK, bez regresji" if rg["ok"] else "  UWAGA: poprawka pogarsza wyniki na zbiorze treningowym!"))
        elif rg:
            L.append(f"  {rg['note']}")
        L.append("")
    return "\n".join(L).rstrip()


# ---------------------------------------------------------------- baza poprawek (SQLite)
def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS corrections (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, class_name TEXT, field TEXT, doc_name TEXT,
        expected TEXT, got TEXT, reason TEXT, as_example INTEGER, text TEXT, diagnosis TEXT)""")
    have = {r[1] for r in con.execute("PRAGMA table_info(corrections)")}
    for col, ddl in (("context", "TEXT"), ("applied", "INTEGER DEFAULT 0")):     # migracja starszych baz
        if col not in have:
            con.execute(f"ALTER TABLE corrections ADD COLUMN {col} {ddl}")
    return con


def save_correction(d: dict, doc_name: str, text: str, as_example: bool) -> int:
    con = _db()
    with con:
        cur = con.execute(
            "INSERT INTO corrections (ts, class_name, field, doc_name, expected, got, reason, as_example, text, diagnosis, context)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (time.strftime("%Y-%m-%d %H:%M:%S"), d["class_name"], d["field"], doc_name,
             json.dumps(d["expected"], ensure_ascii=False), json.dumps(d["current"], ensure_ascii=False),
             d["reason"], int(as_example), text, json.dumps(d, ensure_ascii=False), d.get("context", "")))
    con.close()
    return cur.lastrowid


def list_corrections() -> list[dict]:
    con = _db()
    rows = con.execute("SELECT id, ts, class_name, field, doc_name, expected, got, reason, as_example, diagnosis, applied "
                       "FROM corrections ORDER BY id DESC").fetchall()
    con.close()
    out = []
    for r in rows:
        diag = json.loads(r[9]) if r[9] else {}
        out.append({"id": r[0], "ts": r[1], "class_name": r[2], "field": r[3], "doc_name": r[4],
                    "expected": json.loads(r[5]), "got": json.loads(r[6]), "reason": r[7],
                    "as_example": bool(r[8]), "applied": bool(r[10]), "log": diag.get("log", ""),
                    "learnable": [i for i, p in enumerate(diag.get("proposals", [])) if _learnable(p)],
                    "forceable": [i for i, p in enumerate(diag.get("proposals", [])) if p.get("verified") and p.get("patterns") and not _learnable(p)]})
    return out


# ---------------------------------------------------------------- uczenie: wdrożenie poprawki do pamięci
def _learnable(p: dict) -> bool:
    """Propozycję wolno wdrożyć, gdy działa na dokumencie i nie pogarsza wyników na zbiorze treningowym."""
    rg = p.get("regression") or {}
    return bool(p.get("verified") and p.get("patterns")) and rg.get("ok", True)


def apply_correction(correction_id: int, proposal_index: int = 0, force: bool = False) -> dict:
    """Wdraża zweryfikowany wzorzec do config/learned/patterns.yaml (działa od następnego przetwarzania)."""
    from . import learned
    con = _db()
    row = con.execute("SELECT diagnosis, text, applied FROM corrections WHERE id=?", (correction_id,)).fetchone()
    con.close()
    if not row:
        raise ValueError("nie ma takiej poprawki")
    diag, text = json.loads(row[0]), row[1]
    props = diag.get("proposals", [])
    if not 0 <= proposal_index < len(props):
        raise ValueError("nie ma takiej propozycji")
    prop = props[proposal_index]
    if not prop.get("verified") or not prop.get("patterns"):
        raise ValueError("ta propozycja nie została zweryfikowana - nie można jej wdrożyć")

    # ponowne sprawdzenie na bieżącej konfiguracji (mogła się zmienić od czasu zgłoszenia)
    cls = {c.name: c for c in load_classes()}[diag["class_name"]]
    spec, raw_field = _spec_and_raw(cls, diag["field"])
    doc = build_doc([{"number": 1, "text": text}])
    expected = diag["expected"]
    if _satisfied(_field_values(doc, cls, diag["field"]), expected):
        return {"applied": False, "message": "Pole już zwraca poprawną wartość - nic do wdrożenia."}
    ok, values, err = verify(doc, cls, spec, raw_field, prop["patterns"], expected)
    if not ok:
        raise ValueError(err or f"wzorzec nie działa na bieżącej konfiguracji (zwraca {values})")
    rg = regression(cls, spec, raw_field, prop["patterns"])
    worse = bool(rg.get("available") and not rg["ok"])
    if worse and not force:
        raise ValueError(f"wzorzec pogarsza wyniki na zbiorze treningowym (F1 pola {rg['before']['f1']} -> {rg['after']['f1']}) - nie wdrożono (możesz wymusić wdrożenie mimo pogorszenia)")

    n = learned.add(diag["class_name"], diag["field"], prop["patterns"], f"poprawka #{correction_id}")
    con = _db()
    with con:
        con.execute("UPDATE corrections SET applied=1 WHERE id=?", (correction_id,))
    con.close()
    _TRAIN.clear()                                   # zbiór treningowy bez zmian, ale wyniki bazowe się zmieniły
    msg = f"Wdrożono {n} wzorzec(-ów) do config/learned/patterns.yaml."
    if worse:
        msg += " UWAGA: wymuszono mimo pogorszenia F1 na zbiorze treningowym - w razie problemów użyj „Cofnij”."
    return {"applied": True, "added": n, "forced": worse, "message": msg}


def delete_correction(correction_id: int) -> None:
    """Usuwa wpis z bazy poprawek (wdrożony wzorzec zostaje - cofa się go osobno w tabeli nauczonych reguł)."""
    con = _db()
    with con:
        n = con.execute("DELETE FROM corrections WHERE id=?", (correction_id,)).rowcount
    con.close()
    if not n:
        raise ValueError("nie ma takiej poprawki")


def revoke(class_name: str, field: str, index: int) -> None:
    from . import learned
    learned.remove(class_name, field, index)


# ---------------------------------------------------------------- uczenie LLM: przykłady z poprawek (few-shot)
def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"\w{3,}", s.casefold())}


def examples_for(class_name: str, fields: list[str], doc_text: str, k: int = 2, min_overlap: float = 0.35) -> list[dict]:
    """Do k poprawek-przykładów na pole, wybranych po podobieństwie fragmentu do dokumentu (bez LLM i bez sieci)."""
    if not DB_PATH.exists() or not fields:
        return []
    con = _db()
    q = ",".join("?" * len(fields))
    rows = con.execute(f"SELECT field, context, expected, reason FROM corrections WHERE as_example=1 AND class_name=? "
                       f"AND field IN ({q}) AND context != '' ORDER BY id DESC", (class_name, *fields)).fetchall()
    con.close()
    doc_t = _tokens(doc_text)
    scored = {}
    for field, ctx, exp, why in rows:
        ct = _tokens(ctx)
        if not ct:
            continue
        score = len(ct & doc_t) / len(ct)
        if score >= min_overlap:
            scored.setdefault(field, []).append((score, ctx, json.loads(exp), why or ""))
    out = []
    for field, items in scored.items():
        for score, ctx, exp, why in sorted(items, key=lambda x: -x[0])[:k]:
            out.append({"field": field, "snippet": ctx, "value": exp[0] if len(exp) == 1 else exp, "reason": why})
    return out


def corrected_fields(class_name: str) -> set[str]:
    """Pola tej klasy, które człowiek już kiedyś poprawiał - tam regex bywa zawodny."""
    if not DB_PATH.exists():
        return set()
    con = _db()
    rows = con.execute("SELECT DISTINCT field FROM corrections WHERE class_name=?", (class_name,)).fetchall()
    con.close()
    return {r[0] for r in rows}


def hints_for(class_name: str, fields: list[str], k: int = 3) -> list[dict]:
    """Wskazówki człowieka (powody poprawek) dla pól, o które pyta LLM - niezależnie od podobieństwa dokumentu.
    Tylko z poprawek, w których poprawna wartość faktycznie stała w tekście (context != '')."""
    if not DB_PATH.exists() or not fields:
        return []
    con = _db()
    q = ",".join("?" * len(fields))
    rows = con.execute(f"SELECT field, reason FROM corrections WHERE as_example=1 AND class_name=? AND field IN ({q}) "
                       f"AND context != '' AND reason != '' ORDER BY id DESC", (class_name, *fields)).fetchall()
    con.close()
    seen, out = {}, []
    for field, why in rows:
        why = " ".join(why.split())
        if why and why not in seen.setdefault(field, set()) and len(seen[field]) < k:
            seen[field].add(why)
            out.append({"field": field, "hint": why})
    return out
