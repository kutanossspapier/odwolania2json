"""Testy ekstrakcji pól z YAML na krótkich fragmentach prawdziwych układów dokumentów."""
import pytest

from odw.classes import load_classes
from odw.engine import extract
from odw.extract import Page, PdfText

CLASSES = {c.name: c for c in load_classes()}


def run(cls_name: str, text: str):
    doc = PdfText("test.pdf", [Page(1, 595, 842, text, [])])
    return extract(doc, CLASSES[cls_name])


PRZELEW_A = """Potwierdzenie transakcji
Nr klienta
716025
Tytuł
opłata skarbowa od pełnomocnictwa dla Patrycja Furman i Sławomir Ostrowczyk w postępowaniu przed KIO w przetargu
ZP.447.2026 CEN2608246932582
Kwota
17,00 PLN
Data transakcji
2026-08-12
Numer referencyjny
CEN2608246932582
Nadawca
Gildentia Wyposażenie sp. z o.o., ul. Nowina 87, 40-960 Katowice
Rachunek zleceniodawcy: PL43 0463 3766 1291 7568 1186 9204
Adresat przelewu
Danevia Serwis sp. k., ul. Akacjowa 83, 60-313 Poznań
Rachunek beneficjenta: PL91 3637 7860 6394 9342 6454 7691
Waluta: PLN
"""

PRZELEW_B = """Potwierdzenie realizacji polecenia przelewu
Nova Bank Polska · wydrukowano 15 czerwca 2026
Nadawca Beneficjent
Kadmos Rozwiązania sp. k. Veltra Infrastruktura sp. z o.o.
ul. Różana 26, 90-548 Łódź ul. Ambrożowa 96, 00-624 Warszawa
40826585774033060612824095 19546370601574577093699823
Kwota przelewu 34.00 PLN
Data transakcji 14 czerwca 2026
Numer transakcji CEN2606277078899
Tytułem opłata skarbowa od pełnomocnictwa dla Grzegorz Rutkiewicz w postępowaniu przed KIO
w przetargu DI.123.2026 CEN2606277078899
Status Przelew zrealizowany
"""


def test_przelew_layout_a():
    j = run("przelew", PRZELEW_A).json["przelew_bankowy"]
    assert j["kwota"] == 17.0
    assert j["data_operacji"] == "2026-08-12"
    assert j["z_rachunku"] == "43046337661291756811869204"
    assert j["na_rachunek"] == "91363778606394934264547691"
    assert j["numer_transakcji"] == "CEN2608246932582"
    assert j["tytul"].startswith("opłata skarbowa od pełnomocnictwa dla Patrycja Furman")
    assert j["tytul"].endswith("ZP.447.2026 CEN2608246932582")


def test_przelew_layout_b_bare_accounts_and_polish_date():
    j = run("przelew", PRZELEW_B).json["przelew_bankowy"]
    assert j["kwota"] == 34.0
    assert j["data_operacji"] == "2026-06-14"
    assert j["z_rachunku"] == "40826585774033060612824095"
    assert j["na_rachunek"] == "19546370601574577093699823"
    assert j["numer_transakcji"] == "CEN2606277078899"
    assert j["tytul"].endswith("DI.123.2026 CEN2606277078899")


ODWOLANIE = """Zamawiający:
Danevia Serwis sp. k.
ul. Akacjowa 83, 60-313 Poznań
KRS: 9997812714, NIP: 9990342967, REGON: 999118676
kontakt@sekretariat.example
Odwołujący:
Gildentia Wyposażenie sp. z o.o.
ul. Nowina 87, 40-960 Katowice
KRS: 9992397370, NIP: 9995126748, REGON: 999165641
reprezentowana przez: adw. Patrycja Furman, r. pr. Sławomir Ostrowczyk
adres do korespondencji: Kancelaria Radców Prawnych i Partnerzy, ul. Rybacka 54, 20-691 Lublin
tel. 000 000 000, biuro@kontakt.example
ODWOŁANIE
dotyczy:
postępowania o udzielenie zamówienia w trybie negocjacji z ogłoszeniem pn. „Dostawa licencji
oprogramowania do monitorowania infrastruktury i alarmowej” nr referencyjny sprawy: ZP.447.2026,
numer ogłoszenia w Dzienniku Urzędowym UE: 999512-2026
Katowice, dnia 24 sierpnia 2026 roku
Prezes Krajowej Izby Odwoławczej
Działając w imieniu wykonawcy Gildentia Wyposażenie sp. z o.o. (dalej: „Odwołujący”), wnoszę o:
Zamawiający w dniu 11 sierpnia 2026 roku unieważnił postępowanie.
Zarzut nr 1 nieopisania parametrów technicznych przedmiotu dostawy:
"""


def test_odwolanie_header_parties_and_order_data():
    j = run("odwolanie", ODWOLANIE).json
    o = j["OdwolanieKIO"]
    assert (o["data"], o["miejscowosc"]) == ("2026-08-24", "Katowice")
    assert (o["czynnosc_dzien"], o["czynnosc_powod_id"]) == ("2026-08-11", 9)
    assert o["czynnosc_opis"] == "Decyzja o unieważnieniu postępowania"
    odw = j["Odwolujacy"][0]
    assert odw["nazwa"] == "Gildentia Wyposażenie sp. z o.o."
    assert (odw["krs"], odw["nip"], odw["regon"]) == ("9992397370", "9995126748", "999165641")
    assert odw["przez-pelnomocnika"] == 1
    assert odw["pelnomocnik"] == {"imie_nazwisko": "Patrycja Furman",
                                  "nazwa": "Kancelaria Radców Prawnych i Partnerzy",
                                  "mail": "biuro@kontakt.example"}
    zam = j["Zamawiajacy"][0]
    assert zam["nazwa"] == "Danevia Serwis sp. k."
    assert (zam["krs"], zam["nip"], zam["regon"]) == ("9997812714", "9990342967", "999118676")
    assert zam["prowadzacy_postepowanie"] is None      # jeden zamawiający
    zp = j["ZamowieniePubliczne"]
    assert zp["numer_postepowania"] == "ZP.447.2026"
    assert zp["numer_ted"] == "999512-2026"
    assert zp["numer_bzp"] is None
    assert zp["tytul"] == "Dostawa licencji oprogramowania do monitorowania infrastruktury i alarmowej"


