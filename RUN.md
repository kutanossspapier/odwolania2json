# Uruchomienie (KIOlens, Ubuntu)

Katalog projektu: `/opt/solution`. Wejście: `input/` (PDF-y, także w podkatalogach `case1/…`),
wyjście: `output/` (ta sama struktura, każdy plik JSON nazwany jak PDF).

Jednorazowo (instalacja Pythona, Tesseracta i zależności):

    cd /opt/solution && bash setup_ubuntu.sh

Komenda przetwarzania (zalecana przez jury):

    cd /opt/solution && bash run.sh

Tylko regexy, bez żadnych wywołań API:

    cd /opt/solution && bash run.sh --no-llm

LLM (opcjonalnie, uzupełnia tylko luki): plik `.env` z `LLM_ENABLED=1`, `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`
(patrz `.env.example`). Bez `.env` aplikacja działa na samych regexach i OCR-ze.
