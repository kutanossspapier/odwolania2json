"""Ustawienia aplikacji. Wartości można nadpisać w pliku .env (patrz .env.example)."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

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

# LLM (API zgodne z OpenAI) - użyte w kolejnych krokach
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "ollama")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5:7b")
LLM_ENABLED = os.getenv("LLM_ENABLED", "0").lower() in {"1", "true", "tak", "yes"}
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))
LLM_MAX_CHARS = int(os.getenv("LLM_MAX_CHARS", "12000"))


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
