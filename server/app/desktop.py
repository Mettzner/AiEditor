"""Ganchos do app de desktop (launcher) usados pela API.

O launcher (pywebview + bandeja) roda no mesmo processo do servidor e registra aqui as funções que a API pode
chamar, por exemplo trazer a janela para frente quando o atalho é aberto de novo (instância única).
Em desenvolvimento (uvicorn + next dev) nada é registrado e os endpoints viram no-op.
"""
from __future__ import annotations

import os
import webbrowser
from typing import Callable

show_window: Callable[[], None] | None = None


def is_desktop() -> bool:
    return os.environ.get("AIEDITOR_DESKTOP") == "1"


def bring_to_front() -> bool:
    if show_window is None:
        return False
    show_window()
    return True


def open_external(url: str) -> bool:
    """Abre no navegador padrão. O Google recusa login OAuth dentro de WebView embutida."""
    if not url.startswith(("https://", "http://")):
        return False
    return webbrowser.open(url)
