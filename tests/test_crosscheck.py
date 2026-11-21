from odw.crosscheck import get_values, run_checks

ODW = {"class_name": "odwolanie", "name": "odwolanie.pdf", "json": {
    "Odwolujacy": [{"krs": "9992397370", "nip": "9995126748", "nazwa": "Gildentia sp. z o.o.",
                    "pelnomocnik": {"imie_nazwisko": "Patrycja Furman"}}],
    "ZamowieniePubliczne": {"numer_postepowania": "ZP.447.2026"}}}
PEL = {"class_name": "pelnomocnictwo", "name": "pel.pdf", "json": {
    "mocodawca": [{"data": {"krs": "9992397370", "nip": "9995126748", "nazwa": "GILDENTIA  sp. z o.o."}}],
    "pelnomocnicy": [{"imie_nazwisko": "Patrycja Furman"}, {"imie_nazwisko": "Inny Prawnik"}]}}
PRZ = {"class_name": "przelew", "name": "prz.pdf", "json": {"przelew_bankowy": {
    "tytul": "opłata skarbowa od pełnomocnictwa dla Patrycja Furman w przetargu ZP.447.2026 CEN1"}}}


def by_name(res):
    return {r["name"]: r["status"] for r in res}


def test_get_values_expands_lists_and_skips_nulls():
    assert get_values(PEL["json"], "pelnomocnicy[].imie_nazwisko") == ["Patrycja Furman", "Inny Prawnik"]
    assert get_values(PEL["json"], "mocodawca[0].data.nazwa") == ["GILDENTIA  sp. z o.o."]
    assert get_values({"a": None}, "a") == []


def test_consistent_case_passes_all_checks():
    res = by_name(run_checks([ODW, PEL, PRZ]))
    assert set(res.values()) == {"ok"}


def test_mismatch_is_reported():
    bad = {**PEL, "json": {**PEL["json"], "pelnomocnicy": [{"imie_nazwisko": "Ktoś Inny"}]}}
    res = by_name(run_checks([ODW, bad, PRZ]))
    assert res["Pełnomocnik z odwołania występuje w pełnomocnictwie"] == "blad"


def test_missing_documents_are_skipped_not_failed():
    res = run_checks([ODW])
    assert all(r["status"] == "pominieto" for r in res)
