"""Exclusão definitiva e produção nova sem herança: id reaproveitado pelo SQLite nunca encontra arquivos de outra
produção, e trechos de narração de outro texto são descartados."""
from types import SimpleNamespace

import pytest
from sqlmodel import select

from app.config import DATA_DIR, job_dir
from app.db import session_scope
from app.models import Channel, Issue, Production, ProductionStep, UsedAsset
from app.pipeline import audio
from app.purge import PurgeError, purge_channel, purge_orphans, purge_production, remove_job_files


def _production(s, channel_id: int, status: str = "done") -> Production:
    p = Production(channel_id=channel_id, title="t", script="s", config={}, status=status)
    s.add(p)
    s.commit()
    s.refresh(p)
    s.add(Issue(production_id=p.id, code="X", severity="info", message="m"))
    s.add(ProductionStep(production_id=p.id, step="audio"))
    s.add(UsedAsset(channel_id=channel_id, production_id=p.id, source="stock", external_id=f"a{p.id}"))
    s.commit()
    out = job_dir(p.id) / "output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "final.mp4").write_bytes(b"video")
    return p


def _channel(s) -> Channel:
    c = Channel(name="Canal de teste", preset={})
    s.add(c)
    s.commit()
    s.refresh(c)
    return c


def test_excluir_producao_apaga_registros_e_arquivos():
    with session_scope() as s:
        p = _production(s, _channel(s).id)
        pid = p.id
        purge_production(s, p)
        s.commit()
        assert s.get(Production, pid) is None
        for model in (Issue, ProductionStep, UsedAsset):
            assert not list(s.exec(select(model).where(model.production_id == pid)))
    assert not (DATA_DIR / "jobs" / str(pid)).exists()


def test_producao_em_andamento_nao_e_excluida():
    with session_scope() as s:
        p = _production(s, _channel(s).id, status="running")
        with pytest.raises(PurgeError):
            purge_production(s, p)
        assert (job_dir(p.id) / "output" / "final.mp4").exists()


def test_excluir_canal_leva_as_producoes_junto():
    with session_scope() as s:
        c = _channel(s)
        ids = [_production(s, c.id).id for _ in range(2)]
        ref = DATA_DIR / "references" / f"channel_{c.id}.png"
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_bytes(b"png")
        assert purge_channel(s, c) == 2
        s.commit()
        assert all(s.get(Production, i) is None for i in ids)
    assert not ref.exists()
    assert not any((DATA_DIR / "jobs" / str(i)).exists() for i in ids)


def test_inicializacao_apaga_sobras_de_exclusoes_antigas():
    orphan = job_dir(9001)
    (orphan / "audio").mkdir(parents=True, exist_ok=True)
    (orphan / "audio" / "tts_00.mp3").write_bytes(b"narracao antiga")
    antigos = DATA_DIR / "jobs" / "_antigos" / "3-20261004"
    antigos.mkdir(parents=True, exist_ok=True)
    with session_scope() as s:
        kept = _production(s, _channel(s).id).id
        # issue e etapa têm chave estrangeira; ativos usados não, e sobravam ao excluir
        s.add(UsedAsset(production_id=9001, source="stock", external_id="orfao"))
        s.commit()
        purge_orphans(s)
        assert not list(s.exec(select(UsedAsset).where(UsedAsset.production_id == 9001)))
    assert not orphan.exists() and not antigos.parent.exists()
    assert (job_dir(kept) / "output" / "final.mp4").exists()


def test_pasta_de_id_reaproveitado_comeca_vazia():
    (job_dir(9002) / "output").mkdir(parents=True, exist_ok=True)
    (job_dir(9002) / "output" / "final.mp4").write_bytes(b"video antigo de 17 s")
    remove_job_files(9002)
    assert not (DATA_DIR / "jobs" / "9002").exists()


def test_trecho_de_narracao_de_outro_texto_e_descartado(tmp_path):
    ctx = SimpleNamespace(path=lambda *p: tmp_path.joinpath(*p))
    (tmp_path / "audio").mkdir()
    old = tmp_path / "audio" / "tts_00.mp3"
    old.write_bytes(b"narracao antiga")
    (tmp_path / "audio" / "tts_00.json").write_text('{"job": "antigo"}', encoding="utf-8")
    audio._drop_stale_chunk(ctx, 0, "roteiro novo de 30 minutos", "voz")
    assert not old.exists() and not (tmp_path / "audio" / "tts_00.json").exists()

    old.write_bytes(b"narracao nova")  # baixada nesta produção: o retry reaproveita
    audio._drop_stale_chunk(ctx, 0, "roteiro novo de 30 minutos", "voz")
    assert old.read_bytes() == b"narracao nova"
