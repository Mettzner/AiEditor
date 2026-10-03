"""Etapa 8: upload para o Google Drive (§12)."""
from __future__ import annotations

from datetime import datetime

from ..db import session_scope
from ..models import Production, now
from ..providers.storage import gdrive
from ..worker.context import JobContext, StepError
from .render import ffmpeg
from .report import write_report, write_visual_report


def _credits(ctx: JobContext) -> str | None:
    """creditos.txt: atribuição CC-BY do YouTube (obrigatória) e dos bancos (pedida pelo Pexels/Pixabay)."""
    if not (ctx.dir / "selection.json").exists():
        return None
    from ..providers.stock.archives import ARCHIVE_PROVIDERS

    sel = ctx.read_json("selection.json")
    yt = [v for v in sel.values() if v.get("source") == "youtube"]
    archive = [v for v in sel.values() if v.get("provider") in ARCHIVE_PROVIDERS and v.get("asset")]
    stock = [v for v in sel.values() if v.get("source") == "stock" and v.get("provider") not in ARCHIVE_PROVIDERS]
    if not yt and not stock and not archive:
        return None
    lines: list[str] = []
    if archive:  # acervos históricos: autor, licença e página de cada item (CC BY exige atribuição)
        names = {"wikimedia": "Wikimedia Commons", "loc": "Library of Congress", "internet_archive": "Internet Archive"}
        lines += ["Acervos históricos:", ""]
        lines += [f"- {v.get('title', '')[:120]} — {v.get('author') or 'autor desconhecido'} — {v.get('license', '')} "
                  f"— {names.get(v.get('provider', ''), v.get('provider', ''))} — {v.get('page_url', '')}" for v in archive]
        lines.append("")
    if yt:
        lines += ["Créditos (Creative Commons BY):", ""]
        lines += [f"- {v.get('title', '')} — {v.get('channel', '')} — {v.get('page_url', '')}" for v in yt]
        lines.append("")
    if stock:
        names = {"pexels": "Pexels", "pixabay": "Pixabay"}
        by_provider: dict[str, set[str]] = {}
        for v in stock:
            by_provider.setdefault(names.get(v.get("provider", ""), v.get("provider", "")), set()).add(
                v.get("author") or "autor desconhecido")
        lines.append("Vídeos de banco:")
        lines += [f"- {prov}: {', '.join(sorted(authors))}" for prov, authors in sorted(by_provider.items())]
        lines.append("")
        lines.append("Detalhe por clipe:")
        lines += [f"- {v.get('author', '')} ({names.get(v.get('provider', ''), '')}) — {v.get('page_url', '')}"
                  for v in stock]
    return "\n".join(lines) + "\n"


def run(ctx: JobContext) -> str:
    video = ctx.dir / "output" / "final.mp4"
    duration = ffmpeg.duration(video)
    with session_scope() as s:
        p = s.get(Production, ctx.production_id)
        assert p
        p.output_path = str(video)
        p.duration_seconds = duration
        p.updated_at = now()
        s.add(p)
        s.commit()

    credits = _credits(ctx)
    if credits:
        ctx.path("output", "creditos.txt").write_text(credits, encoding="utf-8")
    report = write_report(ctx)  # atualizado com todos os problemas até o render
    visual = write_visual_report(ctx)

    if not gdrive.is_connected():
        ctx.issue("DRIVE_NOT_CONFIGURED", f"Conta Google não conectada; vídeo salvo em {video}")
        return str(video)

    try:
        folder = ctx.config.drive_folder or "/AiEditor"
        if ctx.config.drive_subfolder_per_production:
            folder = f"{folder.rstrip('/')}/{datetime.now():%Y-%m-%d} {ctx.config.title}"[:250]
        folder_id = gdrive.ensure_folder(folder)
        res = gdrive.upload(video, folder_id, "video/mp4",
                            on_progress=lambda f: ctx.progress(f * 0.95, f"Enviando ao Drive {f:.0%}"))
        if credits:
            gdrive.upload(ctx.dir / "output" / "creditos.txt", folder_id, "text/plain")
        if report:
            gdrive.upload(report, folder_id, "application/json")
        gdrive.upload(visual, folder_id, "application/json")
    except Exception as e:  # noqa: BLE001
        raise StepError("DRIVE_UPLOAD_FAILED", f"Upload falhou; arquivo mantido em {video}", repr(e)) from e

    with session_scope() as s:
        p = s.get(Production, ctx.production_id)
        assert p
        p.drive_url = res.get("webViewLink")
        s.add(p)
        s.commit()
    return res.get("webViewLink") or str(video)
