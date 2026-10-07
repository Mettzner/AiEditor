"""Tabelas SQLite (§14)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now(timezone.utc)


class Channel(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    preset: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=now)


class Production(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    channel_id: Optional[int] = Field(default=None, foreign_key="channel.id", index=True)
    title: str
    script: str
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # queued | running | done | failed | cancel_requested | cancelled
    status: str = Field(default="queued", index=True)
    step: Optional[str] = None
    step_label: Optional[str] = None
    progress: float = 0.0
    drive_url: Optional[str] = None
    output_path: Optional[str] = None
    duration_seconds: Optional[float] = None
    cost_estimated: Optional[float] = None
    cost_actual: float = 0.0
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=now, index=True)


class ProductionStep(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    production_id: int = Field(foreign_key="production.id", index=True)
    step: str
    status: str = "pending"  # pending | running | done | skipped | failed
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    output: Optional[str] = None


class Issue(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    production_id: int = Field(foreign_key="production.id", index=True)
    code: str
    severity: str  # info | warning | error
    scene: Optional[str] = None
    message: str
    detail: Optional[str] = None
    created_at: datetime = Field(default_factory=now)


class UsedAsset(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    channel_id: Optional[int] = Field(default=None, index=True)
    production_id: int = Field(index=True)
    source: str
    external_id: str = Field(index=True)
    perceptual_hash: Optional[str] = None
    segment_start: Optional[float] = None
    created_at: datetime = Field(default_factory=now)


class ProviderPrice(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str
    unit: str  # ex.: "per_1m_input_tokens", "per_image", "per_second_video"
    price: float
    note: Optional[str] = None


class SearchCache(SQLModel, table=True):
    """Resultados de busca por provedor + query normalizada + quantidade (validade por provedor em settings).

    status: ok (com resultados) | empty (busca real sem resultado, cacheável) | error (falha transitória, TTL curto).
    """

    key: str = Field(primary_key=True)
    provider: str
    query: str
    results: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=now)
    status: str = "ok"
    expires_at: Optional[datetime] = None
    error: Optional[str] = None
    next_page_token: Optional[str] = None


class CacheLease(SQLModel, table=True):
    """Single-flight entre processos: quem segura a chave busca; os outros esperam o resultado no cache."""

    key: str = Field(primary_key=True)
    owner: str
    expires_at: float  # time.time()


class VisionCache(SQLModel, table=True):
    """Notas da IA de visão: hash(etapa + candidatos + visual_intent + estilo + modelo)."""

    key: str = Field(primary_key=True)
    stage: str
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=now)


class AssetDescription(SQLModel, table=True):
    """O que uma IA de visão VIU num asset (só conteúdo observável), reaproveitável entre cenas.

    Não guarda adequação a cena nenhuma: subject_visible, context_match e nota ficam no vision_cache, por pedido.
    ref: hash dos bytes (imagem local) ou de candidato + URLs dos frames; content_hash: bytes que a IA viu.
    """

    ref: str = Field(primary_key=True)
    candidate_key: str = Field(index=True)
    content_hash: Optional[str] = None
    seen: str = ""
    realism: str = ""
    brand_or_franchise: bool = False
    is_timeless: bool = False
    provider: str = ""
    prompt_version: str = ""
    created_at: datetime = Field(default_factory=now)


class YtQuota(SQLModel, table=True):
    """LEGADO (até a migração 002): unidades da YouTube Data API num saldo único por dia. Mantida só para leitura
    histórica; o controle atual fica em QuotaUsage, por bucket."""

    day: str = Field(primary_key=True)  # AAAA-MM-DD no horário do Pacífico
    used: int = 0
    exhausted: bool = False
    updated_at: datetime = Field(default_factory=now)


class QuotaUsage(SQLModel, table=True):
    """Cota de API por provedor, bucket e dia (YouTube: fuso do Pacífico). Reserva atômica no SQLite.

    unit: "calls" (ex.: search.list, bucket próprio) ou "units" (demais endpoints). `attempts` conta pedidos
    realmente enviados (inclui retentativas); `uncertain` os que falharam sem resposta (contados por precaução).
    """

    id: str = Field(primary_key=True)  # "<provider>:<bucket>:<day>"
    provider: str = Field(index=True)
    bucket: str
    day: str = Field(index=True)
    unit: str = "units"
    used: int = 0
    attempts: int = 0
    uncertain: int = 0
    exhausted: bool = False
    blocked_until: Optional[float] = None  # time.time(); limite por minuto
    block_reason: Optional[str] = None
    origin: Optional[str] = None  # "migrated_estimate" quando veio do saldo único legado
    updated_at: datetime = Field(default_factory=now)


class LlmCache(SQLModel, table=True):
    """Respostas do LLM: hash(etapa + modelo + versão do prompt + entrada). Retry não paga de novo."""

    key: str = Field(primary_key=True)
    task: str
    model: str
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=now)
