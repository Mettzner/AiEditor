"""Precisão visual: regras compartilhadas por planejamento, busca, geração de imagem e validação.

MELHORIA_PRECISAO_VISUAL.md, ajustado por AJUSTE_ESTILO_CONTEXTUAL.md:
- O assunto manda: toda cena tem um `subject` concreto que precisa aparecer.
- O estilo serve ao contexto: real primeiro; animação, 3D, ilustração, pintura ou cartoon só quando
  representam melhor o trecho (interior do corpo, história sem registro, lendas, espaço, hipóteses...).
- Sempre proibido: franquias e marcas reconhecíveis, gameplay comercial, interfaces/capturas de tela,
  marca d'água e imagem de IA com defeito.
- Todo texto na tela segue o idioma do vídeo.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

Style = Literal["real_footage", "cgi_3d", "animation", "illustration", "painting_historical", "cartoon",
                "video_game"]
StyleAllowance = Literal["real_only", "real_preferred", "stylized_ok"]

# ---------------------------------------------------------------- Etapa A: Bíblia de Contexto
# O antigo "brief do vídeo" virou a Bíblia de Contexto (CONTEXTO_PROFUNDO_DO_ROTEIRO.md): mesmos campos de
# tema/público/tom/mundo visual/entidades recorrentes/global_avoid, mais a linha do tempo de contextos (época,
# lugar, sociedade, cultura material, anacronismos). Esquema e regras ficam em pipeline/context.py.
from .context import BIBLE_RULES, DEFAULT_GLOBAL_AVOID  # noqa: E402


# ---------------------------------------------------------------- Etapa B: brief visual por cena

MEDIA_STYLE_RULES = {
    "real_only": "The channel accepts ONLY real footage: every scene must have style_allowance \"real_only\" and "
                 "allowed_styles [\"real_footage\"]. Figurative and abstract ideas become real, human scenes.",
    "real_preferred": "The channel PREFERS real footage. Use a stylized look only when the scene asks for it "
                      "(see STYLE rules).",
    "free": "The channel accepts any style. Real footage and stylized looks compete equally: choose "
            "\"stylized_ok\" whenever a style represents the scene better or enriches it.",
}

PLAN_SYSTEM = """You are the editing director of a narrated documentary channel (B-roll over narration).
You receive the narration split into numbered units with timings, and you return the video's scenes.
Every visual decision must follow the CONTEXT BIBLE (when, where, with whom and in what environment each part
happens) and the principles below.

PRINCIPLES
0. Interpret before you illustrate. Each scene shows what the script MEANS at that moment, inside the story the
   CONTEXT BIBLE describes (summary, intent, story_beats, characters, era and place), not just the nouns of the
   sentence. Ask: who is this about, where are they, what is happening, what is implied, what should the viewer
   feel? "He knew it was the last time" shows the person and the moment, not a calendar or a clock.
1. The subject rules. Every scene has one main subject (a concrete noun) that must be clearly visible.
   Adjectives and context refine it but never replace it.
2. Use the whole script as context: the video topic, the audience, the era, the place, the culture and the
   neighbouring sentences (previous and next units are in the listing).
   An image must never break its era or place: a scene set in 1850 never shows cars, power lines, asphalt, phones
   or modern clothes; a script about rural Jamaica never shows a European village or snow.
