"""Testes isolados: banco e dados em pasta temporária, sem rede e sem chaves reais."""
import os
import tempfile

os.environ["AIEDITOR_DATA"] = tempfile.mkdtemp(prefix="aieditor_tests_")

from app.db import init_db  # noqa: E402  (depois de apontar AIEDITOR_DATA para a pasta temporária)

init_db()

if os.environ.get("AIEDITOR_LIVE") != "1":
    from app.config import update_settings  # noqa: E402

    # a chave da Anthropic do Credential Manager fica visível aos testes: sem isto, a visão reserva (Claude)
    # faria chamadas reais. Testes da reserva ligam o Claude com chave e chamada simuladas.
    update_settings({"vision": {"provider": "gemini"}})

    import keyring  # noqa: E402
    from keyring.backends import null  # noqa: E402

    # chaves reais do Credential Manager ficam invisíveis aos testes (nada de chamada real nem de gasto); cada
    # teste que precisa de chave a simula
    keyring.set_keyring(null.Keyring())

    import httpx  # noqa: E402

    from app.providers import http as _http  # noqa: E402

    def _no_network(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"rede bloqueada nos testes: {request.url.host}")

    # cliente HTTP comum sem rede: provedores não simulados falham como offline, nunca gastam cota de verdade
    _http._client = httpx.Client(transport=httpx.MockTransport(_no_network))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fake_probe_for_placeholder_files(monkeypatch):
    """Testes antigos gravam bytes de mentira como "vídeo baixado": esses (minúsculos) recebem metadados
    simulados; arquivos de verdade continuam passando pelo ffprobe real."""
    from app.pipeline import media_index

    real = media_index.probe_video

    def probe(path):
        try:
            small = path.stat().st_size < 1024
        except OSError:
            small = False
        if small:
            return {"duration": 3600.0, "width": 1920, "height": 1080, "has_video": True}
        return real(path)

    monkeypatch.setattr(media_index, "probe_video", probe)
