"""Robustez operacional: downloads seguros, gravação atômica, upload e estados de produção."""
import threading

import httpx
import pytest

from app import paths
from app.fsutil import atomic_write_text
from app.providers import http


@pytest.fixture
def served(monkeypatch):
    body = {"data": b"x" * 5000}

    def handler(request):
        return httpx.Response(200, content=body["data"], headers={"Content-Length": str(len(body["data"]))})

    monkeypatch.setattr(http, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    return body


def test_download_grava_no_destino_sem_sobrar_parcial(served):
    dest = paths.cache_dir() / "dl" / "a.bin"
    http.download("https://example.org/a", dest)
    assert dest.read_bytes() == b"x" * 5000
    assert not [p for p in dest.parent.iterdir() if p.name != "a.bin"]


def test_download_respeita_limite(served):
    dest = paths.cache_dir() / "dl" / "big.bin"
    with pytest.raises(http.DownloadRejected):
        http.download("https://example.org/a", dest, max_bytes=1000)
    assert not dest.exists() and not list(dest.parent.glob("big.bin*"))


def test_download_cancelado_apaga_parcial(served):
    dest = paths.cache_dir() / "dl" / "c.bin"
    with pytest.raises(http.DownloadRejected):
        http.download("https://example.org/a", dest, cancel=lambda: True)
    assert not list(dest.parent.glob("c.bin*"))


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x/y", "javascript:alert(1)"])
def test_download_recusa_esquema(served, url):
    with pytest.raises(http.DownloadRejected):
        http.download(url, paths.cache_dir() / "dl" / "x.bin")


def test_download_recusa_destino_fora_das_pastas(served, tmp_path):
    with pytest.raises(http.DownloadRejected):
        http.download("https://example.org/a", tmp_path.parent / "fora" / "x.bin")


def test_downloads_simultaneos_do_mesmo_destino(served):
    dest = paths.cache_dir() / "dl" / "same.bin"
    errors = []

    def go():
        try:
            http.download("https://example.org/a", dest)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and dest.read_bytes() == b"x" * 5000
    assert not [p for p in dest.parent.glob("same.bin.*")]


def test_gravacao_atomica_concorrente(tmp_path):
    target = tmp_path / "state.json"
    errors = []

    def go(i):
        try:
            for _ in range(20):
                atomic_write_text(target, f'{{"writer": {i}}}')
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and target.read_text().startswith('{"writer": ')
    assert not list(tmp_path.glob("state.json.*"))
