"""Normalizatory: zamieniają surowy fragment tekstu na wartość w formacie UZP.

Każdy normalizator to funkcja str -> wartość (albo None, gdy się nie da).
W YAML wpisujemy ich nazwy, np.  normalize: [ws, date_pl].
"""
from __future__ import annotations

import re
from datetime import date

MONTHS = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6,
    "lipca": 7, "sierpnia": 8, "września": 9, "października": 10, "listopada": 11,
    "grudnia": 12,
}


def ws(s: str) -> str:
    """Zwija białe znaki (też złamania linii) do pojedynczych spacji."""
    return " ".join(str(s).split())


_QUOTED = re.compile(r"^[„«\"“]\s*(.*?)\s*[”»\"“]\.?$", re.S)


def strip_quotes(s: str) -> str:
    s = ws(s)
    m = _QUOTED.match(s)
    return m.group(1) if m else s


def rstrip_punct(s: str) -> str:
    return ws(s).rstrip(" .,;:”\"")


def _valid(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def date_pl(s: str) -> str | None:
    """'24 sierpnia 2026', '24.08.2026', '24-08-2026', '2026-08-24' -> '2026-08-24'."""
    s = ws(s).rstrip(".")
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return _valid(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", s)
    if m:
        return _valid(int(m[3]), int(m[2]), int(m[1]))
    m = re.fullmatch(r"(\d{1,2})\s+([a-ząćęłńóśźż]+)\s+(\d{4})", s, re.I)
    if m and m[2].lower() in MONTHS:
        return _valid(int(m[3]), MONTHS[m[2].lower()], int(m[1]))
    return None


def amount(s: str) -> float | None:
    """'1 234,50' / '1234.50' -> 1234.5"""
    s = re.sub(r"[\s ]", "", str(s)).replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def account(s: str) -> str | None:
    """'PL43 0463 ...' -> 26 cyfr bez prefiksu PL."""
    d = re.sub(r"\s", "", str(s)).upper()
    if d.startswith("PL"):
        d = d[2:]
    return d if d.isdigit() else None


def digits(s: str) -> str:
    return re.sub(r"\D", "", str(s))


def to_int(s) -> int | None:
    d = digits(s)
    return int(d) if d else None


def ted(s: str) -> str | None:
    """'2026/S 013-999000' lub '999000-2026' -> '999000-2026'."""
    s = ws(s)
    m = re.search(r"(\d{4})/S\s*\d{3}-(\d{6})", s)
    if m:
        return f"{m[2]}-{m[1]}"
    m = re.search(r"(\d{6})-(\d{4})", s)
    return f"{m[1]}-{m[2]}" if m else None


def bzp(s: str) -> str:
    """'2026/BZP 00999484' -> '2026/BZP00999484' (bez spacji)."""
    return re.sub(r"\s", "", str(s))


REGISTRY = {
    "ws": ws, "strip_quotes": strip_quotes, "rstrip_punct": rstrip_punct,
    "date_pl": date_pl, "amount": amount, "account": account, "digits": digits,
    "int": to_int, "ted": ted, "bzp": bzp,
}


def apply(names: list[str], value):
    for n in names:
        if value is None:
            return None
        value = REGISTRY[n](value)
    return value
