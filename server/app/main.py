"""API do AiEditor.  Uso: uvicorn app.main:app --port 8000

Todas as rotas ficam sob /api. No app instalado, o mesmo servidor também entrega o frontend exportado (estático)
na mesma origem; em desenvolvimento o frontend roda no `next dev` e chama http://localhost:8000/api.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import paths
from .api import channels, productions, settings, system
from .db import init_db


class SpaStaticFiles(StaticFiles):
    """Arquivos do export do Next; rota do app sem arquivo correspondente cai no index.html (fallback)."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code != 404 or path.startswith("api/") or "." in path.rsplit("/", 1)[-1]:
                raise
            return await super().get_response("index.html", scope)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="AiEditor", version="0.1.0", lifespan=lifespan)
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
