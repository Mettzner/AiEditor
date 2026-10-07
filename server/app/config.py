"""Caminhos, configuração persistida (settings.json) e segredos (keyring)."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import keyring
from keyring.errors import PasswordDeleteError

from . import paths

# Todos os caminhos vêm de paths.py: <repo>/data em desenvolvimento, %LOCALAPPDATA%\AiEditor no app instalado.
ROOT = paths.REPO_ROOT
DATA_DIR = paths.data_dir()
JOBS_DIR = paths.jobs_dir()
CACHE_DIR = paths.cache_dir()
MUSIC_DIR = paths.music_dir()
SFX_DIR = paths.sfx_dir()
UPLOADS_DIR = paths.uploads_dir()
DB_PATH = DATA_DIR / "aieditor.db"
SETTINGS_PATH = DATA_DIR / "settings.json"

KEYRING_SERVICE = "AiEditor"

# Provedores com chave de API (guardadas no Windows Credential Manager).
SECRET_PROVIDERS = [
    "darkvi",
    "pexels",
    "pixabay",
    "youtube",
    "anthropic",
    "openai",
    "gemini",
    "fal",
    "elevenlabs",
    "freesound",  # efeitos sonoros CC0 (https://freesound.org/apiv2/apply)
    "google_oauth_client",  # JSON do client OAuth (Desktop/Web) do Google Cloud
    "google_refresh_token",
]

DEFAULT_SETTINGS: dict[str, Any] = {
    "stock_providers": [
        {"id": "pexels", "enabled": True, "priority": 1},
        {"id": "pixabay", "enabled": True, "priority": 2},
        {"id": "storyblocks", "enabled": False, "priority": 3},
        {"id": "shutterstock", "enabled": False, "priority": 4},
    ],
    # first: toda cena real tenta o YouTube primeiro enquanto houver cota no dia (zera à meia-noite do Pacífico);
    # sem cota, vai direto para bancos de vídeo e imagens. Desligado, só a fatia youtube_pct do preset tenta.
    # Cota por bucket (documentação oficial, 2026-10): search.list tem bucket próprio (100 chamadas/dia); os demais
    # endpoints dividem 10.000 unidades. Ajuste os limites se o Google aprovou outra cota para o seu projeto.
    # daily_quota/quota_reserve valem só no regime "legacy_units" (saldo único antigo).
    "youtube": {"enabled": True, "first": True, "creative_commons_only": True, "daily_quota": 10_000,
                "quota_reserve": 500, "max_duration": 1800, "quota_accounting_mode": "separate_buckets",
                "buckets": {"search": {"daily_limit": 100, "reserve": 5},
                            "default": {"daily_limit": 10_000, "reserve": 200}}},
    "ai_image_providers": [
        {"id": "darkvi", "enabled": True, "priority": 1},
    ],
    "ai_video_providers": [
        {"id": "fal", "enabled": False, "priority": 1, "model": "veo3.1-lite"},
    ],
    # Modelo por etapa (OTIMIZACAO_CUSTO_CLAUDE.md §2). thinking: "adaptive" (com effort) ou "off".
    "llm": {
        "plan": {"provider": "anthropic", "model": "claude-sonnet-5-5", "effort": "low", "thinking": "adaptive"},
        # Bíblia de Contexto: 1 chamada por vídeo lendo o roteiro inteiro; mais raciocínio = interpretação melhor
        "bible": {"provider": "anthropic", "model": "claude-sonnet-5-5", "effort": "high", "thinking": "adaptive"},
        "direct": {"provider": "anthropic", "model": "claude-sonnet-5-5", "effort": "low", "thinking": "off"},
        "rewrite": {"provider": "anthropic", "model": "claude-haiku-4-5", "effort": None, "thinking": "off"},
        "overlay": {"provider": "anthropic", "model": "claude-haiku-4-5", "effort": None, "thinking": "off"},
        # IA de visão reserva: avalia a folha de miniaturas e as imagens geradas quando o Gemini não pode
        # visão reserva (Gemini sem cota): Haiku custa metade do Sonnet por folha e julga miniaturas bem
        "vision": {"provider": "anthropic", "model": "claude-haiku-4-5", "effort": None, "thinking": "off"},
        "cache_ttl": "5m",
    },
    "render": {
        "mode": "quality",  # quality (libx264) | fast (h264_amf)
        "x264_preset": "veryfast",
        "crf": 20,
        "amf_bitrate": "12M",
        "parallel_scenes": 3,
        "intermediate_crf": 16,
    },
    "transcription": {"model": "small", "compute_type": "int8", "divergence_warn": 0.15},
    # Funil de seleção (MELHORIA_SELECAO_DE_CENAS.md)
    "selection": {
        "stock_queries_per_scene": 3,
        "youtube_queries_per_scene": 1,
        "results_per_query": 15,  # bancos de vídeo/foto (os modos fast/precise ajustam este)
        # YouTube: até 50 por página (1 chamada do bucket de busca, igual a 10 resultados). Os modos não mexem nele.
        "youtube_results_per_query": 50,
        # validade do cache de busca por provedor, em horas (o Pixabay exige ≥ 24 h); dados vencidos são apagados
        "search_cache_ttl_hours": {"default": 168, "pexels": 168, "pixabay": 168, "youtube": 168, "archives": 336},
        "search_error_ttl_seconds": 120,
        "min_height": 1080,
        "youtube_min_height": 720,
        "min_aspect": 1.55,
        "max_aspect": 2.0,
        "duration_margin": 0.5,
        "prerank_keep": 8,
        "frames_per_candidate": 3,
        "accept_score": 7.5,
        "accept_gap": 1.5,
        "tiebreak_top": 3,
        "tiebreak_frames": 4,
        "min_score": 6.5,
        # sem a IA de visão, nota de texto mínima para aceitar sem tentar a próxima fonte (0–10)
        "text_min_score": 3.0,
        "gemini_model": "gemini-3.8-flash",
        "parallel_scenes": 4,
        "parallel_downloads": 4,
        # Modo de seleção (AJUSTE_ESTILO_CONTEXTUAL.md §10.5): o modo corta esforço, não critério.
        "modes": {
            "fast": {"results_per_query": 10, "prerank_keep": 6, "frames_per_candidate": 2, "tiebreak": False,
                     "rewrite_below": 5.0, "parallel_scenes": 6},
            "precise": {"results_per_query": 15, "prerank_keep": 8, "frames_per_candidate": 3, "tiebreak": True,
                        "rewrite_below": 6.5, "parallel_scenes": 4},
        },
        # Sempre bloqueados, em qualquer cena (direitos autorais/Content ID). Editável.
        "blocked_franchises": [
            "mario", "nintendo", "zelda", "minecraft", "fortnite", "roblox", "gta", "grand theft auto", "call of duty",
            "fifa", "pokemon", "pokémon", "sonic", "disney", "pixar", "marvel", "dc comics", "batman", "superman",
            "spider-man", "spiderman", "star wars", "harry potter", "simpsons", "spongebob", "peppa pig", "barbie",
            "lego", "playstation", "xbox", "league of legends", "valorant", "among us", "the sims", "sims",
            "fallout", "overwatch", "apex legends", "pubg", "free fire", "dreamworks", "looney tunes", "naruto",
            "dragon ball", "one piece",
        ],
    },
    # Acervos históricos grátis (CONTEXTO_PROFUNDO_DO_ROTEIRO.md §5): só domínio público, CC0 ou CC BY.
    # Material de arquivo tem resolução menor: os mínimos abaixo valem só para essas fontes.
    "archives": {"enabled": True, "sources": {"wikimedia": True, "loc": True, "internet_archive": True},
                 "max_video_mb": 300, "min_photo_height": 500, "min_video_height": 240},
    "darkvi": {"remaining": None, "limit": None, "updated_at": None, "tts_poll_seconds": 3, "tts_timeout_seconds": 1800},
    "gemini": {"blocked_until": None, "blocked_reason": ""},
    # Quem avalia clipes e imagens: "auto" (Gemini; sem cota ou com erro, Claude), "gemini" ou "claude".
    # Sem nenhuma, a escolha é só pelo texto dos títulos (às cegas).
    "vision": {"provider": "auto"},
    # Teto de gasto com IA (Claude + Gemini) por produção, em US$; 0 = sem teto. Ao chegar perto, a visão pelo
    # Claude para e o resto da seleção segue pelo ranking de texto (app/budget.py).
    "budget": {"max_usd_per_production": 2.0},
    # OpenAI (ChatGPT) como 2ª IA de visão, antes do Claude; só com a chave "openai" configurada
    "openai": {"vision_model": "gpt-4.1-mini"},
    "music": {"library_dir": str(MUSIC_DIR), "default_volume_db": -22},
    # Efeitos sonoros: data/sfx/<categoria>/. Com chave do Freesound, baixa sons CC0 quando faltam; sem ela (ou
    # offline), usa sons sintetizados. allow_attribution aceita também CC BY (creditado no creditos.txt).
    "sfx": {"library_dir": str(SFX_DIR), "freesound": True, "allow_attribution": False, "per_category": 4},
    "folders": {"cache": str(CACHE_DIR), "jobs": str(JOBS_DIR), "auto_cleanup": False},
    "fonts_dir": str(DATA_DIR / "fonts"),
    "words_per_minute": {"en": 150, "pt": 145, "es": 150},
    "worker": {"max_parallel_productions": 2},
}

_lock = threading.Lock()


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings() -> dict[str, Any]:
    with _lock:
        if SETTINGS_PATH.exists():
            stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            return _deep_merge(DEFAULT_SETTINGS, stored)
        return json.loads(json.dumps(DEFAULT_SETTINGS))


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(SETTINGS_PATH)
    return settings


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    return save_settings(_deep_merge(load_settings(), patch))


def get_secret(provider: str) -> str | None:
    env = os.environ.get(f"AIEDITOR_{provider.upper()}_KEY")
    if env:
        return env
    if provider == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    return keyring.get_password(KEYRING_SERVICE, provider)


def set_secret(provider: str, value: str | None) -> None:
    if value:
        keyring.set_password(KEYRING_SERVICE, provider, value)
    else:
        try:
            keyring.delete_password(KEYRING_SERVICE, provider)
        except PasswordDeleteError:
            pass


def job_dir(production_id: int) -> Path:
    d = JOBS_DIR / str(production_id)
    d.mkdir(parents=True, exist_ok=True)
    return d
