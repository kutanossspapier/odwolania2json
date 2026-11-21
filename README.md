# Odwołania2json

Aplikacja zamienia PDF-y sprawy odwoławczej KIO (odwołanie, pełnomocnictwo, potwierdzenie przelewu)
na JSON w formacie UZP. Rozpoznaje typ dokumentu, wyciąga pola regexami (konfiguracja w YAML),
a **LLM tylko uzupełnia pola, których regex nie znalazł**. Bez LLM aplikacja działa w całości na regexach.

> Dokumentacja jest w trakcie tworzenia - poniżej instalacja i konfiguracja LLM.

## Wymagania
- Windows, **Python 3.13+**
- (opcjonalnie) Tesseract OCR - tylko dla skanowanych PDF-ów; polski pakiet językowy jest w `tessdata/`
- (opcjonalnie) klucz API dostawcy LLM z serwerami w UE

## Instalacja (jedna komenda)
```
setup.bat
```
Skrypt: sprawdza Pythona, tworzy `.venv`, instaluje `requirements.txt`, sprawdza Tesseract,
tworzy plik `.env` z `.env.example` i testuje połączenie z LLM.

Ręcznie:
```
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

## Konfiguracja LLM (plik `.env`)
Wymóg organizatora: API firmy z serwerami w UE/EOG, model otwarty, klucz dostarczony do testów.
Wszystko idzie przez API zgodne z OpenAI, więc zmiana dostawcy to zmiana w `.env`:

| `LLM_PROVIDER` | Dostawca | Region | Domyślny model |
|---|---|---|---|
| `ovh` | OVHcloud AI Endpoints | Francja (UE) | `Mistral-Nemo-Instruct-2407` |
| `scaleway` | Scaleway Generative APIs | Francja (UE) | `mistral-nemo-instruct-2407` |
| `ionos` | IONOS AI Model Hub | Niemcy (UE) | `meta-llama/Meta-Llama-3.1-8B-Instruct` |
| `mistral` | Mistral AI | Francja (UE) | `open-mistral-nemo` |
| `ollama` | lokalnie | ten komputer | `qwen2.5:3b` |

Minimalny `.env` dla komisji:
```
LLM_PROVIDER=ovh
LLM_API_KEY=<klucz dostarczony osobno>
LLM_ENABLED=1
```
Profile są w `config/llm_providers.yaml`; `LLM_BASE_URL` i `LLM_MODEL` w `.env` nadpisują profil.
Nazwy modeli u dostawców się zmieniają - aktualne pokaże `python -m odw.check_llm --list`.

**Klucza API nie umieszczaj w repozytorium** (`.env` jest w `.gitignore`) - przekaż go osobno.

### Sprawdzenie połączenia
```
.venv\Scripts\python.exe -m odw.check_llm
```
Pokazuje dostawcę, adres, model, robi próbne zapytanie i podpowiada, co poprawić
(zły klucz, zła nazwa modelu, brak internetu). Gdy LLM jest niedostępny, aplikacja nadal działa na regexach.

## Poprawianie błędów (bez zmiany kodu)
Przy każdym polu jest przycisk **Popraw**: wpisz (albo zaznacz w tekście dokumentu) poprawną wartość i opcjonalnie powód.
Aplikacja pokazuje, dlaczego reguły nie zadziałały (wartości nie ma w tekście, jest poza sekcją, który wzorzec pasuje do czego),
buduje nowy wzorzec **bez LLM** z otoczenia wartości i sprawdza go na tym dokumencie oraz na zbiorze treningowym (F1 pola przed/po).
Opcjonalnie prosi o propozycję także LLM (też weryfikowaną). Wynik to log „co dopisać w którym pliku YAML” - poprawki
wprowadza człowiek. Po sprawdzeniu przycisk **Wdróż** zapisuje wzorzec w `config/learned/patterns.yaml` (pamięć aplikacji, działa bez LLM, można cofnąć);
wdrożenie jest blokowane, gdy wzorzec pogarsza F1 na zbiorze treningowym. Poprawki oznaczone jako przykłady trafiają też do promptu LLM
(few-shot, dobierane po podobieństwie fragmentu). Wszystkie zgłoszenia trafiają do bazy (`data/corrections.db`, zakładka „Baza przykładów”).

## Uruchomienie
- `uruchom.bat` - strona na http://localhost:8000
- `test_f1.bat` - F1 na danych treningowych UZP + testy jednostkowe
