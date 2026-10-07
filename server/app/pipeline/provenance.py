"""Manifesto de procedência e créditos prontos para publicação (Fase D5).

output/manifest.json, uma linha por cena com asset: URL, autor, licença declarada, data de conferência, forma de
obtenção (API oficial, arquivo autorizado, yt-dlp, geração por IA, gráfico), hash do arquivo, original, trecho
usado, adaptações, papel visual e estado de validação. Mais: referências do YouTube pesquisadas e NÃO
incorporadas, e as afirmações do roteiro que pedem fonte (pendentes: o app não inventa link, citação nem licença).

Os créditos (creditos.txt) saem do manifesto: atribuições exigidas, aviso de imagens geradas por IA (ilustrativas,
nunca registro real), referências consultadas e pendências.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .report import config_min_score, scene_validation

PROVIDER_NAMES = {"pexels": "Pexels", "pixabay": "Pixabay", "wikimedia": "Wikimedia Commons",
                  "loc": "Library of Congress", "internet_archive": "Internet Archive", "youtube": "YouTube",
                  "authorized_youtube": "YouTube (arquivo autorizado)", "darkvi": "Darkvi (IA)",
                  "aieditor": "AiEditor"}


def _load(job: Path, name: str) -> Any:
    p = job / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def _obtained(entry: dict) -> str:
    if entry.get("obtained_how"):
        return entry["obtained_how"]
    src = entry.get("source")
    if src == "ai_image":
        return "imagem gerada por IA (Darkvi): ilustração, não é registro real"
    if src == "chart":
        return f"gráfico gerado pelo AiEditor a partir de: {entry.get('data_source')}"
    if entry.get("provider") == "youtube":  # produções antigas (antes do registro): download por yt-dlp
        return "yt-dlp (download fora dos recursos do YouTube; registrado antes do controle de procedência)"
    return f"API do provedor {entry.get('provider')}" if entry.get("provider") else "desconhecida"


def _adaptations(entry: dict, rendered: dict | None) -> list[str]:
    out = []
    if entry.get("is_image"):
        out.append("recorte 16:9")
    elif entry.get("source") not in ("chart",):
        out.append("sem áudio original")
    if rendered:
        motion = (rendered.get("motion") or {}).get("type")
        if motion:
            out.append(f"movimento de câmera ({motion})")
        if rendered.get("grade"):
            out.append(f"correção de cor ({rendered['grade']})")
    if entry.get("in_point") is not None and entry.get("out_point"):
        out.append(f"trecho {entry['in_point']:.2f}–{entry['out_point']:.2f}s do original")
    return out


def build_manifest(job: Path) -> dict | None:
    plan = _load(job, "plan.json")
    if not plan:
        return None
    selection = _load(job, "selection.json") or {}
    timeline = _load(job, "timeline.json") or {}
    bible = _load(job, "context_bible.json") or {}
    refs = (_load(job, "references.json") or {}).get("scenes") or {}
    rendered = {s["id"]: s for s in timeline.get("scenes", [])}
    min_score = config_min_score(job)
    claims = {c["id"]: c for c in bible.get("claims") or []}
    assets, pending_claims = [], []
    for s in plan["scenes"]:
        e = selection.get(s["id"]) or {}
        for cid in s.get("claim_ids") or []:
            c = claims.get(cid)
            if c and c.get("needs_source"):
                pending_claims.append({"claim_id": cid, "scene": s["id"], "quote": c.get("quote"),
                                       "source": None, "status": "pendente: sem fonte primária verificada"})
        if not e.get("asset"):
            continue
        assets.append({
            "scene": s["id"], "time": [s["start"], s["end"]], "file": e["asset"],
            "sha256": _sha256(job / e["asset"]), "source": e.get("source_used") or e.get("source"),
            "provider": PROVIDER_NAMES.get(e.get("provider", ""), e.get("provider")),
            "original": e.get("page_url") or e.get("prompt") or e.get("data_source"),
            "title": (e.get("title") or "")[:200] or None, "author": e.get("author") or None,
            "author_url": e.get("author_url") or None, "license": e.get("license") or None,
            "license_note": ("licença declarada pelo autor na plataforma; não verificada pelo AiEditor"
                             if e.get("license") and e.get("source") != "chart" else None),
            "obtained_how": _obtained(e), "checked_at": e.get("retrieved_at"),
            "segment": [e.get("in_point"), e.get("out_point")] if e.get("out_point") else None,
            "segment_check": (e.get("segment_check") or {}).get("status"),
            "adaptations": _adaptations(e, rendered.get(s["id"])),
            "visual_role": s.get("visual_role") or "contextual_illustration",
            "validation": scene_validation(e, s, min_score)["status"],
            "ai_generated": e.get("source") == "ai_image",
        })
    references = [{"scene": sid, **r} for sid, items in refs.items() for r in items
                  if r.get("status") != "authorized_available"]
    return {"assets": assets, "references_not_used": references, "claims_pending_source": pending_claims,
            "summary": {"assets": len(assets), "ai_generated": sum(1 for a in assets if a["ai_generated"]),
                        "unique_sources": len({a["original"] for a in assets if a["original"]}),
                        "references": len(references), "claims_pending": len(pending_claims)}}


def credits_text(manifest: dict | None, sounds: list[dict] | None = None) -> str | None:
    """Texto pronto para a descrição do vídeo, a partir do manifesto."""
    if not manifest:
        return None
    a = manifest["assets"]
    lines: list[str] = []
    if sounds:
        lines += ["Efeitos sonoros (Freesound):", ""]
        lines += [f"- {e.get('author') or 'autor desconhecido'} — {e.get('license', '')} — {e.get('page_url', '')}"
                  for e in sounds]
        lines.append("")
    attributed = [x for x in a if x["source"] in ("youtube",) or (x["provider"] or "") in
                  ("Wikimedia Commons", "Library of Congress", "Internet Archive")]
    if attributed:
        lines += ["Imagens de terceiros (atribuição):", ""]
        lines += [f"- {x['title'] or 'sem título'} — {x['author'] or 'autor desconhecido'} — {x['license'] or ''} — "
                  f"{x['original'] or ''}" for x in attributed]
        lines.append("")
    stock = [x for x in a if x["source"] in ("stock", "stock_photo") and x not in attributed]
    if stock:
        by_provider: dict[str, set[str]] = {}
        for x in stock:
            by_provider.setdefault(x["provider"] or "banco", set()).add(x["author"] or "autor desconhecido")
        lines.append("Vídeos e fotos de banco:")
        lines += [f"- {prov}: {', '.join(sorted(authors))}" for prov, authors in sorted(by_provider.items())]
        lines.append("")
        lines.append("Detalhe por clipe:")
        lines += [f"- {x['author'] or ''} ({x['provider']}) — {x['original'] or ''}" for x in stock]
        lines.append("")
    charts = [x for x in a if x["source"] == "chart"]
    if charts:
        lines += ["Gráficos (dados citados):", ""] + [f"- {x['original']}" for x in charts] + [""]
    if manifest["summary"]["ai_generated"]:
        lines += [f"Este vídeo contém {manifest['summary']['ai_generated']} imagem(ns) gerada(s) por IA, usadas como "
                  "ilustração; não são registros reais dos fatos narrados.", ""]
    if manifest["claims_pending_source"]:
        lines += ["Afirmações do roteiro ainda sem fonte verificada (conferir antes de publicar):", ""]
        lines += [f"- \"{c['quote']}\"" for c in manifest["claims_pending_source"][:30]]
        lines.append("")
    return ("\n".join(lines).strip() + "\n") if lines else None


def write_manifest(ctx) -> Path | None:
    manifest = build_manifest(ctx.dir)
    if manifest is None:
        return None
    return ctx.write_json("output/manifest.json", manifest)
