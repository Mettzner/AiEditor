"""Idioma do vídeo e checagem do idioma dos textos na tela (MELHORIA_PRECISAO_VISUAL.md §11).

Detecção offline com lingua. Em textos curtos a biblioteca erra (classifica "12 protein-rich plants"
como alemão), então a divergência só é marcada quando o idioma do vídeo tem confiança baixa e outro
idioma tem confiança alta.
"""
from __future__ import annotations

import re
import threading

NAMES = {"en": "English", "pt": "Portuguese", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian"}

_detector = None
_lock = threading.Lock()


def _get():
    global _detector
    with _lock:
        if _detector is None:
            from lingua import Language, LanguageDetectorBuilder

            _detector = LanguageDetectorBuilder.from_languages(
                Language.ENGLISH, Language.PORTUGUESE, Language.SPANISH, Language.FRENCH, Language.GERMAN,
                Language.ITALIAN,
            ).build()
        return _detector


def name(code: str | None) -> str:
    return NAMES.get(code or "", code or "English")


def _confidences(text: str) -> dict[str, float]:
    return {c.language.iso_code_639_1.name.lower(): c.value for c in _get().compute_language_confidence_values(text)}


def detect(text: str) -> str | None:
    """Idioma de um texto longo (roteiro). None se não houver texto suficiente."""
    if len(re.findall(r"[^\W\d_]{2,}", text)) < 5:
        return None
    lang = _get().detect_language_of(text)
    return lang.iso_code_639_1.name.lower() if lang else None


def checkable(text: str) -> bool:
    """Fora da checagem: textos com menos de 2 palavras com letras (números puros, nomes curtos)."""
    return len(re.findall(r"[^\W\d_]{2,}", text or "")) >= 2


def is_mismatch(text: str, target: str) -> bool:
    if not checkable(text):
        return False
    conf = _confidences(text)
    top = max(conf, key=conf.get)
    return top != target and conf.get(target, 0.0) < 0.15 and conf[top] >= 0.4