3. Literal vs figurative. Figurative expressions ("froze in fear", "battle against the disease", "game
   changer", "root of the problem") never become literal images (ice, war, video game, tree roots).
4. Abstract becomes concrete and human: "health improves" becomes a real person walking outdoors with
   energy, never charts, icons or artificial visual metaphors.
5. The style serves the context. Real footage is the first choice. Animation, 3D/CGI, illustration, cartoon
   or painting are accepted only when they represent the narration BETTER or when no real footage can exist:
   inside the body/cells/molecules → scientific 3D or animation; history with no video record → period
   paintings, engravings, historical illustration; legends, myths, creatures → illustration or concept art;
   space and impossible phenomena → realistic CGI; hypothetical or future scenarios → CGI or illustration;
   the script talks about cartoons or games themselves → generic cartoon or game (prefer real people
   playing or watching first); step-by-step inner processes → explanatory animation.
   A stylized image for something real and filmable (fruit, a person cooking, a backyard plant) is an ERROR.
6. Style blocks, not ping-pong: consecutive stylized scenes form a block with the SAME style (the whole
   "how vitamin C works in the cell" passage in 3D animation), then the video returns to real footage.
   Never alternate real ↔ stylized on every cut.
7. Never: recognizable characters, brands or franchises (Mario, Minecraft, Disney, Marvel, Pokémon...),
   commercial gameplay, screenshots of apps/sites, interfaces, HUDs, watermarks, logos.
8. When in doubt, choose the simple correct image (the subject in close-up) over a beautiful wrong one.

CHANNEL MEDIA STYLE: given in PRODUCTION SETTINGS (context).

SCENE RULES
- LANGUAGE: subject, must_show, setting, action, mood, must_avoid, visual_intent, queries, archival_query,
  timeless_alternative and timeless_query are ALWAYS in English, whatever the video language (stock sites and
  image models work in English). Only on-screen texts follow the video language.
- Each scene is a contiguous range of units [first_unit, last_unit]. Scenes cover ALL units of the window,
  in order, with no gaps or overlaps. Never split a unit; a scene may have a single unit.
- Target average scene duration and its variation: given in PRODUCTION SETTINGS.
- meaning: one English sentence (at most 20 words) on what this moment conveys in the story, with the implied
  who, where and why ("Alejandro, exhausted at his dry ranch, fears losing his last cattle"). Every other field of
  the scene must serve it.
- entities: names of recurring_entities from the bible that are VISIBLE in this shot (exact names), else [].
  Their fixed look is applied automatically, so the same person looks the same in every scene.
- literal: false when the sentence is figurative; then the subject represents the MEANING, not the words.
- subject: ONE concrete noun with at most one modifier ("frozen strawberries", "frightened man", "human
  cell"). Never a concept ("health", "freshness") or a lone adjective.
- subject_category: food | plant | person | animal | object | place | activity | nature | other.
- must_show: 1 to 3 visible elements that prove the scene is right.
- setting, action, shot (close-up | medium | wide | overhead | pov), mood: short phrases.
- must_avoid: the LIKELY CONFUSIONS for this scene. Ask yourself: "what wrong object would a search with
  these words bring?" (frozen fruit → freezer appliance; cold remedy → snow; root cause → tree roots;
  game changer → video game; battle with disease → soldiers, war). 2 to 5 items, each a short object
  name of 1 or 2 words ("freezer", "refrigerator", "ice cubes", "snow", "tree roots") so it can be matched
  against footage titles.
- STYLE: style_allowance, allowed_styles, style_reason.
  * concrete, filmable subject → "real_only", allowed_styles ["real_footage"];
  * real subject that is hard to film well → "real_preferred", ["real_footage", <best alternative style>];
  * subject that cannot be filmed, or that is about the style itself → "stylized_ok", with the fitting
    styles first and "real_footage" too if real footage could still work.
  * allowed_styles ⊂ real_footage, cgi_3d, animation, illustration, painting_historical, cartoon, video_game.
  * style_reason: at most 8 words ("concrete everyday food, easy to film"; "inside human cells, no real
    footage").
  * Keep neighbouring stylized scenes in the same style (block).
CONTEXT (every scene lives in the context block that contains its units)
- context_id: the id of the block that contains the scene's units.
- setting, action, clothing, light and objects must belong to the block's era and place. must_avoid stays about
  the scene's likely confusions; the block's anachronisms are added automatically.
- era_markers_to_show: 0 to 3 era elements visible in this shot (from the block); [] outside historical blocks.
- Historical blocks: queries follow [period vocabulary] + [subject] + [detail], using the block's
  search_vocabulary, NEVER a bare year ("victorian family dinner candlelight", "19th century farmer field
  reenactment", "period drama horse carriage"). Query 1 aims at a period reenactment or costume drama.
  archival_query: 2 to 5 words to find period photos, paintings, engravings or archival film ("irish famine
  engraving", "victorian family painting", "1920s farm archival footage").
  timeless_alternative: one sentence describing a SAFE shot with no era markers that still fits the narration
  (candle flame, hearth fire, hands in close-up without watches or rings, nature, sky, sea, rain, fields, animals,
  an old book, aged paper, quill and ink, hand tools, wood or stone textures). timeless_query: 2 to 5 words for it
  ("hands peeling potato candlelight").
- Contemporary and timeless blocks: archival_query, timeless_alternative and timeless_query are "".
- Place matters for contemporary content too: put the place in a query when it changes the look ("jamaican
  countryside", "caribbean village", "tropical garden"); vegetation, architecture and climate must match it.

- visual_intent: one English sentence of at most 25 words describing the exact shot (subject + action +
  setting + shot + light, and the style when it is not real footage).
- queries: exactly 3 English stock-footage searches of 2 to 5 words, ALWAYS starting with the subject:
  1) subject + its most important detail (the most literal; also used on YouTube);
  2) subject + setting or action;
  3) a broader variation of the subject (synonym, close-up or macro).
  When a stylized look is wanted, a query may include it ("human cell 3d animation", "ancient rome
  painting", "cryptid illustration forest").
  Forbidden in queries: abstract words (benefits, health, power, secret), brands, franchises, scientific
  names, verbs without an object, lone adjectives, and any word from must_avoid.
- kind: "concreto" (filmable and generic), "abstrato" (concept/emotion), "evento_especifico" (a real,
  identifiable fact, place or person).
- energy: "baixa" | "media" | "alta".
- affinity (0 to 1): how well each source serves the scene. stock = generic stock footage; youtube = real
  archive/news footage; ai = AI-generated still (good for very specific, historical or stylized scenes).
- ai_kind: "video" if motion is essential, "image" if a still with slow camera motion is enough.
- music_mood: only in the first window, 3 to 5 words for the whole video's musical mood; otherwise null.

ON-SCREEN TEXT
All on-screen text (cards, titles, highlights, lists, captions) MUST be written in the VIDEO LANGUAGE given in
PRODUCTION SETTINGS. Never translate into any other language. Reuse the script's own wording whenever possible
(the narration says "twelve protein-rich plants" → the card says "12 Protein-Rich Plants"). Format numbers and
units the way that language does (hint in PRODUCTION SETTINGS). Set overlay_language to the video language code
on every scene.

THE CONTEXT YOU RECEIVE
- PRODUCTION SETTINGS: scene duration, video language, channel media style, direction.
- SCRIPT UNITS: the whole narration as numbered units "[i|start-end] text" (times in seconds).
- The request either asks you to write ONLY the CONTEXT BIBLE, or gives the bible and names the units to plan.
Never repeat the narration text or the timings in the output: scenes refer to units by number. Output only JSON.

""" + BIBLE_RULES + """
(Write the bible only when the request asks for it, in English, except card_text, which is in the video
language.)"""

NUMBER_HINTS = {
    "en": " (1,500 mg; 2.5 kg; Title Case for titles)",
    "pt": " (1.500 mg; 2,5 kg; títulos com inicial maiúscula só na primeira palavra e nomes próprios)",
    "es": " (1.500 mg; 2,5 kg)",
}


def compact_json(data) -> str:
    """JSON estável e compacto: a mesma string byte a byte em toda chamada (não quebra o cache do prompt)."""
    import json

    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def brief_section(bible: dict | None) -> str:
    """Bíblia pronta vai no pedido (depois do contexto em cache); sem ela, o pedido manda escrevê-la."""
    if bible:
        return "CONTEXT BIBLE (already written, follow it): " + compact_json(bible)
    return "No context bible yet: follow the script and the principles."


# ---------------------------------------------------------------- estilos

# termos de metadados → estilo inferido do candidato
STYLE_TERMS: dict[str, str] = {
    "animation": "animation", "animated": "animation", "cartoon": "cartoon", "anime": "cartoon",
    "3d": "cgi_3d", "cgi": "cgi_3d", "render": "cgi_3d", "rendered": "cgi_3d", "unreal": "cgi_3d",
    "illustration": "illustration", "illustrated": "illustration", "drawing": "illustration",
    "clipart": "cartoon", "vector": "cartoon", "painting": "painting_historical",
    "game": "video_game", "gaming": "video_game", "gameplay": "video_game", "videogame": "video_game",
}
# sempre bloqueados, independentes do contexto (além das franquias configuráveis)
ALWAYS_BLOCKED_TERMS = ["screenshot", "screen recording", "ui", "hud", "user interface", "app interface",
                        "website", "watermark", "watermarked", "logo", "metaverse", "ai generated"]
# estilo "realism" da IA de visão → estilos de allowed_styles
REALISM_TO_STYLES: dict[str, set[str]] = {
    "real_footage": {"real_footage"}, "cgi_3d": {"cgi_3d", "animation"},
    "animation_cartoon": {"animation", "cartoon"}, "illustration": {"illustration", "painting_historical"},
    "painting": {"painting_historical", "illustration"}, "video_game": {"video_game"},
}
STYLE_PENALTY = 0.7  # −30% na nota de texto


def scene_style(scene: dict, media_style: str = "real_preferred") -> tuple[str, list[str]]:
    """(style_allowance, allowed_styles) da cena, respeitando o preset do canal."""
    if media_style == "real_only":
        return "real_only", ["real_footage"]
    allowance = scene.get("style_allowance") or "real_only"
    allowed = list(dict.fromkeys(scene.get("allowed_styles") or ["real_footage"]))
    if allowance == "real_only":
        allowed = ["real_footage"]
    if media_style == "free" and allowance == "real_preferred":
        allowance = "stylized_ok"
    return allowance, allowed


def wanted_style(scene: dict, media_style: str = "real_preferred") -> str:
    """Estilo para gerar imagem: o primeiro estilizado permitido numa cena stylized_ok; senão real."""
    allowance, allowed = scene_style(scene, media_style)
    if allowance == "stylized_ok":
        stylized = [s for s in allowed if s != "real_footage"]
        if stylized:
            return stylized[0]
    return "real_footage"


# ---------------------------------------------------------------- regras das queries

ABSTRACT_WORDS = {"benefits", "benefit", "health", "healthy", "power", "powerful", "secret", "secrets", "concept",
                  "idea", "success", "wellness", "importance", "amazing", "best", "ultimate", "tips", "facts"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", (text or "").lower())


def _is_year(word: str) -> bool:
    """Bancos de vídeo não entendem anos ("1850 family" traz lixo); décadas ("1920s") e "19th" ficam."""
    return bool(re.fullmatch(r"1\d{3}|20\d{2}", word))


def contains_phrase(text_words: list[str], phrase: list[str]) -> bool:
    n = len(phrase)
    return any(text_words[i:i + n] == phrase for i in range(len(text_words) - n + 1)) if n else False


def sanitize_queries(subject: str, queries: list[str], must_avoid: list[str],
                     allowed_styles: list[str] | None = None) -> list[str]:
    """Garante as regras: contém o assunto, 2–5 palavras, sem abstratas, sem termos do must_avoid e sem termos
    de estilo que a cena não permite ("game" numa cena real_only sai; "3d" numa cena de célula fica)."""
    allowed = set(allowed_styles or ["real_footage"])
    subject_words = _words(subject)[:4] or ["footage"]
    banned = {w for a in must_avoid for w in _words(a)}
    banned |= {t for t, style in STYLE_TERMS.items() if style not in allowed}
    banned -= set(subject_words)
    out: list[str] = []
    for q in queries:
        words = [w for w in _words(q) if w not in ABSTRACT_WORDS and w not in banned and not _is_year(w)]
        head = subject_words[-1]
        if words[: len(subject_words)] != subject_words and head not in words:
            rest = [w for w in words if w not in subject_words]
            words = subject_words + rest
        words = words[:5]
        if len(words) < 2:
            words = (subject_words + ["close", "up"])[:5] if len(subject_words) < 2 else subject_words
        query = " ".join(words)
        if query not in out:
            out.append(query)
    for f in (" ".join(subject_words), " ".join((subject_words + ["close", "up"])[:5])):
        if len(out) >= 3:
            break
        if f not in out:
            out.append(f)
    return out[:3]


def sanitize_free_query(query: str, must_avoid: list[str], allowed_styles: list[str] | None = None) -> str:
    """Queries de arquivo e de plano atemporal: não precisam conter o assunto da cena, mas seguem o resto das
    regras (sem anos, sem palavras abstratas, sem termos do must_avoid, 2 a 5 palavras)."""
    allowed = set(allowed_styles or ["real_footage"]) | {"painting_historical", "illustration"}
    banned = {w for a in must_avoid for w in _words(a)}
    banned |= {t for t, style in STYLE_TERMS.items() if style not in allowed}
    words = [w for w in _words(query) if w not in ABSTRACT_WORDS and w not in banned and not _is_year(w)][:5]
    return " ".join(words) if len(words) >= 2 else ""


# ---------------------------------------------------------------- Etapa C: filtros de metadados


def metadata_check(text: str, allowance: str, allowed_styles: list[str], must_avoid: list[str],
                   global_avoid: list[str], franchises: list[str]) -> tuple[str | None, float]:
    """(motivo de rejeição | None, multiplicador da nota de texto).

    Franquias, interfaces e marca d'água: sempre rejeitados. Termos de estilo: rejeitados em cena real_only,
    −30% quando o estilo não é permitido (ou em real_preferred); sem penalidade se permitido em stylized_ok.
    """
    words = _words(text)
    for name in franchises:
        if contains_phrase(words, _words(name)):
            return "franquia", 0.0
    for term in ALWAYS_BLOCKED_TERMS:
        if contains_phrase(words, term.split()):
            return "interface/marca", 0.0
    for item in list(must_avoid) + list(global_avoid):
        phrase = _words(item)
        if phrase and contains_phrase(words, phrase):
            return "proibido", 0.0
    styles = {STYLE_TERMS[w] for w in words if w in STYLE_TERMS}
    if not styles:
        return None, 1.0
    allowed = set(allowed_styles)
    if allowance == "real_only":
        return "estilo", 0.0
    if allowance == "real_preferred":
        return None, STYLE_PENALTY
    return (None, 1.0) if styles <= allowed else (None, STYLE_PENALTY)


def video_type_for(allowed_styles: list[str]) -> str:
    """Pixabay vídeos: film só para cenas reais; animation se só animação; all nos outros casos."""
    s = set(allowed_styles)
    if s <= {"real_footage"}:
        return "film"
    if s - {"real_footage"} <= {"animation", "cartoon"} and "real_footage" not in s:
        return "animation"
    return "all"


def image_type_for(allowed_styles: list[str]) -> str:
    """Pixabay imagens: photo para cenas reais; illustration/vector quando permitidos; all quando misto."""
    s = set(allowed_styles)
    if s <= {"real_footage"}:
        return "photo"
    if "real_footage" in s:
        return "all"
    if s <= {"cartoon"}:
        return "vector"
    return "illustration"


# ---------------------------------------------------------------- Etapa D: prompt de imagem (Darkvi)

PROMPT_LIMIT = 1000
CAMERA = ("Photorealistic documentary photograph, shot on a full-frame camera, 35mm lens, natural light, real "
          "textures, true-to-life colors, shallow depth of field, candid everyday moment, subtle film grain, "
          "natural imperfections.")
CAMERA_SHORT = "Photorealistic documentary photograph, natural light, real textures."
STYLE_BLOCKS = {
    "cgi_3d": "High-detail scientific 3D medical animation still, accurate anatomy, clean lighting, educational "
              "documentary style.",
    "animation": "High-detail scientific 3D medical animation still, accurate anatomy, clean lighting, educational "
                 "documentary style.",
    "painting_historical": "Historical oil painting in the style of the period, museum quality, accurate period "
                           "clothing and setting.",
    "illustration": "Detailed realistic illustration, documentary book style, natural colors, accurate details.",
    "cartoon": "Simple original cartoon style, generic characters, no existing franchise.",
    "video_game": "Simple original stylized game art, generic characters, no existing franchise.",
}
AVOID_ALWAYS = ["text", "watermark", "logos", "existing characters or franchises", "distorted hands"]
AVOID_REAL = ["cartoon", "illustration", "3D render", "CGI", "video game look", "anime", "painting", "plastic skin"]
STYLE_AVOID_DROP = {
    "cgi_3d": {"3D render", "CGI"}, "animation": {"3D render", "CGI", "cartoon", "anime"},
    "painting_historical": {"painting", "illustration"}, "illustration": {"illustration", "painting"},
    "cartoon": {"cartoon", "illustration", "anime"}, "video_game": {"video game look", "3D render", "CGI"},
}


PERIOD_CINEMATIC = ("Photorealistic still from a high-budget historical period film, authentic costumes and set "
                    "design, natural lighting, cinematic composition, real textures.")
PERIOD_CINEMATIC_SHORT = "Photorealistic still from a historical period film, authentic costumes and sets."


def period_style_block(ctx: dict, period_look: str = "cinematic") -> tuple[str, str]:
    """(bloco, versão curta) do estilo de representação de época (§7.1)."""
    from .context import decade_of

    feas = ctx.get("footage_feasibility")
    if period_look == "archival":
        decade = decade_of(ctx.get("era", ""), ctx.get("era_label", ""))
        if feas == "pre_film":
            b = f"Authentic {decade} daguerreotype photograph, sepia tones, soft focus, period-accurate subjects."
            return b, b
        if feas == "early_film":
            b = f"Authentic black-and-white archival photograph from the {decade}, film grain."
            return b, b
        if feas == "historical_modern":
            b = f"Authentic archival color photograph from the {decade}, faded film colors, film grain."
            return b, b
        if feas == "pre_photo":  # não existia fotografia: arte da época
            return STYLE_BLOCKS["painting_historical"], STYLE_BLOCKS["painting_historical"]
    return PERIOD_CINEMATIC, PERIOD_CINEMATIC_SHORT


def _entity_for(scene: dict, brief: dict) -> str:
    """Entidades recorrentes visíveis na cena, com a mesma descrição fixa em todo o vídeo. O planejamento diz quem
    aparece (`entities`); cenas antigas, sem o campo, caem na busca do nome no texto da cena."""
    entities = [e for e in brief.get("recurring_entities") or [] if e.get("name") and e.get("look")]
    named = {n.lower() for n in scene.get("entities") or []}
    if named:
        found = [e for e in entities if e["name"].lower() in named]
    else:
        text = (scene.get("visual_intent", "") + " " + scene.get("subject", "") + " " + scene.get("action", "")).lower()
        ctx_id = (scene.get("context") or {}).get("id") or scene.get("context_id")
        found = [e for e in entities if e["name"].lower() in text
                 and (not e.get("contexts") or not ctx_id or ctx_id in e["contexts"])][:1]
    return " ".join(f"{e['name']}: {brief_text(e['look'], 130 if len(found) == 1 else 90)}." for e in found[:2])


def _look_line(scene: dict, brief: dict) -> str:
    """Tratamento visual do vídeo inteiro (decidido pela bíblia) + paleta do bloco de contexto."""
    style = (brief.get("visual_style") or "").strip()
    palette = ((scene.get("context") or {}).get("palette") or "").strip()
    parts = [x for x in [brief_text(style, 140), brief_text(palette, 80)] if x]
    return f"Look: {'; '.join(parts)}." if parts else ""


def brief_text(text: str | None, limit: int = 90) -> str:
    """Descrição longa do contexto cortada numa vírgula/ponto e vírgula, para o bloco de estilo caber no prompt."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = max(text.rfind(";", 0, limit), text.rfind(",", 0, limit))
    return text[:cut] if cut > 30 else text[:limit].rsplit(" ", 1)[0]


def build_period_prompt(scene: dict, brief: dict | None, extra_feedback: str | None = None,
                        style: str = "real_footage", period_look: str = "cinematic",
                        extra_avoid: list[str] | None = None) -> str:
    """Prompt de imagem de cena histórica (§7), montado só pelo código a partir dos campos:

    [assunto + ação], [cenário], in [lugar], [época]. Period-accurate details: ... [entidade]. [estilo].
    Must show: [must_show + marcas de época]. Avoid: [anacronismos], moderno genérico, texto, marca d'água...
    Se passar de 1.000 caracteres, corta nesta ordem: mood, objetos/transporte, interiores, estilo longo, Avoid.
    """
    from .context import MODERN_GENERIC

    brief = brief or {}
    ctx = scene.get("context") or {}
    subject_action = scene.get("action") or scene.get("subject") or scene.get("visual_intent", "")
    if scene.get("subject") and scene["subject"].lower() not in subject_action.lower():
        subject_action = f"{scene['subject']}, {subject_action}"
    era = ctx.get("era") if re.search(r"\d", ctx.get("era") or "") else (ctx.get("era_label") or ctx.get("era"))
    place = ctx.get("place") or ""
    base = ", ".join(x for x in [subject_action, scene.get("setting")] if x)
    if place and place.split(",")[0].strip().lower() in base.lower():
        place = ""  # o cenário já diz o lugar: só a época
    head = ", ".join(x for x in [base, f"in {place}" if place else "", era or ""] if x)
    mood = scene.get("mood")
    interiors = ctx.get("interiors") if any(w in (scene.get("setting") or "").lower()
                                             for w in ("interior", "inside", "room", "kitchen", "cottage", "house",
                                                       "home", "hut", "cabin")) else ""
    lighting = f"lighting from {brief_text(ctx['lighting'], 60)}" if ctx.get("lighting") else ""
    objects = ", ".join((ctx.get("objects") or [])[:3])
    entity = _entity_for(scene, brief)
    look = _look_line(scene, brief)
    if style == "real_footage":
        block, block_short = period_style_block(ctx, period_look)
        style_avoid = ["cartoon", "illustration", "3D render", "CGI"]
    else:
        block = block_short = STYLE_BLOCKS.get(style, PERIOD_CINEMATIC)
        drop = STYLE_AVOID_DROP.get(style, set())
        style_avoid = [a for a in ["cartoon", "illustration", "3D render", "CGI"] if a not in drop]
    markers = list(dict.fromkeys((scene.get("must_show") or []) + (scene.get("era_markers_to_show")
                                                                   or ctx.get("era_markers_to_show") or [])))
    essential = ["text", "watermark", "logos", "existing characters", "distorted hands"]
    ordered = list(dict.fromkeys([a for a in (extra_avoid or []) if a] + list(ctx.get("anachronisms") or [])
                                 + MODERN_GENERIC + list(scene.get("must_avoid") or []) + style_avoid))
    ordered = [a for a in ordered if a not in essential]

    def assemble(parts_on: set[str], use_block: str, avoid_items: list[str]) -> str:
        details = [brief_text(ctx.get("clothing")), brief_text(ctx.get("architecture")),
                   brief_text(interiors) if "interiors" in parts_on else "",
                   lighting, objects if "objects" in parts_on else "",
                   ctx.get("transport") if "objects" in parts_on else ""]
        details = [d for d in details if d]
        parts = [head + (f", {mood}" if "mood" in parts_on and mood else "") + "."]
        if details:
            parts.append("Period-accurate details: " + ", ".join(details) + ".")
        if entity:
            parts.append(entity)
        parts.append(use_block)
        if look and "look" in parts_on:
            parts.append(look)
        if markers:
            parts.append("Must show: " + ", ".join(markers) + ".")
        if extra_feedback:
            parts.append(f"Fix from previous attempt: {extra_feedback}.")
        parts.append("Avoid: " + ", ".join(avoid_items + essential) + ".")
        return " ".join(p for p in parts if p)

    markers = markers[:5]
    steps = [({"mood", "objects", "interiors", "look"}, block), ({"objects", "interiors", "look"}, block),
             ({"interiors", "look"}, block), ({"look"}, block), (set(), block)]
    for on, use in steps:
        prompt = assemble(on, use, ordered)
        if len(prompt) <= PROMPT_LIMIT:
            return prompt
    # o bloco de estilo completo vale mais que o fim do Avoid (genéricos e confusões menos prováveis)
    items = list(ordered)
    while len(items) > 8:
        items.pop()
        prompt = assemble(set(), block, items)
        if len(prompt) <= PROMPT_LIMIT:
            return prompt
    prompt = assemble(set(), block_short, ordered)
    if len(prompt) <= PROMPT_LIMIT:
        return prompt
    items = list(ordered)
    while len(items) > 3:
        items.pop()
        prompt = assemble(set(), block_short, items)
        if len(prompt) <= PROMPT_LIMIT:
            return prompt
    return assemble(set(), block_short, items)[:PROMPT_LIMIT]


def build_image_prompt(scene: dict, brief: dict | None, extra_feedback: str | None = None,
                       style: str = "real_footage", period_look: str = "cinematic",
                       extra_avoid: list[str] | None = None) -> str:
    """Monta o prompt e corta, se passar de 1.000 caracteres: mood → bloco de estilo/câmera → finais do Avoid.

    Fotorrealista por padrão; numa cena stylized_ok, o bloco de câmera vira o bloco do estilo pedido e o Avoid
    deixa de proibir esse estilo. Cenas vizinhas com o mesmo estilo usam exatamente o mesmo bloco.
    Cena de contexto histórico: prompt de época (build_period_prompt). Cena atual ou atemporal: o lugar do
    contexto entra no cenário e os anacronismos do bloco entram no Avoid.
    """
    ctx = scene.get("context") or {}
    if ctx.get("setting_type") == "historical":
        return build_period_prompt(scene, brief, extra_feedback, style, period_look, extra_avoid)
    if ctx or extra_avoid:
        scene = {**scene, "must_avoid": list(dict.fromkeys(list(extra_avoid or []) + list(scene.get("must_avoid") or [])
                                                           + list(ctx.get("anachronisms") or [])))}
    brief = brief or {}
    if ctx.get("place"):
        brief = {**brief, "region_culture": ", ".join(x for x in [ctx.get("place"), ctx.get("landscape")] if x),
                 "visual_world": brief.get("visual_world", "")}
    subject_action = scene.get("action") or scene.get("subject") or scene.get("visual_intent", "")
    if scene.get("subject") and scene["subject"].lower() not in subject_action.lower():
        subject_action = f"{scene['subject']}, {subject_action}"
    head = ", ".join(x for x in [subject_action, scene.get("setting"), scene.get("shot")] if x)
    mood = scene.get("mood")
    world = "; ".join(x for x in [brief.get("region_culture"), brief.get("visual_world")] if x)
    entity = _entity_for(scene, brief)
    look = _look_line(scene, brief)
    must_show = ", ".join(scene.get("must_show") or [])
    if style == "real_footage":
        style_block, style_short = CAMERA, CAMERA_SHORT
        avoid_base = AVOID_ALWAYS[:3] + ["existing characters or franchises"] + AVOID_REAL + ["distorted hands"]
        avoid_base = list(dict.fromkeys(["cartoon", "illustration", "3D render", "CGI", "video game look"]
                                        + avoid_base))
    else:
        style_block = style_short = STYLE_BLOCKS.get(style, CAMERA)
        drop = STYLE_AVOID_DROP.get(style, set())
        avoid_base = AVOID_ALWAYS + [a for a in AVOID_REAL if a not in drop and a != "plastic skin"]
    avoid = avoid_base + [a for a in scene.get("must_avoid") or [] if a not in avoid_base]

    def assemble(with_mood: bool, block: str, avoid_items: list[str]) -> str:
        parts = [head + (f", {mood}" if with_mood and mood else "") + ".", block]
        if look and with_mood:
            parts.append(look)
        if world:
            parts.append(f"Setting consistent with: {world}.")
        if entity:
            parts.append(entity)
        if must_show:
            parts.append(f"Must show: {must_show}.")
        if extra_feedback:
            parts.append(f"Fix from previous attempt: {extra_feedback}.")
        parts.append("Avoid: " + ", ".join(avoid_items) + ".")
        return " ".join(p for p in parts if p)

    for with_mood, block in ((True, style_block), (False, style_block), (False, style_short)):
        prompt = assemble(with_mood, block, avoid)
        if len(prompt) <= PROMPT_LIMIT:
            return prompt
    if style == "real_footage":
        prompt = assemble(False, "", avoid)
        if len(prompt) <= PROMPT_LIMIT:
            return prompt
    items = list(avoid)
    keep_block = "" if style == "real_footage" else style_short
    while len(items) > 5:  # nunca corta os 5 primeiros termos do Avoid
        items.pop()
        prompt = assemble(False, keep_block, items)
        if len(prompt) <= PROMPT_LIMIT:
            return prompt
    return assemble(False, keep_block, items)[:PROMPT_LIMIT]


# ---------------------------------------------------------------- Etapa E: validação visual

Realism = Literal["real_footage", "cgi_3d", "video_game", "animation_cartoon", "illustration", "painting",
                  "ai_looking", "screen_capture"]


class VisionRow(BaseModel):
    row: int
    seen: str
    realism: Realism
    brand_or_franchise: bool = False
    subject_visible: bool
    forbidden_present: list[str]
    context_match: float
    quality: float
    best_frame: int
    # CONTEXTO_PROFUNDO_DO_ROTEIRO.md §6 (padrões para ler o cache antigo e os testes)
    era_consistent: bool = True
    anachronisms_seen: list[str] = []
    is_timeless: bool = False
    place_consistent: bool = True


class VisionSheet(BaseModel):
    candidates: list[VisionRow]


VISION_PROMPT = """You are a strict photo editor choosing footage for a documentary.

Video topic: "{topic}". Visual world: "{visual_world}".
Story so far at this point: "{beat}"
Scene narration: "{text}"
What this moment conveys: "{meaning}"
Main subject that MUST be clearly visible: "{subject}"
Must show: {must_show}
Must NOT show: {must_avoid}
Intended shot: {intent}
Allowed styles for this scene: {allowed_styles} ({style_reason})
Previous scene (avoid an identical shot): {previous}
{context}

The image is a contact sheet with {n} numbered rows (yellow number on the left); each row = {frames} frame(s)
from one candidate, in time order, left to right.
For EACH row, first describe what is actually visible, then judge:

- "seen": what is really in the frames (max 8 words, no guessing)
- "realism": one of "real_footage" | "cgi_3d" | "video_game" | "animation_cartoon" | "illustration" | "painting" | "ai_looking" | "screen_capture"
  ("ai_looking" = AI image with visible defects: deformed hands or faces, unreadable text, plastic look)
- "brand_or_franchise": true if a recognizable character, brand, logo or commercial game appears
- "subject_visible": true only if "{subject}" is clearly visible AND is the main focus
- "forbidden_present": list any "Must NOT show" items you can see (empty if none)
- "context_match": 0-10, how well it conveys what this moment means in the story (not just the words)
- "quality": 0-10 (focus, light, composition, no watermark/text/logos)
- "best_frame": 0-based index of the best frame in the row
- "era_consistent": true if clothing, buildings, objects and light fit the time period and place above
- "anachronisms_seen": out-of-period elements you can actually see (e.g. ["power lines", "modern jacket"]); empty if none
- "is_timeless": true if the frames show no era marker at all (nature, sky, fire, hands, textures in close-up)
- "place_consistent": false only if vegetation, architecture or climate clearly contradict the place above

Respond ONLY with JSON, no other text: {{"candidates":[{{"row":1,"seen":"...","realism":"...","brand_or_franchise":false,"subject_visible":true,"forbidden_present":[],"context_match":0,"quality":0,"best_frame":0,"era_consistent":true,"anachronisms_seen":[],"is_timeless":false,"place_consistent":true}}]}}
Include all {n} rows."""


def vision_context(ctx: dict | None) -> str:
    """Bloco de época e lugar do prompt de visão (§6). Vazio para cenas sem contexto (produções antigas)."""
    if not ctx:
        return ""
    place = ctx.get("place") or "unspecified"
    setting = ctx.get("setting_type")
    anach = ", ".join(ctx.get("anachronisms") or []) or "(none)"
    if setting == "historical":
        elements = ", ".join(x for x in [ctx.get("clothing"), ctx.get("architecture"),
                                         f"lighting from {ctx['lighting']}" if ctx.get("lighting") else "",
                                         ctx.get("transport")] if x)
        return (f'Time period of this scene: "{ctx.get("era")} ({ctx.get("era_label")})". Place: "{place}".\n'
                f"Period-appropriate elements: {elements or '(period-accurate)'}.\n"
                f"Anachronisms that must NOT appear: {anach}.")
    if setting == "timeless":
        return (f'Time period of this scene: timeless (no visible era markers). Place: "{place}".\n'
                f"Must NOT show clearly modern or clearly antique items: {anach}.")
    return (f'Time period of this scene: present day. Place: "{place}".\n'
            f"Elements that would look like another era or place: {anach}.")


def score_row(r: VisionRow | dict, allowance: str = "real_only", allowed_styles: list[str] | None = None,
              setting_type: str | None = None) -> float:
    """A IA descreve; a nota final é calculada aqui (AJUSTE_ESTILO_CONTEXTUAL.md §5).

    Época e lugar (CONTEXTO_PROFUNDO_DO_ROTEIRO.md §6) vêm antes das regras de estilo: em contexto histórico,
    qualquer anacronismo visto zera a nota; fora de época e sem ser atemporal, no máximo 2.
    """
    d = r.model_dump() if isinstance(r, BaseModel) else r
    realism = d["realism"]
    if d.get("brand_or_franchise") or realism in ("ai_looking", "screen_capture"):
        return 0.0
    score = 0.7 * float(d["context_match"]) + 0.3 * float(d["quality"])
    era_ok = d.get("era_consistent", True) is not False
    timeless = bool(d.get("is_timeless"))
    if setting_type == "historical":
        if d.get("anachronisms_seen"):
            return 0.0
        if not era_ok and not timeless:
            score = min(score, 2.0)
    elif setting_type == "timeless":
        if d.get("anachronisms_seen"):
            score = min(score, 2.0)
        elif not era_ok and not timeless:
            score = min(score, 4.0)
    elif setting_type == "contemporary" and not era_ok:
        score = min(score, 4.0)
    if d.get("place_consistent", True) is False:
        score = min(score, 3.0)
    if d["forbidden_present"]:
        score = min(score, 2.0)
    if not d["subject_visible"]:
        score = min(score, 3.0)
    allowed = set(allowed_styles or ["real_footage"])
    fits = bool(REALISM_TO_STYLES.get(realism, set()) & allowed)
    if allowance == "real_only":
        score = score if realism == "real_footage" else 0.0
    elif allowance == "real_preferred":
        score = score if realism == "real_footage" else score * (0.8 if fits else 0.5)
    else:  # stylized_ok
        score = score if fits else score * 0.6
    return round(max(0.0, min(10.0, score)), 2)


def style_of_realism(realism: str | None) -> str | None:
    """Estilo "canônico" do material escolhido, para continuidade entre cenas."""
    return {"real_footage": "real", "cgi_3d": "3d", "animation_cartoon": "animation", "illustration": "illustration",
            "painting": "painting", "video_game": "game"}.get(realism or "")


# ---------------------------------------------------------------- Etapa F: reescrita de queries


class QueryRewrite(BaseModel):
    queries: list[str]
    extra_must_avoid: list[str]


class QueryRewriteItem(BaseModel):
    scene_id: str
    queries: list[str]
    extra_must_avoid: list[str]


class QueryRewriteBatch(BaseModel):
    items: list[QueryRewriteItem]


REWRITE_SYSTEM = """You fix stock-footage searches for documentary scenes. For each scene, the previous searches
returned the wrong images; you get what was actually seen in the best rejected candidates. For EACH scene write
3 new English queries of 2 to 5 words that will find the right subject and avoid the observed mistakes. Every
query must contain the subject. No abstract words, brands, franchises or scientific names. Respect the allowed
styles. Also list extra short items to add to must_avoid (the wrong things that were seen), or an empty list.
Answer with one item per scene_id, JSON only."""


class OverlayFix(BaseModel):
    text: str


class OverlayFixItem(BaseModel):
    index: int
    text: str


class OverlayFixBatch(BaseModel):
    items: list[OverlayFixItem]


OVERLAY_FIX_SYSTEM = """You fix on-screen texts for a video. Each text must be rewritten in the target language
given in the request. Reuse the narration's own wording whenever possible, keep each text short (max 40
characters), keep numbers, and format numbers and units the way the target language does. Answer with one item
per index, JSON only."""
