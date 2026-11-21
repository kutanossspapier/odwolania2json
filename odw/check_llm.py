"""Diagnostyka połączenia z LLM.

Użycie:
  python -m odw.check_llm            # sprawdza konfigurację z .env i robi próbne zapytanie
  python -m odw.check_llm --list     # dodatkowo wypisuje modele dostępne u dostawcy

Klucz API nigdy nie jest wypisywany.
"""
from __future__ import annotations

import sys
import time

from . import llm, settings


def run(list_models: bool = False) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"Dostawca : {settings.LLM_PROVIDER_LABEL} [{settings.LLM_PROVIDER}] - {settings.LLM_REGION}")
    print(f"Adres    : {settings.LLM_BASE_URL}")
    print(f"Model    : {settings.LLM_MODEL}")
    print(f"Klucz API: {'ustawiony' if settings.LLM_API_KEY not in ('', 'none') else 'brak (OK tylko dla Ollamy / trybu darmowego)'}")
    print(f"LLM_ENABLED={int(settings.LLM_ENABLED)} (1 = aplikacja używa LLM do uzupełniania luk)\n")

    client = llm.make_client()

    # 1) lista modeli (nie każdy dostawca ją udostępnia)
    ids: list[str] = []
    try:
        ids = sorted(m.id for m in client.models.list().data)
        print(f"[1/2] Lista modeli: OK ({len(ids)} dostępnych)")
        if settings.LLM_MODEL not in ids:
            print(f"      UWAGA: modelu „{settings.LLM_MODEL}” nie ma na liście dostawcy. Ustaw LLM_MODEL na jedną z nazw poniżej (--list).")
        if list_models:
            for i in ids:
                print("       -", i)
    except Exception as exc:
        print(f"[1/2] Lista modeli: pominięta ({llm.explain_error(exc)})")

    # 2) próbne zapytanie JSON
    print("[2/2] Próbne zapytanie do modelu… (pierwsze wywołanie lokalnego modelu może trwać ok. minuty)")
    t0 = time.perf_counter()
    try:
        raw = llm.chat_json(client, [
            {"role": "system", "content": llm.SYSTEM_PROMPT},
            {"role": "user", "content": 'Tekst: "Faktura nr FV/12/2026 z dnia 5 marca 2026, kwota 123,45 PLN."\n'
                                        'Zwróć JSON z kluczami "numer" (tekst) i "kwota" (liczba).'}])
        data = llm.parse_response(raw)
    except Exception as exc:
        print(f"      BŁĄD: {llm.explain_error(exc)}")
        print("\nWynik: LLM NIEDOSTĘPNY. Aplikacja nadal działa na samych regexach (F1 bez LLM jest wysokie).")
        return 1
    secs = time.perf_counter() - t0
    try:
        kwota = float(str(data.get("kwota")).replace(",", ".").replace("PLN", "").strip())
    except ValueError:
        kwota = None
    ok = "FV/12/2026" in str(data.get("numer", "")) and kwota is not None and abs(kwota - 123.45) < 0.01
    print(f"      odpowiedź: {data}  ({secs:.1f} s)")
    print("\nWynik:", "LLM DZIAŁA POPRAWNIE." if ok else "LLM odpowiada, ale odpowiedź jest nieoczekiwana - rozważ inny model.")
    if not settings.LLM_ENABLED:
        print("Podpowiedź: ustaw LLM_ENABLED=1 w .env, żeby aplikacja używała LLM.")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(run("--list" in sys.argv))
