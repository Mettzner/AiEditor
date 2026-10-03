"""Google Drive: OAuth (refresh token no keyring) e upload resumable (§12)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from ...config import get_secret, set_secret

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
# Client do tipo "App para computador": o Google aceita redirect de loopback em qualquer porta, então o endereço
# segue a porta em que o app está rodando (8000 no desenvolvimento, porta livre no app instalado).
CALLBACK_PATH = "api/auth/google/callback"
FOLDER_MIME = "application/vnd.google-apps.folder"

_pending_flows: dict[str, Flow] = {}


class DriveNotConfigured(RuntimeError):
    pass


def _embedded_client() -> str | None:
    """Client OAuth "App para computador" do projeto AiEditor embutido no instalador (§4). No fluxo para apps
    instalados o Google não trata o client_secret como segredo; cada usuário entra com a própria conta."""
    from ... import paths

    f = paths.resource_dir() / "resources" / "google_oauth_client.json"
    return f.read_text(encoding="utf-8") if f.exists() else None


def _client_config() -> dict:
    raw = get_secret("google_oauth_client") or _embedded_client()
    if not raw:
        raise DriveNotConfigured("Cole o JSON do client OAuth do Google na Configuração")
    return json.loads(raw)


def client_available() -> bool:
    return bool(get_secret("google_oauth_client") or _embedded_client())


def start_auth(base_url: str = "http://localhost:8000/") -> str:
    redirect_uri = base_url.rstrip("/") + "/" + CALLBACK_PATH
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=redirect_uri)
    url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    _pending_flows[state] = flow
    return url


def finish_auth(state: str, code: str) -> None:
    flow = _pending_flows.pop(state, None)
    if flow is None:
        raise RuntimeError("Fluxo OAuth expirado; comece de novo na Configuração")
    flow.fetch_token(code=code)
    creds = flow.credentials
    if not creds.refresh_token:
        raise RuntimeError("O Google não devolveu refresh token; revogue o acesso e tente de novo")
    set_secret("google_refresh_token", creds.refresh_token)


def _credentials() -> Credentials:
    refresh = get_secret("google_refresh_token")
    if not refresh:
        raise DriveNotConfigured("Conta Google não conectada")
    cfg = _client_config()
    section = cfg.get("installed") or cfg.get("web") or {}
    creds = Credentials(None, refresh_token=refresh, token_uri=section.get("token_uri", "https://oauth2.googleapis.com/token"),
                        client_id=section.get("client_id"), client_secret=section.get("client_secret"), scopes=SCOPES)
    creds.refresh(Request())
    return creds


def is_connected() -> bool:
    return bool(get_secret("google_refresh_token") and client_available())


def _service():
    return build("drive", "v3", credentials=_credentials(), cache_discovery=False)


def ensure_folder(path: str) -> str:
    """Cria (se preciso) cada segmento de '/Canais/X' e devolve o id da pasta final."""
    svc = _service()
    parent = "root"
    for name in [p for p in path.replace("\\", "/").split("/") if p]:
        safe = name.replace("'", "\\'")
        q = f"name = '{safe}' and mimeType = '{FOLDER_MIME}' and '{parent}' in parents and trashed = false"
        found = svc.files().list(q=q, fields="files(id)", pageSize=1).execute().get("files", [])
        if found:
            parent = found[0]["id"]
        else:
            parent = svc.files().create(body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]},
                                        fields="id").execute()["id"]
    return parent


def upload(file: Path, folder_id: str, mime: str, on_progress: Callable[[float], None] | None = None) -> dict:
    svc = _service()
    media = MediaFileUpload(str(file), mimetype=mime, resumable=True, chunksize=16 * 1024 * 1024)
    req = svc.files().create(body={"name": file.name, "parents": [folder_id]}, media_body=media,
                             fields="id, webViewLink")
    response = None
    while response is None:
        try:
            status, response = req.next_chunk(num_retries=5)
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504):
                continue
            raise
        if status and on_progress:
            on_progress(status.progress())
    return response


def test() -> dict:
    about = _service().about().get(fields="user(emailAddress)").execute()
    return {"ok": True, "detail": about["user"]["emailAddress"]}
