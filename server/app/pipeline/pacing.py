"""Ritmo das cenas: tempo em tela perto da média pedida e nunca acima do teto.

O planejamento agrupa a narração por sentido, e frases longas viram cenas de 9 a 13 s. Um mesmo clipe tanto tempo
na tela cansa e aumenta o risco de reivindicação de direitos (Content ID). Depois do LLM, este passo:

1. junta fragmentos curtos demais (menos de 60% da média) à cena vizinha do mesmo contexto;
2. divide cenas acima do teto (média × 1,5) em tomadas de duração parecida com a média, cortando nas pausas entre
   palavras. Cada tomada é uma cena própria, com o mesmo brief e outro enquadramento, e a seleção nunca repete um
   clipe, então cada tomada recebe material diferente.
"""
from __future__ import annotations

import math

MAX_FACTOR = 1.5  # teto = média × 1,5 (4,5 s → 6,75 s)
MIN_FACTOR = 0.6  # abaixo de média × 0,6 a cena é juntada à vizinha
MERGE_LIMIT = 1.3  # a junção não passa de média × 1,3
MIN_PART = 1.5  # nenhuma tomada com menos de 1,5 s

# a tomada seguinte varia o enquadramento da anterior
NEXT_SHOT = {"wide": "close-up", "medium": "close-up", "close-up": "medium", "overhead": "close-up",
             "pov": "medium"}
EMPHASIS_RANK = {"none": 0, "calm": 1, "tension": 2, "reveal": 3}


def max_scene_seconds(avg: float) -> float:
    return round(avg * MAX_FACTOR, 2)


def _dur(s: dict) -> float:
    return s["end"] - s["start"]


def _words_in(words: list[dict], start: float, end: float) -> list[dict]:
    return [w for w in words if start <= (w["start"] + w["end"]) / 2 < end]


# ---------------------------------------------------------------- junção de fragmentos

def _absorb(keep: dict, other: dict, first: dict) -> dict:
    """`keep` (o mais longo) absorve `other`; `first` é o que vem antes no tempo (dita capítulo e início)."""
    out = dict(keep)
    out["start"], out["end"] = min(keep["start"], other["start"]), max(keep["end"], other["end"])
    a, b = (keep, other) if first is keep else (other, keep)
    out["text"] = f"{a.get('text', '')} {b.get('text', '')}".strip()
    out["chapter_break"], out["chapter_title"] = first.get("chapter_break", False), first.get("chapter_title")
    for k in ("highlight", "quote"):
        out[k] = keep.get(k) or other.get(k)
    if EMPHASIS_RANK.get(other.get("emphasis") or "none", 0) > EMPHASIS_RANK.get(keep.get("emphasis") or "none", 0):
        out["emphasis"] = other["emphasis"]
    if keep.get("units") and other.get("units"):
        out["units"] = [min(keep["units"][0], other["units"][0]), max(keep["units"][1], other["units"][1])]
    out["speech_end"] = max(keep.get("speech_end") or 0, other.get("speech_end") or 0)
    return out


def merge_short(scenes: list[dict], avg: float) -> list[dict]:
    """Junta cenas curtas demais à vizinha do mesmo bloco de contexto, sem atravessar um capítulo."""
    out = [dict(s) for s in scenes]
    i = 0
    while i < len(out):
        s = out[i]
        if _dur(s) >= avg * MIN_FACTOR or len(out) == 1:
            i += 1
            continue
        prev = out[i - 1] if i > 0 else None
        nxt = out[i + 1] if i + 1 < len(out) else None

        def fits(a: dict | None, b: dict | None) -> bool:  # a vem antes de b
            return bool(a and b and not b.get("chapter_break") and a.get("context_id") == b.get("context_id")
                        and _dur(a) + _dur(b) <= avg * MERGE_LIMIT)

        options = []
        if fits(prev, s):
            options.append(("prev", _dur(prev)))
        if fits(s, nxt):
            options.append(("next", _dur(nxt)))
        if not options:
            i += 1
            continue
        side = min(options, key=lambda o: o[1])[0]  # junta ao vizinho mais curto: o resultado fica mais perto da média
        if side == "prev":
            keep, other = (prev, s) if _dur(prev) >= _dur(s) else (s, prev)
            out[i - 1:i + 1] = [_absorb(keep, other, prev)]
            i = max(0, i - 1)
        else:
            keep, other = (nxt, s) if _dur(nxt) >= _dur(s) else (s, nxt)
            out[i:i + 2] = [_absorb(keep, other, s)]
    return out


# ---------------------------------------------------------------- divisão em tomadas

