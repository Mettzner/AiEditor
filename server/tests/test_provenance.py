"""Fase D: YouTube como referência, mídia autorizada, índice temporal, uso por segmento, conferência do trecho
final e manifesto de procedência. Os testes com FFmpeg geram vídeos sintéticos locais (sem rede)."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from sqlmodel import delete

from app.config import update_settings
from app.db import session_scope
from app.models import AuthorizedMedia
from app.pipeline import media_index
from app.pipeline import select as sel_mod
from app.pipeline.provenance import build_manifest, credits_text
from app.pipeline.render import ffmpeg
from app.pipeline.select.segments import SegmentLedger, planned_interval
from app.providers.stock.base import Candidate
from app.providers.youtube import client as yt

from .test_selector import env, issues, make_ctx, row, scene  # noqa: F401  (fixture env)


def _has_ffmpeg() -> bool:
    try:
        return bool(ffmpeg.ffmpeg_bin()) and bool(ffmpeg.ffprobe_bin())
    except Exception:  # noqa: BLE001
        return False


needs_ffmpeg = pytest.mark.skipif(not _has_ffmpeg(), reason="FFmpeg não instalado neste ambiente")


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    """30 s, 1280×720, com troca de plano nítida em 10 s e 20 s (preto, branco, azul), sem áudio."""
    if not _has_ffmpeg():
        pytest.skip("FFmpeg não instalado")
    out = tmp_path_factory.mktemp("vid") / "fixture.mp4"
    filt = ("color=c=black:s=1280x720:d=10[a];color=c=white:s=1280x720:d=10[b];color=c=blue:s=1280x720:d=10[c];"
            "[a][b][c]concat=n=3:v=1:a=0,format=yuv420p")
    subprocess.run([ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-filter_complex", filt,
                    "-r", "25", "-c:v", "libx264", "-preset", "ultrafast", str(out)], check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return out


# ---------------------------------------------------------------- D2/D3 com FFmpeg real
@needs_ffmpeg
def test_indice_temporal_com_cortes_medidos(video):
    idx = media_index.index_file(video)
    assert idx["width"] == 1280 and idx["height"] == 720 and abs(idx["duration"] - 30) < 0.2
    assert idx["method"] == "scene_cuts"
    starts = [round(s["start"]) for s in idx["segments"]]
    assert starts == [0, 10, 20]
    assert all(s["start"] <= s["frame_t"] <= s["end"] for s in idx["segments"])
    assert media_index.index_file(video) == idx  # reaproveitado pelo hash


@needs_ffmpeg
def test_corte_do_trecho_confere_duracao_e_resolucao(video, tmp_path):
    meta = media_index.cut_segment(video, 12.0, 15.0, tmp_path / "s.mp4")
    assert abs(meta["duration"] - 3.0) < 0.2 and (meta["width"], meta["height"]) == (1280, 720)
    frames = media_index.frames_at(tmp_path / "s.mp4", media_index.segment_times(0, 3, 3), "chk")
    assert len(frames) == 3 and all(p.exists() for _, p in frames)
    near = media_index.refine_around(video, 15.0, span=2, step=1)
    assert [t for t, _ in near] == [13.0, 14.0, 15.0, 16.0, 17.0]


# ---------------------------------------------------------------- D4: uso por segmento
def test_video_longo_da_trechos_distintos_sem_sobreposicao():
    led = SegmentLedger(max_per_video=2, max_per_channel=4, min_gap=30)
    long = SegmentLedger.is_long(600, 5)
    assert led.claim("youtube:x", 100, 105, long, "canal")
    assert not led.claim("youtube:x", 110, 115, long, "canal")  # perto demais (intervalo mínimo)
    assert led.claim("youtube:x", 300, 305, long, "canal")
    assert not led.claim("youtube:x", 500, 505, long, "canal")  # limite por vídeo
    assert led.stats() == {"unique_assets": 1, "segments": 2, "reused_assets": 1}
    short = SegmentLedger()
    assert short.claim("pexels:1", 0, 5, False) and not short.claim("pexels:1", 10, 15, False)


def test_limite_por_canal():
    led = SegmentLedger(max_per_video=2, max_per_channel=2, min_gap=0)
    assert led.claim("youtube:a", 0, 5, True, "c") and led.claim("youtube:b", 0, 5, True, "c")
    assert not led.claim("youtube:z", 0, 5, True, "c")


def test_intervalo_planejado_igual_ao_do_download():
    assert planned_interval(600, 0.5, 6, False) == (297.0, 303.0)
    assert planned_interval(4, 0.5, 6, False) == (0.0, 6.0)


# ---------------------------------------------------------------- D1: modo referência e mídia autorizada
def ytc(i: int, title: str = "dark country road night", dur: float = 600.0) -> Candidate:
    return Candidate(provider="youtube", external_id=f"vid{i:08d}", title=title, duration=dur, width=1280,
                     height=720, page_url=f"https://www.youtube.com/watch?v=vid{i:08d}", thumbnail=None,
                     author=f"canal{i}", license="Creative Commons BY (YouTube)",
                     preview_frames=[f"https://i/{i}/{k}.jpg" for k in (1, 2, 3)], frame_positions=[.25, .5, .75])


def test_modo_referencia_registra_e_nao_baixa(env):
    update_settings({"youtube": {"ingest_mode": "reference"}})
    env["monkeypatch"].setattr(yt, "search_page", lambda q, n=50, lang="en", t=None: ([ytc(i) for i in range(4)], None))
    env["monkeypatch"].setattr(yt, "download_segment", lambda *a: pytest.fail("baixou do YouTube"))
    sel = sel_mod.Selector(make_ctx())
    entry = sel.select_scene(scene(source="youtube"))
    assert entry["source_used"] == "stock"
    refs = sel.references["s001"]
    assert len(refs) == 4 and refs[0]["status"] == "reference_only" and refs[0]["pending"]
    assert refs[0]["url"].startswith("https://www.youtube.com/watch?v=")


@needs_ffmpeg
def test_referencia_com_arquivo_autorizado_entra_no_render(env, video):
    with session_scope() as s:
        s.exec(delete(AuthorizedMedia))
        s.add(AuthorizedMedia(youtube_id="vid00000001", local_path=str(video), sha256="x", rights_note="conteúdo próprio",
                              author="Eu", license="todos os direitos (autor)", duration=30, width=1280, height=720))
        s.commit()
    update_settings({"youtube": {"ingest_mode": "reference"}})
    env["monkeypatch"].setattr(yt, "search_page",
                               lambda q, n=50, lang="en", t=None: ([ytc(1, dur=30), ytc(2)], None))
    sel = sel_mod.Selector(make_ctx())
    entry = sel.select_scene(scene(source="youtube"))
    assert entry["provider"] == "authorized_youtube" and entry["source_used"] == "youtube"
    assert "conteúdo próprio" in entry["obtained_how"]
    meta = media_index.probe_video(sel.ctx.dir / entry["asset"])
    assert abs(meta["duration"] - 5.5) < 0.3 and meta["width"] == 1280  # cena de 5 s + 0,5 s de folga
    statuses = {r["youtube_id"]: r["status"] for r in sel.references["s001"]}
    assert statuses["vid00000001"] == "authorized_available" and statuses["vid00000002"] == "reference_only"


def test_download_cc_fica_registrado_como_nao_autorizado(env):
    env["monkeypatch"].setattr(yt, "search_page", lambda q, n=50, lang="en", t=None: ([ytc(1)], None))
    env["monkeypatch"].setattr(yt, "download_segment", lambda vid, a, b, dest: dest.write_bytes(b"v") or dest)
    entry = sel_mod.Selector(make_ctx()).select_scene(scene(source="youtube"))
    assert entry["source_used"] == "youtube" and "yt-dlp" in entry["obtained_how"]
    assert "não é fluxo autorizado" in entry["obtained_how"]


# ---------------------------------------------------------------- D3: trecho errado não é aprovado
def test_miniatura_certa_e_trecho_errado_nao_vira_trecho_confirmado(env, tmp_path):
    env["monkeypatch"].setattr(yt, "search_page", lambda q, n=50, lang="en", t=None: ([ytc(i) for i in range(3)], None))
    env["monkeypatch"].setattr(yt, "download_segment", lambda vid, a, b, dest: dest.write_bytes(b"v") or dest)
    fake = tmp_path / "f.jpg"
    fake.write_bytes(b"jpg")
    env["monkeypatch"].setattr(media_index, "frames_at", lambda path, times, tag="f": [(t, fake) for t in times])

    def notes(n, prompt, k):
        if k == 2:  # 1ª conferência de trecho: o assunto não aparece no intervalo usado
            return [row(1, 9.0, subject=False)]
        return [row(1, 9.0)] + [row(i, 8.0) for i in range(2, n + 1)]

    env["vision"].mode = notes
    ctx = make_ctx()
    exact = {**scene(source="youtube"), "visual_role": "exact_evidence", "required_identity": "exact_event"}
    entry = sel_mod.Selector(ctx).select_scene(exact)
    assert "SEGMENT_REJECTED" in issues(ctx)
    assert entry["segment_check"]["status"] == "confirmed"  # a próxima opção passou na conferência
    assert entry["validation"]["status"] == "validated"


def test_sem_conferencia_possivel_fica_para_revisao(env):
    env["monkeypatch"].setattr(yt, "search_page", lambda q, n=50, lang="en", t=None: ([ytc(1)], None))
    env["monkeypatch"].setattr(yt, "download_segment", lambda vid, a, b, dest: dest.write_bytes(b"v") or dest)

    def boom(*a, **k):
        raise ffmpeg.FFmpegError("sem frames")

    env["monkeypatch"].setattr(media_index, "frames_at", boom)
    exact = {**scene(source="youtube"), "visual_role": "exact_evidence", "required_identity": "exact_event"}
    entry = sel_mod.Selector(make_ctx()).select_scene(exact)
    assert entry["segment_check"]["status"] == "unverified"
    assert entry["validation"]["status"] == "review_required"


# ---------------------------------------------------------------- D5: manifesto e créditos
def test_manifesto_e_creditos(tmp_path):
    job = tmp_path
    (job / "assets").mkdir()
    (job / "assets" / "s001.mp4").write_bytes(b"video")
    (job / "assets" / "s002.png").write_bytes(b"png")
    plan = {"scenes": [
        {"id": "s001", "start": 0, "end": 5, "text": "x", "source": "stock", "claim_ids": ["clm01"],
         "visual_role": "contextual_illustration"},
        {"id": "s002", "start": 5, "end": 9, "text": "y", "source": "ai"}]}
    selection = {
        "s001": {"source": "stock", "source_used": "stock", "provider": "pexels", "asset": "assets/s001.mp4",
                 "page_url": "https://pexels.com/v/1", "author": "Ana", "license": "Pexels License",
                 "method": "vision", "score": 8.0, "in_point": 2.0, "out_point": 7.0,
                 "obtained_how": "API oficial do Pexels (arquivo oferecido pela API)", "retrieved_at": "2026-10-07"},
        "s002": {"source": "ai_image", "source_used": "ai_image", "provider": "darkvi", "asset": "assets/s002.png",
                 "prompt": "a farmer", "method": "ai_image", "score": None, "is_image": True}}
    bible = {"claims": [{"id": "clm01", "quote": "a batata cura tudo", "needs_source": True}]}
    refs = {"scenes": {"s001": [{"youtube_id": "abc", "url": "https://www.youtube.com/watch?v=abc",
                                 "status": "reference_only"}]}}
    for name, data in (("plan.json", plan), ("selection.json", selection), ("context_bible.json", bible),
                       ("references.json", refs)):
        (job / name).write_text(json.dumps(data), encoding="utf-8")
    m = build_manifest(job)
    a1 = m["assets"][0]
    assert a1["sha256"] and a1["obtained_how"].startswith("API oficial") and a1["checked_at"] == "2026-10-07"
    assert a1["segment"] == [2.0, 7.0] and "sem áudio original" in a1["adaptations"]
    assert m["assets"][1]["ai_generated"] and "não é registro real" in m["assets"][1]["obtained_how"]
    assert m["claims_pending_source"][0]["source"] is None  # nada inventado
    assert m["references_not_used"][0]["youtube_id"] == "abc"
    text = credits_text(m)
    assert "Pexels: Ana" in text and "gerada(s) por IA" in text and "a batata cura tudo" in text


# ---------------------------------------------------------------- API de mídia autorizada
@needs_ffmpeg
def test_api_associa_arquivo_autorizado(video):
    from fastapi.testclient import TestClient

    from app.api.media import youtube_id
    from app.main import app

    assert youtube_id("https://youtu.be/dQw4w9WgXcQ?t=3") == "dQw4w9WgXcQ"
    assert youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=x") == "dQw4w9WgXcQ"
    with TestClient(app) as c:
        files = {"file": ("meu.mp4", video.read_bytes(), "video/mp4")}
        r = c.post("/api/authorized-media", files=files, data={"rights_note": " ", "youtube_url": ""})
        assert r.status_code == 422
        r = c.post("/api/authorized-media", files=files,
                   data={"rights_note": "vídeo do meu canal", "youtube_url": "https://youtu.be/dQw4w9WgXcQ"})
        assert r.status_code == 200, r.text
        card = r.json()
        assert card["youtube_id"] == "dQw4w9WgXcQ" and card["width"] == 1280 and card["available"]
        bad = c.post("/api/authorized-media", files={"file": ("x.mp4", b"not a video" * 200, "video/mp4")},
                     data={"rights_note": "meu"})
        assert bad.status_code == 400
        assert c.delete(f"/api/authorized-media/{card['id']}").status_code == 200
