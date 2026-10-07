"""Etapa 8: upload para o Google Drive (§12)."""
from __future__ import annotations

from datetime import datetime

from ..db import session_scope
from ..models import Production, now
from ..providers.storage import gdrive
from ..worker.context import JobContext, StepError
from .render import ffmpeg
from .provenance import build_manifest, credits_text, write_manifest
from .report import write_report, write_visual_report


def _credits(ctx: JobContext) -> str | None:
    """creditos.txt a partir do manifesto de procedência: atribuições (YouTube, acervos, bancos, Freesound),
    gráficos com a fonte dos dados, aviso de imagens geradas por IA e afirmações sem fonte verificada."""
    timeline = ctx.read_json("timeline.json") if (ctx.dir / "timeline.json").exists() else {}
    sounds = [e for e in (timeline.get("audio") or {}).get("sfx") or [] if e.get("source") == "freesound"]
    unique = list({e["file"]: e for e in sounds}.values())
    return credits_text(build_manifest(ctx.dir), unique)


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
    manifest = write_manifest(ctx)
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
        if manifest:
            gdrive.upload(manifest, folder_id, "application/json")
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
