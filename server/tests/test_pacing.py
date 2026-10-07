"""Ritmo: tempo em tela perto da média, teto por cena, tomadas com clipes diferentes; YouTube primeiro."""
from __future__ import annotations

import pytest

from app.pipeline.pacing import cut_points, max_scene_seconds, merge_short, pace
from app.providers.youtube import client as yt


def words_between(start: float, end: float, step: float = 0.4, pause_at: tuple[float, ...] = ()) -> list[dict]:
    out, t, n = [], start, 0
    while t + 0.3 <= end:
        out.append({"text": f"w{n}", "start": round(t, 3), "end": round(t + 0.3, 3)})
        t += step + (0.5 if any(abs(t - p) < step / 2 for p in pause_at) else 0)
        n += 1
    return out


def sc(sid: str, start: float, end: float, **kw) -> dict:
    return {"id": sid, "start": start, "end": end, "text": sid, "context_id": "ctx1", "chapter_break": False,
            "chapter_title": None, "highlight": None, "quote": None, "emphasis": "none", "shot": "wide",
            "queries": ["ranch dry land", "ranch cattle", "dry field"], "visual_intent": "a dry ranch", **kw}


def test_teto_e_media_vezes_1_5():
    assert max_scene_seconds(4.5) == 6.75


def test_cena_longa_vira_tomadas_perto_da_media_e_abaixo_do_teto():
    words = words_between(0, 13.5)
    out = pace([sc("s001", 0, 13.5, chapter_break=True, chapter_title="Cap", highlight="47")], words, 4.5)
    durs = [s["end"] - s["start"] for s in out]
    assert len(out) == 3 and max(durs) <= 6.75 and all(3.0 <= d <= 6.0 for d in durs)
    assert [s["id"] for s in out] == ["s001", "s002", "s003"]
    assert out[0]["chapter_break"] and out[0]["highlight"] == "47"
    assert not out[1]["chapter_break"] and out[1]["highlight"] is None  # acontece uma vez, na 1ª tomada
    assert out[1]["shot"] != out[0]["shot"] and out[1]["queries"] != out[0]["queries"]  # outro enquadramento
    assert {s["youtube_query"] for s in out} == {"ranch dry land"}  # mesma busca do YouTube (cache)
    assert out[0]["start"] == 0 and out[-1]["end"] == 13.5
    assert all(a["end"] == b["start"] for a, b in zip(out, out[1:]))


def test_corte_prefere_a_pausa_perto_do_ponto_ideal():
    words = words_between(0, 9.0, pause_at=(4.0,))
    (cut,) = cut_points(0, 9.0, words, 4.5, 6.75)
    gap = next((a["end"] + b["start"]) / 2 for a, b in zip(words, words[1:]) if b["start"] - a["end"] > 0.4)
    assert cut == pytest.approx(gap, abs=0.01)


def test_cena_dentro_do_teto_nao_e_dividida():
    assert cut_points(0, 6.5, words_between(0, 6.5), 4.5, 6.75) == []


def test_fragmento_curto_junta_ao_vizinho_do_mesmo_contexto():
    out = merge_short([sc("s001", 0, 3.5), sc("s002", 3.5, 5.0, highlight="1887"), sc("s003", 5.0, 9.5)], 4.5)
    assert [(s["start"], s["end"]) for s in out] == [(0, 5.0), (5.0, 9.5)]
    assert out[0]["highlight"] == "1887"


def test_fragmento_nao_atravessa_capitulo_nem_contexto():
    scenes = [sc("s001", 0, 4.0), sc("s002", 4.0, 5.5, chapter_break=True),
              sc("s003", 5.5, 7.0, context_id="ctx2")]
    assert len(merge_short(scenes, 4.5)) == 3


def test_citacao_fica_inteira_numa_tomada():
    words = [{"text": w, "start": 0.4 * i, "end": 0.4 * i + 0.3}
             for i, w in enumerate("uno dos tres cuatro cinco seis siete ocho nadie volvió a verlo nunca más "
                                   "en este pueblo tan pequeño y seco".split())]
    end = words[-1]["end"] + 0.2
    quote = "nadie volvió a verlo nunca más"
    out = pace([sc("s001", 0, end, quote=quote)], words, 3.0)
    q_start, q_end = words[8]["start"], words[13]["end"]
    holders = [s for s in out if s.get("quote")]
    assert len(holders) == 1 and holders[0]["start"] <= q_start and holders[0]["end"] >= q_end


# ---------------------------------------------------------------- YouTube
def test_proporcao_pelo_player_embutido():
    assert yt.player_aspect({"embedWidth": "1280", "embedHeight": "720"}) == pytest.approx(16 / 9)
    assert yt.player_aspect({"embedWidth": 405, "embedHeight": 720}) < 1
    assert yt.player_aspect({}) is None


def test_busca_descarta_shorts_e_verticais(monkeypatch):
    def fake_get(path, params, cost):
        if path == "search":
            return {"items": [{"id": {"videoId": v}} for v in ("wide", "tall", "short")]}
        assert "player" in params["part"] and params["maxWidth"] == 1280

        def item(vid, w, h, title):
            return {"id": vid, "status": {"license": "creativeCommon"}, "player": {"embedWidth": w, "embedHeight": h},
                    "snippet": {"title": title, "channelTitle": "c"}, "contentDetails": {"duration": "PT2M",
                                                                                         "definition": "hd"}}
        return {"items": [item("wide", 1280, 720, "ranch at noon"), item("tall", 405, 720, "ranch"),
                          item("short", 1280, 720, "ranch #Shorts")]}

    monkeypatch.setattr(yt, "_get", fake_get)
    monkeypatch.setattr(yt.quota, "can_search", lambda: True)
    assert [c.external_id for c in yt.search("ranch")] == ["wide"]
