"""Similaridade semântica OPCIONAL para o pré-ranking (Fase C5).

Desligada por padrão: nada é instalado nem baixado sem o usuário pedir. Para ligar:
    1. pip install sentence-transformers   (traz PyTorch: ~1–2 GB em disco)
    2. settings.ranking = {"semantic": true, "model": "sentence-transformers/all-MiniLM-L6-v2"}
Custo operacional medido em referência pública do modelo (não neste PC): ~90 MB de pesos, ~0,5–1 GB de RAM
com o PyTorch carregado, CPU basta (dezenas de títulos por cena em milissegundos a poucos segundos). GPU não é
necessária. Sem a biblioteca, ou com erro ao carregar, `available()` é False e o ranking lexical segue sozinho.
Chamada local: sem custo por uso e sem enviar dados para fora.
"""
from __future__ import annotations

import logging
import threading

from ..config import load_settings

log = logging.getLogger("aieditor.embeddings")
_model = None
_failed = False
_lock = threading.Lock()


def _cfg() -> dict:
    return load_settings().get("ranking") or {}


def enabled() -> bool:
    return bool(_cfg().get("semantic", False))


def _load():
    global _model, _failed
    with _lock:
        if _model is not None or _failed:
            return _model
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

            _model = SentenceTransformer(_cfg().get("model", "sentence-transformers/all-MiniLM-L6-v2"))
        except Exception as e:  # noqa: BLE001 — capacidade opcional: falhou, fica desligada
            log.info("ranking semântico indisponível: %s", e)
            _failed = True
        return _model


def available() -> bool:
    return enabled() and _load() is not None


def similarities(query: str, texts: list[str]) -> list[float] | None:
    """Cosseno (0–1) entre a consulta e cada texto; None se a capacidade não estiver disponível."""
    if not texts or not available():
        return None
    try:
        vecs = _model.encode([query, *texts], normalize_embeddings=True)  # type: ignore[union-attr]
    except Exception as e:  # noqa: BLE001
        log.warning("ranking semântico falhou: %s", e)
        return None
    q = vecs[0]
    return [max(0.0, float(sum(a * b for a, b in zip(q, v)))) for v in vecs[1:]]
