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


# ---------------------------------------------------------------- API: upload, estados, validação, origem
from datetime import timedelta  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.models import Channel, Preset, Production, now  # noqa: E402


@pytest.fixture
def api(monkeypatch):
    from app import whisper_models
    from app.main import app

    monkeypatch.setattr(whisper_models, "model_ready", lambda m: True)
    with session_scope() as s:
        ch = Channel(name="op", preset=Preset(tts_voice="v1").model_dump())
        s.add(ch)
        s.commit()
        s.refresh(ch)
        cid = ch.id
    with TestClient(app) as c:
        yield c, cid


def _post(c, cid, audio: bytes, name="n.mp3"):
    return c.post("/api/productions", data={"channel_id": cid, "title": "t", "script": "um roteiro curto"},
                  files={"audio": (name, audio, "audio/mpeg")})


def test_upload_valido_entra_na_fila_so_com_o_audio_completo(api, monkeypatch):
    from app.api import productions as prod_api

    seen = {}

    def probe(path):
        seen["size"] = path.stat().st_size  # o arquivo inteiro já está gravado quando é conferido
        with session_scope() as s:  # e a produção ainda nem existe: o worker não tem o que pegar
            seen["running"] = len(s.exec(select(Production).where(Production.title == "t-upload")).all())
        return 61.0

    monkeypatch.setattr(prod_api, "audio_duration", probe)
    c, cid = api
    r = c.post("/api/productions", data={"channel_id": cid, "title": "t-upload", "script": "um roteiro curto"},
               files={"audio": ("n.mp3", b"\xff\xfb" * 4000, "audio/mpeg")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued" and seen == {"size": 8000, "running": 0}
    from app.config import job_dir

    assert (job_dir(body["id"]) / "input" / "narration.mp3").stat().st_size == 8000


def test_upload_invalido_ou_grande_demais_nao_cria_producao(api, monkeypatch):
    from app.api import productions as prod_api
    from app.config import update_settings

    c, cid = api
    with session_scope() as s:
        before = len(s.exec(select(Production)).all())
    monkeypatch.setattr(prod_api, "audio_duration", lambda p: None)
    assert _post(c, cid, b"not audio").status_code == 400
    monkeypatch.setattr(prod_api, "audio_duration", lambda p: 5 * 3600.0)
    assert _post(c, cid, b"\xff\xfb" * 10).status_code == 413
    update_settings({"upload": {"max_mb": 1}})
    try:
        monkeypatch.setattr(prod_api, "audio_duration", lambda p: 60.0)
        assert _post(c, cid, b"\x00" * (1024 * 1024 + 10)).status_code == 413
    finally:
        update_settings({"upload": {"max_mb": 500}})
    assert _post(c, cid, b"", name="n.ogg").status_code == 400
    with session_scope() as s:
        assert len(s.exec(select(Production)).all()) == before
    from app.config import UPLOADS_DIR

    assert not list(UPLOADS_DIR.glob("*.upload"))


def test_producao_preparando_orfa_vira_falha_e_nao_roda():
    from app.worker.__main__ import _claim_next, _recover

    with session_scope() as s:
        p = Production(title="orfa", script="x", config={}, status="preparing", updated_at=now() - timedelta(hours=1))
        s.add(p)
        s.commit()
        s.refresh(p)
        pid = p.id
    _recover()
    with session_scope() as s:
        assert s.get(Production, pid).status == "failed"
    assert _claim_next() != pid


def test_config_invalido_da_422(api):
    c, cid = api
    r = c.post("/api/productions", data={"channel_id": cid, "title": "t", "script": "x", "config": "{oops"})
    assert r.status_code == 422


def test_settings_validados(api):
    c, _ = api
    r = c.put("/api/settings", json={"settings": {"youtube": {"buckets": {"search": {"daily_limit": -1}}},
                                                  "selection": {"youtube_results_per_query": 80,
                                                                "search_cache_ttl_hours": {"pixabay": 2}}}})
    assert r.status_code == 422
    errors = r.json()["detail"]["errors"]
    assert any("daily_limit" in e for e in errors) and any("youtube_results_per_query" in e for e in errors)
    assert any("pixabay" in e for e in errors)


def test_precos_validados_e_datados(api):
    c, _ = api
    prices = c.get("/api/prices").json()
    pid = prices[0]["id"]
    assert c.put("/api/prices", json=[{"id": pid, "price": -1}]).status_code == 422
    assert c.put("/api/prices", json=[{"id": pid, "price": float(prices[0]["price"]) + 0.01}]).status_code == 200
    row = next(p for p in c.get("/api/prices").json() if p["id"] == pid)
    assert row["currency"] == "USD" and row["as_of"]


def test_erro_do_oauth_e_escapado(api, monkeypatch):
    from app.providers.storage import gdrive

    def boom(state, code):
        raise RuntimeError("<script>alert(1)</script>")

    monkeypatch.setattr(gdrive, "finish_auth", boom)
    c, _ = api
    r = c.get("/api/auth/google/callback", params={"state": "s", "code": "c"})
    assert "<script>" not in r.text and "&lt;script&gt;" in r.text


def test_host_e_origem_externos_sao_recusados(api):
    c, _ = api
    assert c.get("/api/health", headers={"host": "evil.example"}).status_code == 403
    assert c.post("/api/app/show", headers={"origin": "https://evil.example"}).status_code == 403
    assert c.get("/api/health").status_code == 200
    assert c.post("/api/app/show", headers={"origin": "http://localhost:3000"}).status_code == 200


def test_backup_antes_de_migrar(tmp_path, monkeypatch):
    import sqlite3

    from sqlalchemy import create_engine

    from app import db

    path = tmp_path / "aieditor.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE schema_version (version INTEGER NOT NULL, name TEXT, applied_at TEXT)")
    con.execute("INSERT INTO schema_version (version, name) VALUES (1, 'baseline')")
    con.execute("CREATE TABLE dados (x TEXT)")
    con.execute("INSERT INTO dados VALUES ('valioso')")
    con.commit()
    con.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "engine", create_engine(f"sqlite:///{path}"))
    dest = db.backup_before_migrations()
    assert dest and dest.exists()
    assert sqlite3.connect(dest).execute("SELECT x FROM dados").fetchone() == ("valioso",)


def test_limpeza_preserva_saidas_e_manifestos(api):
    from app.config import job_dir

    c, _ = api
    with session_scope() as s:
        p = Production(title="limpa", script="x", config={}, status="done")
        s.add(p)
        s.commit()
        s.refresh(p)
        pid = p.id
    job = job_dir(pid)
    for rel in ("render/scenes/s001.mp4", "render/overlays/o001.mkv", "output/final.mp4", "output/manifest.json",
                "plan.json", "assets/s001.mp4"):
        (job / rel).parent.mkdir(parents=True, exist_ok=True)
        (job / rel).write_bytes(b"1234")
    r = c.post(f"/api/productions/{pid}/cleanup").json()
    assert r["files"] == 2 and r["freed_bytes"] == 8
    assert not (job / "render" / "scenes").exists()
    assert all((job / rel).exists() for rel in ("output/final.mp4", "output/manifest.json", "plan.json",
                                                "assets/s001.mp4"))


def test_versao_unica():
    import json
    import re

    from app import paths

    root = paths.REPO_ROOT
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    assert json.loads((root / "web" / "package.json").read_text(encoding="utf-8"))["version"] == version
    assert re.search(r'^version = "([^"]+)"', (root / "server" / "pyproject.toml").read_text(encoding="utf-8"),
                     re.M).group(1) == version
