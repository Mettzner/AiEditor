"""IA de visão reserva: sem cota do Gemini, o Claude avalia; a produção nunca escolhe clipes às cegas à toa."""
from __future__ import annotations

import pytest

from app.config import update_settings
from app.pipeline.visual import VisionSheet
from app.providers.http import ProviderError
from app.providers.llm import gemini, vision
from app.providers.llm.anthropic import AnthropicLLM
from app.providers.llm.base import LLMUsage

SHEET = VisionSheet(candidates=[])


@pytest.fixture
def auto_mode(monkeypatch):
    update_settings({"vision": {"provider": "auto"}})
    monkeypatch.setattr(vision, "get_secret", lambda p: "k")
    yield
    update_settings({"vision": {"provider": "gemini"}})


def test_sem_cota_do_gemini_o_claude_assume(auto_mode, monkeypatch):
    calls = []

    def quota(*a, **k):
        raise gemini.GeminiQuotaExhausted("cota diária")

    def fake_llm(task, **kw):
        calls.append((task, len(kw["images"])))
        return SHEET, LLMUsage(cost=0.01)

    monkeypatch.setattr(vision, "gemini_usable", lambda: True)
    monkeypatch.setattr(gemini, "rate_sheet", quota)
    monkeypatch.setattr("app.providers.llm.base.call_llm", fake_llm)
    parsed, cost, provider = vision.rate_sheet(b"jpeg", "prompt", "gemini-x", VisionSheet)
    assert provider == "claude" and cost == 0.01 and calls == [("vision", 1)]


def test_gemini_disponivel_e_usado_primeiro(auto_mode, monkeypatch):
    monkeypatch.setattr(vision, "gemini_usable", lambda: True)
    monkeypatch.setattr(gemini, "rate_sheet", lambda *a, **k: (SHEET, 0.001))
    assert vision.rate_sheet(b"jpeg", "p", "g", VisionSheet)[2] == "gemini"


def test_so_gemini_configurado_propaga_a_cota(monkeypatch):
    def quota(*a, **k):
        raise gemini.GeminiQuotaExhausted("cota")

    monkeypatch.setattr(vision, "gemini_usable", lambda: True)
    monkeypatch.setattr(gemini, "rate_sheet", quota)
    with pytest.raises(gemini.GeminiQuotaExhausted):
        vision.rate_sheet(b"jpeg", "p", "g", VisionSheet)  # conftest: provider "gemini", sem reserva


def test_sem_nenhuma_visao(monkeypatch):
    monkeypatch.setattr(vision, "gemini_usable", lambda: False)
    assert not vision.available()
    with pytest.raises(ProviderError):
        vision.rate_sheet(b"jpeg", "p", "g", VisionSheet)


def test_claude_recebe_a_imagem_antes_do_texto():
    params = AnthropicLLM._params("claude-sonnet-5-5", "sys", "avalie", None, "low", "off", 4000, "5m",
                                  images=[b"\xff\xd8jpeg"])
    content = params["messages"][0]["content"]
    assert [b["type"] for b in content] == ["image", "text"]
    assert content[0]["source"]["media_type"] == "image/jpeg" and content[1]["text"] == "avalie"
