"""Testy modułu LLM z atrapą klienta (bez prawdziwego modelu)."""
import json
from types import SimpleNamespace

from odw.classes import load_classes
from odw.engine import extract
from odw.extract import Page, PdfText
from odw import llm

CLASSES = {c.name: c for c in load_classes()}


class FakeClient:
    """Udaje klienta OpenAI: zawsze zwraca zadaną odpowiedź."""
    def __init__(self, content=None, error=None):
        self.content, self.error, self.calls = content, error, 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls += 1
        if self.error:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


def doc_of(text):
    return PdfText("t.pdf", [Page(1, 595, 842, text, [])])


PRZELEW_BEZ_TYTULU = """Potwierdzenie przelewu
Kwota 17,00 PLN
Data transakcji 2026-08-12
Rachunek zleceniodawcy: PL43 0463 3766 1291 7568 1186 9204
Rachunek beneficjenta: PL91 3637 7860 6394 9342 6454 7691
Numer referencyjny CEN2608246932582
opłata skarbowa od pełnomocnictwa dla Jan Kowalski w przetargu ZP.1.2026
"""


def run_fill(cls_name, text, client):
    doc = doc_of(text)
    cls = CLASSES[cls_name]
    ex = extract(doc, cls)
    filled = llm.fill_missing(doc, cls, ex, client)
    return ex, filled, {f.name: f for f in ex.fields}


def test_llm_fills_only_missing_field_and_marks_method():
    assert extract(doc_of(PRZELEW_BEZ_TYTULU), CLASSES["przelew"]).json["przelew_bankowy"]["tytul"] is None
    fake = FakeClient(json.dumps({"tytul": "opłata skarbowa od pełnomocnictwa dla Jan Kowalski w przetargu ZP.1.2026"}))
    ex, filled, by = run_fill("przelew", PRZELEW_BEZ_TYTULU, fake)
    assert filled == ["tytul"]
    assert ex.json["przelew_bankowy"]["tytul"].startswith("opłata skarbowa")
    assert by["tytul"].method == "llm" and by["tytul"].confidence == llm.CONF_LLM_FOUND_IN_TEXT
    assert ex.json["przelew_bankowy"]["kwota"] == 17.0          # pole z regexa nietknięte
    assert by["kwota"].method == "regex"


def test_prompt_contains_only_missing_fields():
    cls = CLASSES["przelew"]
    doc = doc_of(PRZELEW_BEZ_TYTULU)
    ex = extract(doc, cls)
    by = {f.name: f for f in ex.fields}
    specs = [s for s in cls.fields if llm.needs_llm(s, by[s.name])]
    assert [s.name for s in specs] == ["tytul"]
    prompt = llm.build_prompt(cls, specs, doc)
    assert '"tytul"' in prompt and '"kwota"' not in prompt


def test_hallucinated_value_gets_low_confidence_and_warning():
    ex, filled, by = run_fill("przelew", PRZELEW_BEZ_TYTULU, FakeClient(json.dumps({"tytul": "coś zmyślonego"})))
    assert filled == ["tytul"]
    assert by["tytul"].confidence == llm.CONF_LLM_NOT_IN_TEXT < llm.LOW_CONFIDENCE
    assert by["tytul"].warnings


def test_json_wrapped_in_markdown_is_parsed():
    content = '```json\n{"tytul": "opłata skarbowa od pełnomocnictwa dla Jan Kowalski w przetargu ZP.1.2026"}\n```'
    _, filled, _ = run_fill("przelew", PRZELEW_BEZ_TYTULU, FakeClient(content))
    assert filled == ["tytul"]


def test_broken_json_or_server_error_keeps_regex_result():
    for fake in (FakeClient("to nie jest json"), FakeClient(error=ConnectionError("brak serwera"))):
        ex, filled, by = run_fill("przelew", PRZELEW_BEZ_TYTULU, fake)
        assert filled == []
        assert ex.json["przelew_bankowy"]["kwota"] == 17.0
        assert by["tytul"].warnings and by["tytul"].value is None


def test_wrong_type_is_rejected_per_field():
    text = PRZELEW_BEZ_TYTULU.replace("Kwota 17,00 PLN", "")      # brakuje też kwoty
    fake = FakeClient(json.dumps({"kwota": "nie liczba", "tytul": "opłata skarbowa od pełnomocnictwa dla Jan Kowalski w przetargu ZP.1.2026"}))
    ex, filled, by = run_fill("przelew", text, fake)
    assert filled == ["tytul"]                   # zły typ odrzucony, dobre pole zostaje
    assert ex.json["przelew_bankowy"]["kwota"] is None


def test_llm_not_called_when_nothing_missing():
    full = PRZELEW_BEZ_TYTULU.replace("opłata skarbowa", "Tytuł przelewu: opłata skarbowa")
    fake = FakeClient(error=AssertionError("LLM nie powinien być wołany"))
    _, filled, _ = run_fill("przelew", full, fake)
    assert filled == [] and fake.calls == 0


ODWOLANIE_NIEJASNE = """Odwołujący:
Alfa sp. z o.o.
KRS: 9992397370, NIP: 9995126748
ODWOŁANIE
Katowice, dnia 24 sierpnia 2026 roku
Działając w imieniu wykonawcy (dalej: „Odwołujący”), wnoszę o:
Zamawiający w dniu 11 sierpnia 2026 roku podjął decyzję dotyczącą dalszych losów postępowania.
"""


def test_llm_fills_reason_and_lookup_description_stays_consistent():
    ex0 = extract(doc_of(ODWOLANIE_NIEJASNE), CLASSES["odwolanie"])
    assert ex0.json["OdwolanieKIO"]["czynnosc_powod_id"] is None          # reguły nie rozpoznały
    ex, filled, by = run_fill("odwolanie", ODWOLANIE_NIEJASNE, FakeClient(json.dumps({"czynnosc_powod_id": 9})))
    assert "czynnosc_powod_id" in filled
    assert ex.json["OdwolanieKIO"]["czynnosc_powod_id"] == 9
    assert ex.json["OdwolanieKIO"]["czynnosc_opis"] == "Decyzja o unieważnieniu postępowania"


def test_reason_outside_1_12_is_rejected():
    ex, filled, _ = run_fill("odwolanie", ODWOLANIE_NIEJASNE, FakeClient(json.dumps({"czynnosc_powod_id": 99})))
    assert "czynnosc_powod_id" not in filled
    assert ex.json["OdwolanieKIO"]["czynnosc_powod_id"] is None


def test_classify_with_llm_accepts_only_known_class():
    classes = list(CLASSES.values())
    d = doc_of("cokolwiek")
    assert llm.classify_with_llm(d, classes, FakeClient('{"class": "przelew"}')) == "przelew"
    assert llm.classify_with_llm(d, classes, FakeClient('{"class": "none"}')) is None
    assert llm.classify_with_llm(d, classes, FakeClient('{"class": "wymyslona"}')) is None
