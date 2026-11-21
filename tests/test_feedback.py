"""Testy ręcznej korekty: diagnoza błędu i propozycja nowego wzorca (bez LLM i z atrapą LLM)."""
import json
from types import SimpleNamespace

import pytest

from odw import feedback
from odw.classes import load_classes
from odw.engine import extract
from odw.extract import Page, PdfText

CLASSES = {c.name: c for c in load_classes()}


@pytest.fixture(autouse=True)
def no_training_regression(monkeypatch):
    """Testy nie czytają PDF-ów treningowych (byłoby wolno); regresję sprawdza osobny test."""
    monkeypatch.setattr(feedback, "_TRAIN", {c: [] for c in CLASSES})

ODW = """Odwołujący:
Belmoria S.A.
ul. Zaczarowana 78, 90-803 Łódź
KRS: 9999405188, NIP: 9999284065
Pełnomocnik odwołującego: Jan Kowalski (radca prawny)
adres do korespondencji: Kancelaria X, ul. Jasionowa 19, 80-994 Gdańsk
Zamawiający:
Aurelink S.A.
ODWOŁANIE
"""


def pages(t):
    return [{"number": 1, "text": t}]


def test_diagnosis_builds_verified_pattern_without_llm():
    d = feedback.diagnose("odwolanie", "pelnomocnik_imie_nazwisko", pages(ODW), "Jan Kowalski")
    assert d["current"] == [] and not d["already_ok"]
    prop = d["proposals"][0]
    assert prop["verified"] and prop["values"] == ["Jan Kowalski"]
    assert "Pełnomocnik" in prop["patterns"][0]
    assert all(p["status"] == "nie pasuje do tekstu" for p in d["patterns"])
    assert "NA POCZĄTKU listy" in d["log"] and "config/classes/odwolanie.yaml" in d["log"]


def test_new_pattern_generalizes_to_other_documents():
    d = feedback.diagnose("odwolanie", "pelnomocnik_imie_nazwisko", pages(ODW), "Jan Kowalski")
    cls = feedback._with_patterns(CLASSES["odwolanie"], next(f for f in CLASSES["odwolanie"].fields if f.name == "pelnomocnik_imie_nazwisko"),
                                  d["proposals"][0]["patterns"], False)
    other = ODW.replace("Jan Kowalski", "Maria Nowak-Lis")
    ex = extract(PdfText("x.pdf", [Page(1, 595, 842, other, [])]), cls)
    assert ex.json["Odwolujacy"][0]["pelnomocnik"]["imie_nazwisko"] == "Maria Nowak-Lis"


def test_value_missing_from_text_is_reported():
    d = feedback.diagnose("odwolanie", "pelnomocnik_imie_nazwisko", pages(ODW), "Zenon Nieistniejący")
    assert any("nie ma w tekście" in x for x in d["findings"])
    assert not d["proposals"][0]["verified"]


def test_value_outside_section_is_reported():
    long_gap = ODW.replace("Zamawiający:", "x" * 5000 + "\nZamawiający:")
    text = long_gap + "\nreprezentowana przez: Piotr Daleki\n"
    d = feedback.diagnose("odwolanie", "pelnomocnik_imie_nazwisko", pages(text), "Piotr Daleki")
    assert d["findings"]


def test_date_found_through_normalization():
    t = "Potwierdzenie przelewu\nOperację wykonano dnia 12 sierpnia 2026 r.\n"
    spec = next(f for f in CLASSES["przelew"].fields if f.name == "data_operacji")
    pos = feedback.find_literal(spec, t, "2026-08-12")
    assert pos and pos[2] == "12 sierpnia 2026"


def test_shapes_are_generalized():
    assert feedback._shape("123456", "") == r"\d{6}"
    assert feedback._shape("ZP.039.2025", "") == r"[A-ZĄĆĘŁŃÓŚŹŻ]+[.]?".replace("[.]?", r"\.") + r"\d+\." + r"\d+"
    assert feedback._shape("2026-08-12", "") == "<<DATE>>"


class FakeClient:
    def __init__(self, content):
        self.content = content
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


