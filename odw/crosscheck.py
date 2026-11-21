"""Walidacja między dokumentami sprawy (reguły w config/case_checks.yaml)."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from . import settings

CHECKS_FILE = settings.ROOT / "config" / "case_checks.yaml"


def load_checks(path: Path = CHECKS_FILE) -> list[dict]:
    if not path.exists():
        return []
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("checks", [])


def get_values(obj, path: str) -> list:
    """Wartości pod ścieżką; '[]' rozwija listę, '[0]' wybiera element. Pomija null-e."""
    cur = [obj]
    for key, idx in re.findall(r"([^.\[\]]+)|\[(\d*)\]", path):
        nxt = []
        for c in cur:
            if key:
                if isinstance(c, dict) and key in c:
                    nxt.append(c[key])
            elif isinstance(c, list):
                if idx == "":
                    nxt.extend(c)
                elif int(idx) < len(c):
                    nxt.append(c[int(idx)])
        cur = nxt
    return [v for v in cur if v not in (None, "")]


def _norm(v) -> str:
    return " ".join(str(v).split()).casefold()


def _compare(op: str, left: str, rights: list[str]) -> bool:
    if op == "equals":
        return any(left == r for r in rights)
    if op == "in":
        return left in rights
    if op == "contained_in":
        return any(left in r for r in rights)
    raise ValueError(f"nieznany operator: {op}")


def run_checks(docs: list[dict], checks: list[dict] | None = None) -> list[dict]:
    """docs: [{"name": plik, "class_name": typ, "json": wynik}]. Zwraca listę wyników reguł."""
    checks = load_checks() if checks is None else checks
    out = []
    for c in checks:
        lefts = [d for d in docs if d.get("class_name") == c["left"]["doc"]]
        rights = [d for d in docs if d.get("class_name") == c["right"]["doc"]]
        if not lefts or not rights:
            missing = c["left"]["doc"] if not lefts else c["right"]["doc"]
            out.append({"name": c["name"], "status": "pominieto", "detail": f"brak dokumentu: {missing}"})
            continue
        right_vals = [_norm(v) for d in rights for v in get_values(d["json"], c["right"]["path"])]
        for ld in lefts:
            name = c["name"] + (f" ({ld['name']})" if len(lefts) > 1 else "")
            lv = get_values(ld["json"], c["left"]["path"])
            if not lv or not right_vals:
                out.append({"name": name, "status": "pominieto", "detail": "brak wartości do porównania"})
                continue
            left = _norm(lv[0])
            ok = _compare(c["op"], left, right_vals)
            out.append({"name": name, "status": "ok" if ok else "blad",
                        "detail": f"{lv[0]}" if ok else f"„{lv[0]}” nie pasuje do wartości z drugiego dokumentu"})
    return out
