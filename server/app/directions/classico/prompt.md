## Direction: Classic

- Sober documentary editing with polished, cinematic finishing. Hard cuts for most of the video; the
  renderer adds the transitions, camera moves, animated titles and subtle sound design from the fields below.
- Set `chapter_break = true` only on the first scene of a new section or chapter of the script (a clear
  change of subject, time or place). Expect one every 1–3 minutes. Those scenes get a dip-to-black transition
  and an animated chapter title in `chapter_title` (max 40 characters, in the video language, reusing the
  script's words).
- Scene duration varies ±30% around the requested average. Hooks, reveals and tense passages are shorter;
  descriptive or contemplative passages are longer.
- `highlight`: fill only when the scene mentions a number, name, place or date worth showing on screen
  (e.g. "1987 — Ohio", "47 witnesses", "$200 Billion a Year"). Short text, max 40 characters, in the video
  language, taken from the narration's own words. Quantities are animated counting up, so keep the number
  as digits. Leave it null in most scenes: at most one highlight every 20–30 seconds.
- `emphasis`, the narrative weight of the scene (drives camera moves, transitions and sound design):
  * "reveal": the exact scene where a twist, discovery or key fact lands (a flash cut and an impact sound).
    Rare: at most one every 45–60 seconds, never two in a row.
  * "tension": suspense builds, danger approaches, something is about to happen (slow push-in).
  * "calm": contemplative, descriptive or emotional passages (soft dissolves, slow drifts).
  * "none": everything else. Most scenes are "none".
- `quote`: a short, powerful sentence copied VERBATIM from this scene's narration (4–14 words, same
  language, no paraphrase) to show full screen, word by word as it is spoken. Only for the most memorable
  lines: a chilling statement, a final verdict, a famous phrase. At most one every 90 seconds; null in
  almost every scene. A scene with a quote gets no highlight.
- No stickers, emojis or cartoon sound effects.
