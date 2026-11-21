import tempfile
from pathlib import Path

import pytest

from odw import learned, llm

# Testy nie mogą zależeć od tego, czego aplikacja nauczyła się z poprawek użytkownika (config/learned).
# Ustawiamy to na poziomie modułu, bo moduły testowe ładują klasy już przy imporcie.
learned.PATH = Path(tempfile.mkdtemp(prefix="odw_learned_")) / "patterns.yaml"


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Testy nie mogą naprawdę czekać na przerwy między ponowieniami zapytań do LLM."""
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
