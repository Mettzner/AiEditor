"""Benchmark offline (seção 11): os casos anotados rodam pelo pós-processamento real do app, sem rede nem custo."""
from __future__ import annotations

from app import evaluation


def test_benchmark_offline_atende_as_anotacoes():
    report = evaluation.run(evaluation.load_cases())
    assert not report["failures"], report["failures"]
    assert {c["id"] for c in report["cases"]} >= {"italiano_moringa", "plantas_semelhantes",
                                                  "evento_historico_chernobyl", "pessoa_real_curie",
                                                  "flashback_katrina", "comparacao_lavoura", "documentario_longo"}
    for metric, t in report["totals"].items():
        assert t["ok"] == t["total"], (metric, t)
    for case in report["cases"]:
        interp = case["interpretation"]
        assert interp["scenes_crossing_contexts"] == 0, case["id"]
        assert interp["contradictions"]["found"] == interp["contradictions"]["expected"], case["id"]
        assert interp["context_blocks"]["found"] == interp["context_blocks"]["expected"], case["id"]
        if case["search"]:
            assert case["search"]["precision_at_1"] == 1.0, case["id"]
    longo = next(c for c in report["cases"] if c["id"] == "documentario_longo")
    assert longo["llm_calls"] == 1 + -(-300 // 45)  # Bíblia + janelas de 45 unidades
    assert "não medem" in report["warning"]
    assert "| caso |" in evaluation.to_markdown(report)


def test_modo_real_exige_autorizacao_e_orcamento(monkeypatch):
    monkeypatch.delenv("AIEDITOR_LIVE", raising=False)
    assert evaluation.main(["--live", "--budget", "1"]) == 2
    monkeypatch.setenv("AIEDITOR_LIVE", "1")
    assert evaluation.main(["--live"]) == 2  # sem teto, nada roda
