"""Testy konfiguracji dostawców LLM oraz odporności klienta (ponawianie, brak response_format)."""
from types import SimpleNamespace

import pytest

from odw import llm, settings

PROVIDERS = settings.load_providers()


class HttpError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status_code = status


class ScriptedClient:
    """Klient, który zwraca kolejno zadane wyniki (wyjątek albo tekst odpowiedzi)."""
    def __init__(self, *steps):
        self.steps, self.calls = list(steps), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=step))])


# ---------------------------------------------------------------- profile dostawców
def test_provider_preset_is_used():
    c = settings.resolve_llm_config({"LLM_PROVIDER": "ovh", "LLM_API_KEY": "sekret"}, PROVIDERS)
    assert c["base_url"] == "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1"
    assert c["model"] and c["provider"] == "ovh" and "UE" in c["region"]
    assert c["api_key"] == "sekret"


def test_env_overrides_preset():
    c = settings.resolve_llm_config({"LLM_PROVIDER": "ovh", "LLM_MODEL": "inny-model",
                                     "LLM_BASE_URL": "https://example.eu/v1"}, PROVIDERS)
    assert (c["base_url"], c["model"]) == ("https://example.eu/v1", "inny-model")


def test_defaults_to_local_ollama_and_placeholder_key():
    c = settings.resolve_llm_config({}, PROVIDERS)
    assert c["provider"] == "ollama" and c["base_url"].startswith("http://localhost")
    assert c["api_key"] == "none"             # klient openai wymaga niepustego klucza


def test_custom_server_without_provider():
    c = settings.resolve_llm_config({"LLM_BASE_URL": "https://moj.serwer/v1", "LLM_MODEL": "m"}, PROVIDERS)
    assert c["provider"] == "custom" and c["base_url"] == "https://moj.serwer/v1"


@pytest.mark.parametrize("name", [n for n in PROVIDERS if n != "ollama"])
def test_remote_providers_are_https_and_in_eu(name):
    p = PROVIDERS[name]
    assert p["base_url"].startswith("https://") and p["model"] and p["label"]
    assert "UE" in p["region"]                 # wymóg organizatora: serwery w UE/EOG


# ---------------------------------------------------------------- chat_json
def test_retries_on_rate_limit_then_succeeds():
    c = ScriptedClient(HttpError(429), HttpError(503), '{"ok": true}')
    assert llm.chat_json(c, [{"role": "user", "content": "x"}]) == '{"ok": true}'
    assert len(c.calls) == 3


def test_falls_back_without_response_format_on_400():
    c = ScriptedClient(HttpError(400), '{"ok": true}')
    assert llm.chat_json(c, []) == '{"ok": true}'
    assert "response_format" in c.calls[0] and "response_format" not in c.calls[1]


def test_bad_key_fails_immediately_without_retries():
    c = ScriptedClient(HttpError(401), '{"ok": true}')
    with pytest.raises(HttpError):
        llm.chat_json(c, [])
    assert len(c.calls) == 1


def test_gives_up_after_configured_retries(monkeypatch):
    monkeypatch.setattr(settings, "LLM_RETRIES", 2)
    c = ScriptedClient(*[HttpError(503)] * 5)
    with pytest.raises(HttpError):
        llm.chat_json(c, [])
    assert len(c.calls) == 3                   # 1 próba + 2 ponowienia


def test_error_explanations_are_helpful():
    assert "klucz" in llm.explain_error(HttpError(401))
    assert "modelu" in llm.explain_error(HttpError(404))
    assert "limit" in llm.explain_error(HttpError(429))
    assert "połączenia" in llm.explain_error(ConnectionError())
