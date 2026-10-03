"""EMPACOTAMENTO_INSTALADOR_WINDOWS.md: caminhos, migrações, API do app, frontend estático, OAuth e launcher
(offline). O executável e o instalador são testados pelo build.ps1 (teste de fumaça)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app import paths, whisper_models
from app.migrations import MIGRATIONS, add_column, current_version, migrate


# ---------------------------------------------------------------- §3.1 caminhos
def test_dados_seguem_aieditor_data_e_nunca_a_pasta_do_programa(monkeypatch, tmp_path):
    monkeypatch.setenv("AIEDITOR_DATA", str(tmp_path / "dados"))
    assert paths.data_dir() == tmp_path / "dados"
    for fn in (paths.jobs_dir, paths.cache_dir, paths.logs_dir, paths.models_dir, paths.tmp_dir):
        assert fn().parent == tmp_path / "dados" and fn().exists()


def test_app_instalado_usa_localappdata(monkeypatch):
    import os

    monkeypatch.delenv("AIEDITOR_DATA", raising=False)
    monkeypatch.setattr(paths, "FROZEN", True)
    monkeypatch.setattr(paths, "_ensure", lambda p: p)  # não cria a pasta real durante o teste
    assert paths.data_dir() == Path(os.environ["LOCALAPPDATA"]) / "AiEditor"


def test_versao_vem_do_arquivo_version():
    assert paths.app_version() == (paths.REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def test_desenvolvimento_nao_serve_o_frontend_sem_pedir(monkeypatch):
    monkeypatch.delenv("AIEDITOR_SERVE_WEB", raising=False)
    assert paths.web_dir() is None
    assert paths.bundled_bin_dir() is None  # em desenvolvimento o FFmpeg vem do PATH


# ---------------------------------------------------------------- §3.3 migrações
def test_migracoes_versionadas_e_idempotentes(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    assert current_version(engine) == 0
    assert migrate(engine) == MIGRATIONS[-1][0]
    assert migrate(engine) == MIGRATIONS[-1][0]  # 2ª inicialização não reaplica
    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM schema_version")).scalar() == len(MIGRATIONS)


def test_add_column_nao_apaga_dados(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'c.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, nome TEXT)"))
        conn.execute(text("INSERT INTO t (nome) VALUES ('canal')"))
        add_column(conn, "t", "extra", "TEXT DEFAULT ''")
        add_column(conn, "t", "extra", "TEXT DEFAULT ''")  # idempotente
        assert conn.execute(text("SELECT nome, extra FROM t")).one() == ("canal", "")


# ---------------------------------------------------------------- API do app
@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_rotas_sob_api(client):
    health = client.get("/api/health").json()
    assert health["ok"] and health["version"] == paths.app_version()
    assert client.get("/api/channels").status_code == 200
    assert client.get("/channels").status_code == 404  # sem prefixo não existe mais


def test_status_da_primeira_execucao_e_conclusao(client, monkeypatch):
    from app.api import system

    monkeypatch.setattr(system, "latest_release", lambda: None)
    st = client.get("/api/setup/status").json()
    assert set(st["keys"]) == set(system.KEY_PROVIDERS) and "model" in st and "google" in st
    assert client.post("/api/setup/done").json()["ok"]
    assert client.get("/api/setup/status").json()["needs_setup"] is False


def test_aviso_de_versao_nova(client, monkeypatch):
    from app.api import system

    monkeypatch.setattr(system, "latest_release", lambda: {"version": "99.0.0", "url": "https://x/setup.exe",
                                                            "notes": "novidades"})
    info = client.get("/api/app/info").json()
    assert info["update"] == {"version": "99.0.0", "url": "https://x/setup.exe", "notes": "novidades"}
    monkeypatch.setattr(system, "latest_release", lambda: {"version": paths.app_version()})
    assert client.get("/api/app/info").json()["update"] is None


def test_mostrar_janela_chama_o_gancho_do_launcher(client, monkeypatch):
    from app import desktop

    calls = []
    monkeypatch.setattr(desktop, "show_window", lambda: calls.append(1))
    assert client.post("/api/app/show").json()["ok"] and calls == [1]
    monkeypatch.setattr(desktop, "show_window", None)
    assert client.post("/api/app/show").json()["ok"] is False


def test_criacao_bloqueada_sem_modelo_no_app_instalado(client, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "FROZEN", True)
    monkeypatch.setenv("AIEDITOR_DATA", str(tmp_path))
    r = client.post("/api/productions", data={"channel_id": "1", "title": "t", "script": "texto"})
    assert r.status_code == 409 and "modelo de transcrição" in r.json()["detail"]


# ---------------------------------------------------------------- §2 frontend estático com fallback
def test_frontend_estatico_com_fallback_para_rotas_do_app(tmp_path):
    from app.main import SpaStaticFiles

    (tmp_path / "index.html").write_text("<html>AiEditor</html>", encoding="utf-8")
    (tmp_path / "canais").mkdir()
    (tmp_path / "canais" / "index.html").write_text("<html>Canais</html>", encoding="utf-8")
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"ok": True}

    app.mount("/", SpaStaticFiles(directory=tmp_path, html=True))
    c = TestClient(app)
    assert c.get("/api/health").json() == {"ok": True}  # API antes dos estáticos
    assert "Canais" in c.get("/canais/").text
    assert "AiEditor" in c.get("/rota/do/app").text  # fallback para o index.html
    assert c.get("/sem-isto.js").status_code == 404  # arquivo inexistente não vira página
    assert c.get("/api/inexistente").status_code == 404


# ---------------------------------------------------------------- §4 OAuth com porta dinâmica
def test_redirect_do_google_segue_a_porta_do_app(monkeypatch):
    from app.providers.storage import gdrive

    fake = {"installed": {"client_id": "x.apps.googleusercontent.com", "client_secret": "s",
                          "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                          "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}}
    monkeypatch.setattr(gdrive, "_client_config", lambda: fake)
    url = gdrive.start_auth("http://127.0.0.1:53127/")
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A53127%2Fapi%2Fauth%2Fgoogle%2Fcallback" in url


def test_client_oauth_embutido_quando_nao_ha_no_keyring(monkeypatch, tmp_path):
    from app.providers.storage import gdrive

    (tmp_path / "resources").mkdir()
    (tmp_path / "resources" / "google_oauth_client.json").write_text('{"installed": {}}', encoding="utf-8")
    monkeypatch.setattr(paths, "resource_dir", lambda: tmp_path)
    monkeypatch.setattr(gdrive, "get_secret", lambda p: None)
    assert gdrive.client_available() and gdrive._client_config() == {"installed": {}}


# ---------------------------------------------------------------- §3.4 modelo de transcrição
def test_modelo_local_e_bloqueio_no_app_instalado(monkeypatch, tmp_path):
    monkeypatch.setenv("AIEDITOR_DATA", str(tmp_path))
    assert whisper_models.model_ready("small") and whisper_models.model_source("small") == "small"  # dev: cache HF
    monkeypatch.setattr(paths, "FROZEN", True)
    assert not whisper_models.model_ready("small")
    with pytest.raises(FileNotFoundError):
        whisper_models.model_source("small")
    d = whisper_models.model_dir("small")
    d.mkdir(parents=True)
    (d / "model.bin").write_bytes(b"x")
    (d / "config.json").write_text("{}")
    assert whisper_models.model_ready("small") and whisper_models.model_source("small") == str(d)


# ---------------------------------------------------------------- §3.2 launcher
def test_launcher_porta_local_e_comando_do_worker():
    from app import launcher

    port = launcher.free_port()
    assert 1024 < port < 65536
    cmd = launcher.Worker().command()
    assert cmd[-3:-1] == ["--worker", "--parent-pid"] and "app.launcher" in cmd


def test_spec_e_instalador_versionados():
    root = paths.REPO_ROOT
    spec = (root / "aieditor.spec").read_text(encoding="utf-8")
    assert "console=False" in spec and "upx=False" in spec and "exclude_binaries=True" in spec  # onedir
    iss = (root / "installer" / "aieditor.iss").read_text(encoding="utf-8-sig")
    assert "PrivilegesRequired=lowest" in iss and "{localappdata}\\Programs" in iss
    assert "BrazilianPortuguese" in iss and "DelTree" in iss and "NeedsWebView2" in iss
    assert Path(root / "build.ps1").exists()