def test_llm_proposal_is_verified_and_bad_pattern_is_rejected():
    good = FakeClient(json.dumps({"pattern": r"Pełnomocnik\s+odwołującego:\s*([^\n(]+?)\s*\(", "explanation": "po etykiecie"}))
    d = feedback.diagnose("odwolanie", "pelnomocnik_imie_nazwisko", pages(ODW), "Jan Kowalski", use_llm=True, client=good)
    llm_prop = next(p for p in d["proposals"] if p["source"] == "LLM")
    assert llm_prop["verified"] and llm_prop["values"] == ["Jan Kowalski"]

    bad = FakeClient(json.dumps({"pattern": r"(nic się nie zgadza)", "explanation": "x"}))
    d = feedback.diagnose("odwolanie", "pelnomocnik_imie_nazwisko", pages(ODW), "Jan Kowalski", use_llm=True, client=bad)
    assert not next(p for p in d["proposals"] if p["source"] == "LLM")["verified"]
    assert any(p["source"].startswith("reguły") and p["verified"] for p in d["proposals"])   # wersja bez LLM nadal działa


def test_invalid_regex_from_llm_does_not_crash():
    d = feedback.diagnose("odwolanie", "pelnomocnik_imie_nazwisko", pages(ODW), "Jan Kowalski", use_llm=True,
                          client=FakeClient(json.dumps({"pattern": "([", "explanation": ""})))
    assert not next(p for p in d["proposals"] if p["source"] == "LLM")["verified"]


def test_regression_check_on_training_data():
    feedback._TRAIN.clear()      # prawdziwe dane (jeśli są na tym komputerze)
    if not feedback.DANE.exists():
        pytest.skip("brak danych treningowych")
    d = feedback.diagnose("odwolanie", "pelnomocnik_imie_nazwisko", pages(ODW), "Jan Kowalski")
    rg = d["proposals"][0]["regression"]
    assert rg["available"] and rg["ok"] and rg["after"]["f1"] >= rg["before"]["f1"]


def test_unknown_field_and_empty_value_raise():
    with pytest.raises(ValueError):
        feedback.diagnose("odwolanie", "nie_ma_takiego", pages(ODW), "x")
    with pytest.raises(ValueError):
        feedback.diagnose("odwolanie", "pelnomocnik_imie_nazwisko", pages(ODW), "  ")


def test_multiple_field_gets_all_expected_values(monkeypatch):
    t = "PEŁNOMOCNICTWO\nMocodawca: Alfa sp. z o.o., ul. Leśna 5, 00-001 Warszawa\nPełnomocnicy do sprawy: Ewa Zielona oraz Jan Biały.\n"
    d = feedback.diagnose("pelnomocnictwo", "pelnomocnicy", pages(t), "Ewa Zielona\nJan Biały")
    assert d["proposals"][0]["verified"]
    assert sorted(d["proposals"][0]["values"]) == ["Ewa Zielona", "Jan Biały"]


@pytest.mark.parametrize("zapis,oczekiwani", [
    ("reprezentowana przez: Anna Maria Kowalska-Nowak, radca prawny Jan Kowalski oraz mec. Ewa Lis",
     ["Anna Maria Kowalska-Nowak", "Jan Kowalski", "Ewa Lis"]),
    ("reprezentowana przez:\nadw. Patrycja Jakaś\nr. pr. Marek Innyśtam\nadres do korespondencji: Kancelaria X",
     ["Patrycja Jakaś", "Marek Innyśtam"]),
    ("reprezentowana przez: r. pr. Aleksandra MARCINIAK (nr wpisu WA 1), adres do korespondencji: Kancelaria X, ul. A 1",
     ["Aleksandra MARCINIAK"]),
])
def test_pelnomocnictwo_lista_pelnomocnikow(zapis, oczekiwani):
    t = "PEŁNOMOCNICTWO\nMocodawca: Alfa sp. z o.o., ul. Leśna 5, 00-001 Warszawa\n" + zapis + "\n"
    ex = extract(PdfText("t.pdf", [Page(1, 595, 842, t, [])]), CLASSES["pelnomocnictwo"])
    assert [p["imie_nazwisko"] for p in ex.json["pelnomocnicy"]] == oczekiwani


# ---------------------------------------------------------------- uczenie (pamięć)
@pytest.fixture
def memory(tmp_path, monkeypatch):
    from odw import learned
    monkeypatch.setattr(learned, "PATH", tmp_path / "learned" / "patterns.yaml")
    monkeypatch.setattr(feedback, "DB_PATH", tmp_path / "c.db")
    return learned


def _save(expected="Jan Kowalski", as_example=True):
    d = feedback.diagnose("odwolanie", "pelnomocnik_imie_nazwisko", pages(ODW), expected)
    return d, feedback.save_correction(d, "t.pdf", ODW, as_example)


