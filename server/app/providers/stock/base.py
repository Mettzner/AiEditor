"""Interface comum dos bancos de vídeo/foto e do YouTube."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol


@dataclass
class Rendition:
    url: str
    width: int
    height: int


@dataclass
class Candidate:
    provider: str  # pexels | pixabay | youtube
    external_id: str
    title: str  # texto descritivo disponível (tags, slug, título)
    duration: float  # 0 para fotos
    width: int
    height: int
    page_url: str
    thumbnail: str | None
    renditions: list[Rendition] = field(default_factory=list)
    query: str = ""
    score: float = 0.0
    author: str = ""  # crédito pedido pelos bancos / atribuição CC-BY
    author_url: str = ""
    license: str = ""
    category: str = ""  # YouTube: snippet.categoryId
    is_image: bool = False
    penalty: float = 1.0  # multiplicador da nota de texto (estilo não preferido pela cena)
    # Frames grátis em ordem temporal (sem baixar o vídeo) e sua posição relativa no clipe (0–1).
    preview_frames: list[str] = field(default_factory=list)
    frame_positions: list[float] = field(default_factory=list)
    # metadados de procedência/diversidade (YouTube); vazios nos bancos e nos caches antigos
    published_at: str = ""
    channel_id: str = ""
    description: str = ""
    # descrição observada por uma IA de visão em outra avaliação (reaproveitável, nunca vale como aprovação)
    observed: str = ""

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.external_id}"

    @property
    def source(self) -> str:
        if self.provider in ("youtube", "authorized_youtube"):
            return "youtube"
        return "stock_photo" if self.is_image else "stock"

    def positions(self) -> list[float]:
        n = len(self.preview_frames)
        if len(self.frame_positions) == n:
            return self.frame_positions
        return [(i + 0.5) / n for i in range(n)]

    def best_rendition(self, target_height: int = 1080) -> Rendition | None:
        """A menor rendição ≥ 1080p (nunca 4K se houver 1080p); senão, a maior disponível."""
        if not self.renditions:
            return None
        ok = sorted((r for r in self.renditions if r.height >= target_height), key=lambda r: r.height)
        if ok:
            return ok[0]
        return max(self.renditions, key=lambda r: r.height)

    def lightest_rendition(self) -> Rendition | None:
        """Versão leve (~360p) para extrair frames no desempate."""
        usable = [r for r in self.renditions if r.height >= 240]
        return min(usable or self.renditions, key=lambda r: r.height, default=None)

    def to_dict(self) -> dict:
        return asdict(self)


def candidate_from_dict(d: dict) -> Candidate:
    d = dict(d)
    d["renditions"] = [Rendition(**r) for r in d.get("renditions", [])]
    known = set(Candidate.__dataclass_fields__)
    return Candidate(**{k: v for k, v in d.items() if k in known})


class StockProvider(Protocol):
    id: str

    def search(self, query: str, per_page: int = 15, lang: str = "en") -> list[Candidate]: ...

    def search_photos(self, query: str, per_page: int = 15, lang: str = "en") -> list[Candidate]: ...

    def test(self) -> dict: ...
