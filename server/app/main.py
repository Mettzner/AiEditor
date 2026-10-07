"""API do AiEditor.  Uso: uvicorn app.main:app --port 8000

Todas as rotas ficam sob /api. No app instalado, o mesmo servidor também entrega o frontend exportado (estático)
na mesma origem; em desenvolvimento o frontend roda no `next dev` e chama http://localhost:8000/api.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import paths
from .api import channels, productions, settings, system
from .db import init_db, session_scope
from .purge import purge_orphans


class SpaStaticFiles(StaticFiles):
    """Arquivos do export do Next; rota do app sem arquivo correspondente cai no index.html (fallback)."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            norm = path.replace("\\", "/")  # no Windows o Starlette entrega o caminho com barras invertidas
            if e.status_code != 404 or norm.startswith("api/") or "." in norm.rsplit("/", 1)[-1]:
                raise
            return await super().get_response("index.html", scope)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with session_scope() as s:  # sobras de exclusões antigas, que apagavam só o registro
        purge_orphans(s)
    yield


app = FastAPI(title="AiEditor", version=paths.app_version(), lifespan=lifespan)

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1", "testserver"}  # testserver: cliente de testes
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _host_only(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("["):
        return value.split("]")[0] + "]"
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


@app.middleware("http")
async def local_only(request: Request, call_next):
    """A API só atende o próprio computador: Host precisa ser loopback (barra DNS rebinding) e, em mutações, a
    origem do navegador (se houver) também. Requisições sem Origin (scripts locais, o próprio app) passam."""
    if request.url.path.startswith("/api"):
        if _host_only(request.headers.get("host", "")) not in LOOPBACK_HOSTS:
            return JSONResponse({"detail": "Host não permitido"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method in MUTATING and origin and origin != "null":
            if _host_only(urlsplit(origin).netloc) not in LOOPBACK_HOSTS:
                return JSONResponse({"detail": "Origem não permitida"}, status_code=403)
    return await call_next(request)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)
for module in (system, channels, productions, settings):
    app.include_router(module.router, prefix="/api")

# frontend estático DEPOIS das rotas da API (app instalado, ou AIEDITOR_SERVE_WEB=1 com web/out)
_web = paths.web_dir()
if _web:
    app.mount("/", SpaStaticFiles(directory=_web, html=True), name="web")