def _gaps(words: list[dict], start: float, end: float) -> list[tuple[float, float]]:
    """(instante do corte, tamanho da pausa) no meio de cada pausa entre palavras dentro da cena."""
    ws = _words_in(words, start, end)
    return [((a["end"] + b["start"]) / 2, max(0.0, b["start"] - a["end"])) for a, b in zip(ws, ws[1:])]


def cut_points(start: float, end: float, words: list[dict], avg: float, max_len: float,
               avoid: tuple[float, float] | None = None) -> list[float]:
    """Cortes internos de uma cena longa: n tomadas perto da média (nenhuma acima do teto), cada corte puxado para
    a maior pausa próxima do ponto ideal e fora do trecho `avoid` (a fala de uma citação)."""
    dur = end - start
    if dur <= max_len:
        return []
    n = max(math.ceil(dur / max_len), round(dur / avg))
    step = dur / n
    gaps = _gaps(words, start, end)

    def allowed(t: float) -> bool:
        return not (avoid and avoid[0] < t < avoid[1])

    cuts: list[float] = []
    prev = start
    for k in range(1, n):
        ideal = start + k * step
        window = 0.25 * step
        near = [(t, g) for t, g in gaps if abs(t - ideal) <= window and allowed(t)]
        # pausa maior ajuda (até 0,4 s), mas a distância do ponto ideal pesa mais: tomadas parecidas com a média
        t = max(near, key=lambda c: min(c[1], 0.4) * 1.5 - abs(c[0] - ideal))[0] if near else ideal
        if not allowed(t):  # o ponto ideal caiu dentro da citação: corta na borda mais próxima
            t = min(avoid, key=lambda b: abs(b - ideal))  # type: ignore[arg-type]
        remaining = n - k
        # cada tomada entre MIN_PART e o teto, contando o que ainda falta dividir
        t = min(max(t, prev + MIN_PART, end - remaining * max_len), prev + max_len, end - remaining * MIN_PART)
        cuts.append(round(t, 3))
        prev = t
    return cuts


def split_scene(scene: dict, words: list[dict], avg: float, max_len: float,
                quote_span: tuple[float, float] | None = None) -> list[dict]:
    avoid = (quote_span[0] - 0.6, quote_span[1] + 1.6) if quote_span else None
    if avoid and avoid[1] - avoid[0] > max_len:
        avoid = None  # citação longa demais para caber numa tomada: a direção decide se ela entra
    cuts = cut_points(scene["start"], scene["end"], words, avg, max_len, avoid)
    if not cuts:
        return [scene]
    bounds = [scene["start"], *cuts, scene["end"]]
    n = len(bounds) - 1
    queries = list(scene.get("queries") or [])
    parts = []
    shot = scene.get("shot") or "medium"
    for k in range(n):
        a, b = bounds[k], bounds[k + 1]
        part = {**scene, "start": round(a, 3), "end": round(b, 3), "part": [k + 1, n]}
        if queries:
            part["youtube_query"] = queries[0]  # mesma busca do YouTube em todas as tomadas (cache, sem cota extra)
        ws = _words_in(words, a, b)
        if ws:
            part["text"] = " ".join(w["text"] for w in ws)
        if k > 0:
            shot = NEXT_SHOT.get(shot, "close-up")
            part["shot"] = shot
            part["queries"] = queries[k % len(queries):] + queries[:k % len(queries)] if queries else queries
            part["visual_intent"] = (f"{scene.get('visual_intent', '')} Another {shot} shot of the same moment, "
                                     "a different angle.").strip()
            # capítulo, destaque e revelação acontecem uma vez, na primeira tomada
            part.update(chapter_break=False, chapter_title=None, highlight=None)
            if part.get("emphasis") == "reveal":
                part["emphasis"] = "none"
        if scene.get("quote"):
            holds = quote_span is not None and a <= quote_span[0] < b
            part["quote"] = scene["quote"] if (holds or (quote_span is None and k == 0)) else None
        parts.append(part)
    return parts


def pace(scenes: list[dict], words: list[dict], avg: float) -> list[dict]:
    """Junta os fragmentos, divide as cenas longas e renumera os ids (s001, s002...)."""
    from .direct import quote_word_times

    max_len = max_scene_seconds(avg)
    out: list[dict] = []
    for s in merge_short(scenes, avg):
        span = None
        if s.get("quote") and _dur(s) > max_len:
            times = quote_word_times(s["quote"], words, s["start"], s["end"])
            span = (times[0], times[-1]) if times else None
        out += split_scene(s, words, avg, max_len, span)
    for n, s in enumerate(out, start=1):
        s["id"] = f"s{n:03d}"
    return out