def test_odwolanie_two_contracting_authorities():
    text = ODWOLANIE.replace(
        "Zamawiający:\nDanevia Serwis sp. k.\nul. Akacjowa 83, 60-313 Poznań\nKRS: 9997812714, NIP: 9990342967, REGON: 999118676\nkontakt@sekretariat.example",
        "Zamawiający (zamawiający wspólnie przeprowadzający postępowanie):\n"
        "1) Zamawiający – Lider, prowadzący postępowanie:\nAlfa sp. z o.o.\nKRS: 9991111111, NIP: 9990000001\n"
        "2) Zamawiający – Partner:\nBeta S.A.\nKRS: 9992222222, NIP: 9990000002")
    j = run("odwolanie", text).json
    assert [z["nazwa"] for z in j["Zamawiajacy"]] == ["Alfa sp. z o.o.", "Beta S.A."]
    assert [z["prowadzacy_postepowanie"] for z in j["Zamawiajacy"]] == [1, 0]
    assert j["Odwolujacy"][0]["nip"] == "9995126748"      # sekcje się nie mieszają


PELNOMOCNICTWO = """PEŁNOMOCNICTWO
Warszawa, 01-01-2026
§ 1. Mocodawca
Mocodawcą jest Deltawing Usługi sp. j., ul. Miodowa 63, 00-427 Warszawa, KRS 9990929624, NIP 9994888947,
REGON 999788508.
§ 2. Pełnomocnik
Mocodawca udzielam pełnomocnictwa adw. Ignacy Rutkiewicz (nr wpisu na listę: WA 87629), r. pr. Weronika
Furman (nr wpisu na listę: WA 48119).
§ 4. Zakres umocowania
1) zasięgania informacji o dokumentacji postępowania i jej udostępniania Wykonawcy.
2) udzielania dalszych pełnomocnictw radcom prawnym i adwokatom,
Mocodawca: Pełnomocnictwo przyjmuję:
............................. .............................
Oliwia Górnicki adw. Ignacy Rutkiewicz
Prokurent
.............................
Jolanta Kaczmarek
Prokurent
"""


def test_pelnomocnictwo_basic():
    j = run("pelnomocnictwo", PELNOMOCNICTWO).json
    m = j["mocodawca"]
    assert len(m) == 1                       # lista "1) zasięgania…" to nie konsorcjum
    assert m[0]["data"]["nazwa"] == "Deltawing Usługi sp. j."
    assert (m[0]["data"]["krs"], m[0]["data"]["nip"]) == ("9990929624", "9994888947")
    assert m[0]["data"]["typ"] == "osoba_prawna"
    assert [p["imie_nazwisko"] for p in m[0]["podpisujacy"]] == ["Oliwia Górnicki", "Jolanta Kaczmarek"]
    assert [p["imie_nazwisko"] for p in j["pelnomocnicy"]] == ["Ignacy Rutkiewicz", "Weronika Furman"]


def test_pelnomocnictwo_consortium_two_principals():
    text = ("Toruń, 6 sierpnia 2026 r.\nPEŁNOMOCNICTWO\n"
            "Wykonawcy wspólnie ubiegający się o udzielenie zamówienia (Konsorcjum): 1) Torvex\n"
            "Budownictwo sp. z o.o., ul. Przemysłowa 8, 87-100 Toruń, KRS 9995540127, NIP 9992284611,\n"
            "REGON 999473016; 2) Hydromel Instalacje sp.j., ul. Lipowa 15, 85-016 Bydgoszcz, KRS\n"
            "9997108834, NIP 9994417025 (dalej łącznie: „Mocodawca”), udzielają pełnomocnictwa: "
            "adw. Piotr Zalewski (nr wpisu na listę: TO/Adw/412).\n")
    m = run("pelnomocnictwo", text).json["mocodawca"]
    assert [x["data"]["krs"] for x in m] == ["9995540127", "9997108834"]
    assert [x["data"]["nip"] for x in m] == ["9992284611", "9994417025"]
    assert m[1]["data"]["nazwa"] == "Hydromel Instalacje sp.j."


def test_missing_values_stay_null_not_guessed():
    j = run("przelew", "Potwierdzenie przelewu\nBrak danych").json["przelew_bankowy"]
    assert all(v is None for v in j.values())


def test_validators_lower_confidence_but_keep_value():
    ex = run("przelew", PRZELEW_B)
    acc = next(f for f in ex.fields if f.name == "z_rachunku")
    assert acc.value == "40826585774033060612824095"      # błędna suma IBAN, ale wartość zostaje
    assert acc.confidence < 0.9 and acc.warnings


def test_every_class_has_fields_and_valid_methods():
    for c in CLASSES.values():
        assert c.fields and c.output
        for f in c.fields:
            assert f.method in {"regex", "rules", "lookup", "llm"}
