"""Interface comum dos bancos de vídeo."""
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
    provider: str
    external_id: str
    title: str  # texto descritivo disponível (tags, slug, título)
    duration: float
    width: int
    height: int
    page_url: str
    thumbnail: str | None
    renditions: list[Rendition] = field(default_factory=list)
    query: str = ""
    score: float = 0.0
    author: str = ""  # crédito pedido pelos bancos (creditos.txt)
    author_url: str = ""
    preview_frames: list[str] = field(default_factory=list)  # quadros de prévia (pré-ranking visual, Fase 2)

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.external_id}"

    def best_rendition(self, target_height: int = 1080) -> Rendition | None:
        """A menor rendição ≥ 1080p (nunca 4K se houver 1080p); senão, a maior disponível."""
        if not self.renditions:
            return None
        ok = sorted((r for r in self.renditions if r.height >= target_height), key=lambda r: r.height)
        if ok:
            return ok[0]
        return max(self.renditions, key=lambda r: r.height)

    def to_dict(self) -> dict:
        return asdict(self)


class StockProvider(Protocol):
    id: str

    def search(self, query: str, per_page: int = 15, lang: str = "en") -> list[Candidate]: ...

    def test(self) -> dict: ...
