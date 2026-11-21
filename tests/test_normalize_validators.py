"""Testy normalizatorów i walidatorów (NIP, REGON, PESEL, IBAN, daty, kwoty)."""
import pytest

from odw import normalize as n
from odw import validators as v


# ------------------------------------------------------------- normalizatory
@pytest.mark.parametrize("raw,expected", [
    ("24 sierpnia 2026", "2026-08-24"),
    ("1 stycznia 2025", "2025-01-01"),
    ("24.08.2026", "2026-08-24"),
    ("09-07-2026", "2026-07-09"),
    ("2026-03-21", "2026-03-21"),
    ("24 sierpnia 2026 roku", None),      # tekst po dacie nie jest częścią daty
    ("31.02.2026", None),                 # nieistniejąca data
    ("5 smoka 2026", None),
])
def test_date_pl(raw, expected):
    assert n.date_pl(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("200,00", 200.0), ("50.00", 50.0), ("1 234,50", 1234.5), ("10 000,00", 10000.0),
])
def test_amount(raw, expected):
    assert n.amount(raw) == expected


def test_account_removes_pl_and_spaces():
    assert n.account("PL43 0463 3766 1291 7568 1186 9204") == "43046337661291756811869204"
    assert n.account("43046337661291756811869204") == "43046337661291756811869204"


def test_ted_formats():
    assert n.ted("2026/S 013-999000") == "999000-2026"
    assert n.ted("2026/S\n142-999327") == "999327-2026"
    assert n.ted("999512-2026") == "999512-2026"
    assert n.ted("brak") is None


def test_bzp_and_text_helpers():
    assert n.bzp("2026/BZP 00999263") == "2026/BZP00999263"
    assert n.ws("a \n  b\tc") == "a b c"
    assert n.strip_quotes("„opłata skarbowa”.") == "opłata skarbowa"
    assert n.rstrip_punct("ZP.447.2026.") == "ZP.447.2026"


# ------------------------------------------------------------- walidatory
def test_nip():
    assert v.nip("5540240549")[0]            # NIP z przykładu UZP
    assert not v.nip("5540240548")[0]        # zła suma kontrolna
    assert not v.nip("123")[0]


def test_regon():
    assert v.regon("000279077")[0]
    assert not v.regon("000279078")[0]
    assert not v.regon("12345")[0]


def test_pesel():
    assert v.pesel("44051401359")[0]         # przykładowy poprawny PESEL
    assert not v.pesel("44051401358")[0]


def test_iban_pl():
    assert v.iban_pl("60101010100081362231000000")[0]
    assert v.iban_pl("PL60 1010 1010 0081 3622 3100 0000")[0]
    assert not v.iban_pl("60101010100081362231000001")[0]


def test_krs_date_amount():
    assert v.krs("0000054588")[0] and not v.krs("54588")[0]
    assert v.date_iso("2026-08-24")[0] and not v.date_iso("2026-13-01")[0]
    assert v.amount_positive(10.0)[0] and not v.amount_positive(0)[0]
