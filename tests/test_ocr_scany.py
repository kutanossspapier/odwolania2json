"""Czyszczenie błędów OCR i wzorce pod układ skanów (inny szablon niż pliki tekstowe)."""
from odw.classes import load_classes
from odw.engine import extract
from odw.extract import Page, PdfText, clean_ocr_text

CLASSES = {c.name: c for c in load_classes()}


def doc_of(text):
    return PdfText("t.pdf", [Page(1, 595, 842, text, [], ocr=True)])


def test_ocr_cleanup_repairs_email_dates_nip_and_company_form():
    assert clean_ocr_text("e-mail: a.rybakGrybak-kancelaria.example") == "e-mail: a.rybak@rybak-kancelaria.example"
    assert clean_ocr_text("d.kubiak(kubiak-kancelaria.example") == "d.kubiak@kubiak-kancelaria.example"
    assert clean_ocr_text("zamowienia()brzozowiec.example") == "zamowienia@brzozowiec.example"
    assert clean_ocr_text("m.wierzbickiowierzbicki-kancelaria.example") == "m.wierzbicki@wierzbicki-kancelaria.example"
    assert clean_ocr_text("w dniu 14.03 .2026 r.") == "w dniu 14.03.2026 r."
    assert clean_ocr_text("NIP: 999-509-02-81, REGON") == "NIP: 9995090281, REGON"
    assert clean_ocr_text("Kalinex Sp. Z 0.0. oraz Z postępowania") == "Kalinex Sp. z o.o. oraz z postępowania"


SKAN_ODWOLANIE = """Poznań, dnia 11 września 2026 roku
Prezes Krajowej Izby Odwoławczej
Odwołujący: Asterion Budownictwo sp. k.
ul. Jana Pawła II 11A/15
KRS: 9991022552, NIP: 9992956550, REGON: 999913617
reprezentowany przez pełnomocnika:
radca prawny Aleksandra Rybak
adres do korespondencji:
Kancelaria Radcy Prawnego Aleksandra Rybak
ul. Polna 115, 40-175 Katowice
e-mail: a.rybak@rybak-kancelaria.example
Zamawiający: Powiatowy Zespół Szkół Technicznych w Zielonkowie
ul. Marszałkowska 97B/13
Dotyczy: postępowania pn. „Odbiór odpadów”, nr referencyjny: DA.ZP.242.50.2026
ODWOŁANIE
Działając w imieniu Odwołującego, wnoszę odwołanie wobec:
1. treści Specyfikacji Warunków Zamówienia (SWZ) opublikowanej w dniu 6 września 2026 roku;
2. określenia warunków udziału w postępowaniu;
Informacja o zaskarżonej czynności została opublikowana w dniu 06.09.2026 r. Odwołanie zostało
"""


def test_skan_odwolanie_layout():
    ex = extract(doc_of(SKAN_ODWOLANIE), CLASSES["odwolanie"])
    o = ex.json["Odwolujacy"][0]
    assert o["nazwa"] == "Asterion Budownictwo sp. k."          # nazwa w tej samej linii co etykieta
    assert o["pelnomocnik"]["imie_nazwisko"] == "Aleksandra Rybak"
    assert o["pelnomocnik"]["nazwa"] == "Kancelaria Radcy Prawnego Aleksandra Rybak"   # adres w osobnej linii
    assert ex.json["OdwolanieKIO"]["czynnosc_dzien"] == "2026-09-06"
    assert ex.json["OdwolanieKIO"]["czynnosc_powod_id"] == 4    # pierwszy punkt "wobec:" (SWZ)


SKAN_PELNOMOCNICTWO = """Lublin, dnia 9 września 2026 roku
PEŁNOMOCNICTWO
Asterion Budownictwo sp. k. z siedzibą w Lublin (20-380), ul. Jana Pawła II 11A/15, wpisana
do rejestru przedsiębiorców Krajowego Rejestru Sądowego pod numerem KRS: 9991022552, NIP:
9992956550, REGON: 999913617, reprezentowana przez: Szymon Brzozowski - Wiceprezes
Zarządu (dalej: „Mocodawca”), niniejszym udziela pełnomocnictwa:
Aleksandra Rybak
Halina Jodłowska
do reprezentowania Mocodawcy w sprawie dotyczącej środków ochrony prawnej
"""


def test_skan_pelnomocnictwo_layout():
    ex = extract(doc_of(SKAN_PELNOMOCNICTWO), CLASSES["pelnomocnictwo"])
    by = {f.name: f.value for f in ex.fields}
    assert by["mocodawca_nazwa"] == ["Asterion Budownictwo sp. k."]
    assert by["pelnomocnicy"] == ["Aleksandra Rybak", "Halina Jodłowska"]   # same nazwiska w kolejnych liniach
    assert by["podpisujacy"] == [["Szymon Brzozowski"]]


def test_fallback_pattern_is_used_only_when_nothing_else_matches():
    # lista z tytułami znaleziona wzorcem listy, więc "ostatnia deska ratunku" nie dokłada nagłówka kancelarii
    text = "KANCELARIA RADCY PRAWNEGO ALEKSANDRA RYBAK\nPEŁNOMOCNICTWO\nniniejszym udziela pełnomocnictwa:\nAleksandra Rybak\ndo reprezentowania Mocodawcy"
    ex = extract(doc_of(text), CLASSES["pelnomocnictwo"])
    assert {f.name: f.value for f in ex.fields}["pelnomocnicy"] == ["Aleksandra Rybak"]
