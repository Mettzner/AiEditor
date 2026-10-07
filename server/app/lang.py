"""Idioma do vídeo e checagem do idioma dos textos na tela (MELHORIA_PRECISAO_VISUAL.md §11).

O idioma do vídeo é escolhido na Criação, já preenchido com o detectado no roteiro. Narração, legendas, textos na
tela e transcrição seguem ele; as buscas de vídeos e imagens são sempre em inglês.

Detecção offline com lingua. Em textos curtos a biblioteca erra (classifica "12 protein-rich plants"
como alemão), então a divergência só é marcada quando o idioma do vídeo tem confiança baixa e outro
idioma tem confiança alta.
"""
from __future__ import annotations

import re
import threading

# Idiomas selecionáveis: escrita latina ou cirílica (as fontes dos overlays e das legendas cobrem; a quebra de linha
# das legendas é por espaço). código → (nome em inglês para o LLM, nome na tela, Language do lingua)
LANGUAGES: dict[str, tuple[str, str, str]] = {
    "en": ("English", "Inglês", "ENGLISH"),
    "pt": ("Portuguese", "Português", "PORTUGUESE"),
    "es": ("Spanish", "Espanhol", "SPANISH"),
    "fr": ("French", "Francês", "FRENCH"),
    "de": ("German", "Alemão", "GERMAN"),
    "it": ("Italian", "Italiano", "ITALIAN"),
    "nl": ("Dutch", "Holandês", "DUTCH"),
    "pl": ("Polish", "Polonês", "POLISH"),
    "ro": ("Romanian", "Romeno", "ROMANIAN"),
    "sv": ("Swedish", "Sueco", "SWEDISH"),
    "da": ("Danish", "Dinamarquês", "DANISH"),
    "no": ("Norwegian", "Norueguês", "BOKMAL"),
    "fi": ("Finnish", "Finlandês", "FINNISH"),
    "cs": ("Czech", "Tcheco", "CZECH"),
    "sk": ("Slovak", "Eslovaco", "SLOVAK"),
    "sl": ("Slovenian", "Esloveno", "SLOVENE"),
    "hu": ("Hungarian", "Húngaro", "HUNGARIAN"),
    "hr": ("Croatian", "Croata", "CROATIAN"),
    "ca": ("Catalan", "Catalão", "CATALAN"),
    "tr": ("Turkish", "Turco", "TURKISH"),
    "id": ("Indonesian", "Indonésio", "INDONESIAN"),
    "ms": ("Malay", "Malaio", "MALAY"),
    "vi": ("Vietnamese", "Vietnamita", "VIETNAMESE"),
    "ru": ("Russian", "Russo", "RUSSIAN"),
    "uk": ("Ukrainian", "Ucraniano", "UKRAINIAN"),
    "bg": ("Bulgarian", "Búlgaro", "BULGARIAN"),
}
NAMES = {code: v[0] for code, v in LANGUAGES.items()}
_ISO_FIX = {"nb": "no"}  # lingua devolve Bokmål como "nb"; Whisper e Darkvi usam "no"

_detector = None
_lock = threading.Lock()


def _get():
    global _detector
    with _lock:
        if _detector is None:
            from lingua import Language, LanguageDetectorBuilder

            _detector = LanguageDetectorBuilder.from_languages(
                *(getattr(Language, v[2]) for v in LANGUAGES.values())).build()
        return _detector


def name(code: str | None) -> str:
    return NAMES.get(code or "", code or "English")


def options() -> list[dict]:
    """Idiomas para a tela, em ordem alfabética do nome em português."""
    return sorted(({"value": c, "label": v[1]} for c, v in LANGUAGES.items()), key=lambda o: o["label"])


def _code(language) -> str:
    iso = language.iso_code_639_1.name.lower()
    return _ISO_FIX.get(iso, iso)


def _confidences(text: str) -> dict[str, float]:
    return {_code(c.language): c.value for c in _get().compute_language_confidence_values(text)}


def detect(text: str) -> str | None:
    """Idioma de um texto longo (roteiro). None se não houver texto suficiente."""
    if len(re.findall(r"[^\W\d_]{2,}", text)) < 5:
        return None
    lang = _get().detect_language_of(text)
    return _code(lang) if lang else None


def checkable(text: str) -> bool:
    """Fora da checagem: textos com menos de 2 palavras com letras (números puros, nomes curtos)."""
    return len(re.findall(r"[^\W\d_]{2,}", text or "")) >= 2


def is_mismatch(text: str, target: str) -> bool:
    if not checkable(text):
        return False
    conf = _confidences(text)
    top = max(conf, key=conf.get)
    mine = conf.get(target, 0.0)
    # com muitos idiomas a confiança de um texto curto se divide entre parentes (pt × es): basta um idioma
    # claramente à frente e o do vídeo quase ausente
    return top != target and mine < 0.15 and conf[top] >= 0.25 and conf[top] >= 3 * mine