def test_applied_correction_is_learned_and_used_for_new_documents(memory):
    d, cid = _save()
    assert feedback.list_corrections()[0]["learnable"] == [0]
    res = feedback.apply_correction(cid, 0)
    assert res["applied"] and memory.PATH.exists()
    # nowa instancja klas (jak po restarcie) ma wzorzec na początku listy
    cls = {c.name: c for c in load_classes()}["odwolanie"]
    spec = next(f for f in cls.fields if f.name == "pelnomocnik_imie_nazwisko")
    assert "Pełnomocnik" in spec.patterns[0].regex.pattern
    other = ODW.replace("Jan Kowalski", "Maria Nowak-Lis")
    ex = extract(PdfText("x.pdf", [Page(1, 595, 842, other, [])]), cls)
    assert ex.json["Odwolujacy"][0]["pelnomocnik"]["imie_nazwisko"] == "Maria Nowak-Lis"
    assert feedback.list_corrections()[0]["applied"]
    # drugie wdrożenie niczego nie dubluje
    assert feedback.apply_correction(cid, 0)["applied"] is False


def test_learned_pattern_can_be_revoked(memory):
    _, cid = _save()
    feedback.apply_correction(cid, 0)
    item = memory.listing()[0]
    feedback.revoke(item["class_name"], item["field"], item["index"])
    assert memory.listing() == []
    cls = {c.name: c for c in load_classes()}["odwolanie"]
    assert extract(PdfText("x.pdf", [Page(1, 595, 842, ODW, [])]), cls).json["Odwolujacy"][0]["pelnomocnik"]["imie_nazwisko"] is None


def test_unverified_or_unknown_correction_is_refused(memory):
    d, cid = _save("Zenon Nieistniejący")
    with pytest.raises(ValueError):
        feedback.apply_correction(cid, 0)
    with pytest.raises(ValueError):
        feedback.apply_correction(9999, 0)
    assert memory.listing() == []


def test_regression_blocks_harmful_pattern(memory, monkeypatch):
    d, cid = _save()
    monkeypatch.setattr(feedback, "regression",
                        lambda *a, **k: {"available": True, "docs": 1, "ok": False, "before": {"f1": 1.0}, "after": {"f1": 0.5}})
    with pytest.raises(ValueError, match="pogarsza"):
        feedback.apply_correction(cid, 0)
    res = feedback.apply_correction(cid, 0, force=True)          # świadome wdrożenie mimo pogorszenia
    assert res["applied"] and res["forced"] and memory.listing()


def test_correction_can_be_deleted(memory):
    _, cid = _save()
    feedback.delete_correction(cid)
    assert feedback.list_corrections() == []
    with pytest.raises(ValueError):
        feedback.delete_correction(cid)


def test_examples_for_llm_come_from_similar_corrections(memory):
    _save()
    ex = feedback.examples_for("odwolanie", ["pelnomocnik_imie_nazwisko"], ODW)
    assert ex and ex[0]["value"] == "Jan Kowalski" and "Pełnomocnik" in ex[0]["snippet"]
    assert feedback.examples_for("odwolanie", ["pelnomocnik_imie_nazwisko"], "zupełnie inny tekst o czymś innym") == []
    assert feedback.examples_for("przelew", ["kwota"], ODW) == []


def test_corrections_not_marked_as_example_are_not_used_by_llm(memory):
    _save(as_example=False)
    assert feedback.examples_for("odwolanie", ["pelnomocnik_imie_nazwisko"], ODW) == []


def test_llm_prompt_contains_examples_and_human_hints(memory):
    from odw import llm
    d = feedback.diagnose("odwolanie", "pelnomocnik_imie_nazwisko", pages(ODW), "Jan Kowalski",
                          "Pełnomocnik stoi po słowach „Pełnomocnik odwołującego”")
    feedback.save_correction(d, "t.pdf", ODW, True)
    cls = CLASSES["odwolanie"]
    spec = next(f for f in cls.fields if f.name == "pelnomocnik_imie_nazwisko")
    doc = PdfText("x.pdf", [Page(1, 595, 842, ODW, [])])
    ex = feedback.examples_for("odwolanie", [spec.name], ODW)
    hints = feedback.hints_for("odwolanie", [spec.name])
    prompt = llm.build_prompt(cls, [spec], doc, ex, hints)
    assert "poprawna wartość: \"Jan Kowalski\"" in prompt
    assert "uwaga człowieka: Pełnomocnik stoi po słowach" in prompt
    assert "Wskazówki od człowieka" in prompt


def test_hints_ignore_corrections_whose_value_was_not_in_the_text(memory):
    d, _ = _save("Zenon Nieistniejący")       # wartości nie ma w tekście -> brak kontekstu
    assert feedback.hints_for("odwolanie", ["pelnomocnik_imie_nazwisko"]) == []
