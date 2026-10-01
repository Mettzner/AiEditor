# AiEditor

Ferramenta local que recebe um roteiro (e opcionalmente a narração) e devolve um vídeo 16:9/1080p montado
e renderizado, com clipes de bancos, imagens de IA, legendas e música, enviado ao Google Drive.

## Requisitos

- Windows, Python 3.12+ e Node 20+
- **FFmpeg completo** no PATH (ex.: `winget install Gyan.FFmpeg`) ou a pasta configurada em Configuração → Render.
  Precisa de libx264, libass e (para o modo rápido) h264_amf. FFmpeg 7 ou mais novo.

## Instalação

```powershell
cd server
python -m venv .venv
.venv\Scripts\python -m pip install -e .
cd ..\web
npm install
```

## Rodando

```powershell
.\start.ps1          # API :8000, worker e frontend :3000 em janelas separadas
```

Ou manualmente, em três terminais:

```powershell
cd server; .venv\Scripts\python -m uvicorn app.main:app --port 8000 --reload
cd server; .venv\Scripts\python -m app.worker
cd web;    npm run dev
```

Primeiros passos: **Configuração** (chaves Darkvi, Anthropic, Pexels/Pixabay; testar FFmpeg) → **Canais**
(criar um canal com voz TTS) → **Criação**.

## Arquitetura

- `web/`: Next.js + Tailwind + shadcn (Base UI). Fala com a API por REST e acompanha produções por SSE.
- `server/app/api/`: rotas FastAPI. `server/app/worker/`: fila e orquestração (processo separado).
- `server/app/pipeline/`: uma etapa por arquivo; cada uma persiste sua saída em `data/jobs/<id>/` e é
  retomável (o retry pula as etapas concluídas).
- `server/app/providers/`: adapters por categoria (darkvi, stock, llm, storage, music...).
- `server/app/directions/`: direções como plugins (`manifest.json`, `prompt.md`, `overlays.py`).
- `data/` (fora do git): SQLite, settings.json, cache, jobs, músicas. Chaves ficam no Windows Credential Manager.

Artefatos de cada produção em `data/jobs/<id>/`: `audio/narration.wav`, `transcript.json`, `units.json`,
`plan.json`, `selection.json`, `assets/`, `timeline.json`, `subs.ass`, `render/`, `output/final.mp4`.

## Estado (Fase 1 + adiantamentos)

| Item | Estado |
|---|---|
| Canais/presets, wizard, fila, SSE, retry/cancelamento | pronto |
| TTS Darkvi, upload de áudio, loudnorm | pronto (formatos de resposta da Darkvi a confirmar) |
| Transcrição faster-whisper + alinhamento ao roteiro + fallback SRT | pronto |
| Planejamento com Claude (saída estruturada) + fallback determinístico | pronto |
| Pexels + Pixabay, filtro técnico + pré-ranking textual | pronto (funil de visão: Fase 2) |
| Imagens IA via Darkvi com rate limiter 5/min e queda para bancos | pronto (adiantado da Fase 3) |
| Direção Clássico com crossfade de capítulo, destaques e títulos | pronto (adiantado da Fase 4) |
| Render x264 / AMF com fallback, legendas .ass, música da biblioteca com ducking | pronto |
| Upload resumable para o Drive via OAuth | pronto (não testado sem conta) |
| YouTube CC, vídeo de IA (fal.ai), ElevenLabs Music, funil de visão | próximas fases |
