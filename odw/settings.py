"""Ustawienia aplikacji. Wartości można nadpisać w pliku .env (patrz .env.example)."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_dotenv()

CLASSES_DIR = Path(os.getenv("CLASSES_DIR", ROOT / "config" / "classes"))
TESSDATA_DIR = Path(os.getenv("TESSDATA_PREFIX", ROOT / "tessdata"))
OCR_LANG = os.getenv("OCR_LANG", "pol")

def load_providers() -> dict:
    """Profile dostawców LLM z config/llm_providers.yaml."""
    p = ROOT / "config" / "llm_providers.yaml"
    if not p.exists():
        return {}
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("providers", {})


def resolve_llm_config(env, providers: dict | None = None) -> dict:
    """Składa konfigurację LLM. Pierwszeństwo: zmienne (.env) > profil dostawcy > domyślna Ollama."""
    providers = load_providers() if providers is None else providers
    name = (env.get("LLM_PROVIDER") or "").strip().lower()
    preset = providers.get(name) or {}
    if not preset and not env.get("LLM_BASE_URL"):
        name, preset = "ollama", providers.get("ollama", {})
    return {
        "provider": name or "custom",
        "label": preset.get("label", "własny serwer"),
        "region": preset.get("region", "nieznany"),
        "key_hint": preset.get("key_hint", ""),
        "base_url": env.get("LLM_BASE_URL") or preset.get("base_url") or "http://localhost:11434/v1",
        "model": env.get("LLM_MODEL") or preset.get("model") or "qwen2.5:3b",
        # klient openai wymaga niepustego klucza; serwery bez klucza (Ollama) ignorują wartość
        "api_key": env.get("LLM_API_KEY") or "none",
    }


# LLM (API zgodne z OpenAI): adres, klucz i model z .env albo z profilu dostawcy
_LLM = resolve_llm_config(os.environ)
LLM_PROVIDER, LLM_PROVIDER_LABEL, LLM_REGION = _LLM["provider"], _LLM["label"], _LLM["region"]
LLM_BASE_URL, LLM_API_KEY, LLM_MODEL = _LLM["base_url"], _LLM["api_key"], _LLM["model"]
LLM_ENABLED = os.getenv("LLM_ENABLED", "0").lower() in {"1", "true", "tak", "yes"}
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))
LLM_MAX_CHARS = int(os.getenv("LLM_MAX_CHARS", "12000"))
LLM_RETRIES = int(os.getenv("LLM_RETRIES", "3"))
# Tryb "LLM poprawia regex": 0 (domyślnie) = tylko podejrzane; 1 = model weryfikuje WSZYSTKIE jednowartościowe pola (na 5 zestawach obniżyło F1 z 1.000 do 0.988: model dopisał adres do nazwy kancelarii)
# (niska pewność, ostrzeżenia walidacji, pola wcześniej poprawiane przez człowieka)
LLM_REVIEW_ALL = os.getenv("LLM_REVIEW_ALL", "0").lower() in {"1", "true", "tak", "yes"}


def tesseract_cmd() -> str | None:
    """Ścieżka do tesseract.exe: z .env, z PATH albo domyślna instalacja Windows."""
    explicit = os.getenv("TESSERACT_CMD")
    if explicit:
        return explicit
    found = shutil.which("tesseract")
    if found:
        return found
    default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    return str(default) if default.exists() else None
