"""Walidatory: sprawdzają np. sumy kontrolne.

UWAGA: dane w zbiorze UZP są syntetyczne - część NIP-ów, PESEL-i i rachunków ma
błędną sumę kontrolną. Dlatego walidator NIGDY nie odrzuca wartości; zwraca
(ok, komunikat), a silnik tylko obniża pewność pola i pokazuje ostrzeżenie.
"""
from __future__ import annotations

import re
from datetime import date


def nip(v) -> tuple[bool, str]:
    s = re.sub(r"\D", "", str(v))
    if len(s) != 10:
        return False, "NIP musi mieć 10 cyfr"
    w = [6, 5, 7, 2, 3, 4, 5, 6, 7]
    ok = sum(int(a) * b for a, b in zip(s, w)) % 11 == int(s[9])
    return ok, "" if ok else "suma kontrolna NIP się nie zgadza"


def regon(v) -> tuple[bool, str]:
    s = re.sub(r"\D", "", str(v))
    if len(s) == 9:
        w = [8, 9, 2, 3, 4, 5, 6, 7]
        ok = sum(int(a) * b for a, b in zip(s, w)) % 11 % 10 == int(s[8])
    elif len(s) == 14:
        w = [2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8]
        ok = sum(int(a) * b for a, b in zip(s, w)) % 11 % 10 == int(s[13])
    else:
        return False, "REGON musi mieć 9 lub 14 cyfr"
    return ok, "" if ok else "suma kontrolna REGON się nie zgadza"


def krs(v) -> tuple[bool, str]:
    ok = bool(re.fullmatch(r"\d{10}", str(v)))
    return ok, "" if ok else "KRS musi mieć 10 cyfr"


def pesel(v) -> tuple[bool, str]:
    s = str(v).zfill(11) if str(v).isdigit() else str(v)
    if not re.fullmatch(r"\d{11}", s):
        return False, "PESEL musi mieć 11 cyfr"
    w = [1, 3, 7, 9, 1, 3, 7, 9, 1, 3]
    ok = (10 - sum(int(a) * b for a, b in zip(s, w)) % 10) % 10 == int(s[10])
    return ok, "" if ok else "suma kontrolna PESEL się nie zgadza"


def iban_pl(v) -> tuple[bool, str]:
    s = re.sub(r"\s", "", str(v)).upper()
    if not s.startswith("PL"):
        s = "PL" + s
    if not re.fullmatch(r"PL\d{26}", s):
        return False, "rachunek PL musi mieć 26 cyfr"
    r = "".join(str(int(ch, 36)) for ch in s[4:] + s[:4])
    ok = int(r) % 97 == 1
    return ok, "" if ok else "suma kontrolna IBAN się nie zgadza"


def date_iso(v) -> tuple[bool, str]:
    try:
        date.fromisoformat(str(v))
        return True, ""
    except ValueError:
        return False, "niepoprawna data"


def amount_positive(v) -> tuple[bool, str]:
    ok = isinstance(v, (int, float)) and v > 0
    return ok, "" if ok else "kwota musi być dodatnia"


REGISTRY = {"nip": nip, "regon": regon, "krs": krs, "pesel": pesel, "iban_pl": iban_pl,
            "date_iso": date_iso, "amount_positive": amount_positive}
