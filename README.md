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

A API fica sob `/api` (ex.: `http://localhost:8000/api/health`); o `next dev` chama `http://localhost:8000/api`.

## Instalador para Windows

Um comando gera `dist\AiEditor-Setup-<versão>.exe` (PyInstaller + pywebview + Inno Setup, tudo gratuito):

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -EmbedGoogleClientFromKeyring
```

Nova versão: altere `VERSION` (ex.: `1.0.1`), rode o `build.ps1`, publique o setup e atualize o `latest.json`
(o app instalado avisa quando há versão nova). Detalhes: [EMPACOTAMENTO](installer/COMO_INSTALAR.md) para quem
instala; `aieditor.spec` e `installer\aieditor.iss` para o build. O app instalado guarda os dados em
`%LOCALAPPDATA%\AiEditor`; o modo de desenvolvimento continua usando `data\`.

Rodar o app de desktop a partir do código (janela própria + bandeja), sem empacotar:

```powershell
cd web; npm run build; cd ..\server
$env:AIEDITOR_SERVE_WEB = "1"; .venv\Scripts\python -m app.launcher
```

## Testes

```powershell
cd server
.venv\Scripts\python -m pip install -e .[dev]
.venv\Scripts\python -m pytest tests -q
```

Os testes usam banco temporário e provedores simulados (sem rede e sem gastar cota).

## Arquitetura

- `web/`: Next.js + Tailwind + shadcn (Base UI). Fala com a API por REST e acompanha produções por SSE.
- `server/app/api/`: rotas FastAPI. `server/app/worker/`: fila e orquestração (processo separado).
- `server/app/pipeline/`: uma etapa por arquivo; cada uma persiste sua saída em `data/jobs/<id>/` e é
  retomável (o retry pula as etapas concluídas).
- `server/app/providers/`: adapters por categoria (darkvi, stock, llm, storage, music...).
- `server/app/directions/`: direções como plugins (`manifest.json`, `prompt.md`, `overlays.py`).
- `data/` (fora do git): SQLite, settings.json, cache, jobs, músicas, efeitos sonoros (`sfx/`). Chaves ficam no Windows Credential Manager.

Artefatos de cada produção em `data/jobs/<id>/`: `audio/narration.wav`, `transcript.json`, `units.json`,
`plan.json`, `selection.json`, `assets/`, `timeline.json`, `subs.ass`, `render/`, `output/final.mp4`.

## Estado (Fase 1 + adiantamentos)

| Item | Estado |
|---|---|
| Canais/presets, wizard, fila, SSE, retry/cancelamento | pronto |
| TTS Darkvi, upload de áudio, loudnorm | pronto (formatos de resposta da Darkvi a confirmar) |
| Transcrição faster-whisper + alinhamento ao roteiro + fallback SRT | pronto |
| Planejamento com Claude (saída estruturada) + fallback determinístico | pronto |
| Interpretação do roteiro: Bíblia de Contexto com resumo, intenção, gênero e estrutura narrativa (raciocínio alto, 1 chamada por vídeo); cada cena declara o que transmite e quais personagens aparecem (aparência fixa) | pronto |
| Automático, sem campo na tela: idioma (detectado no roteiro), buscas (inglês), estilo visual e representação de época (decididos pela Bíblia) | pronto |
| Seleção econômica: cache 7 dias, filtro técnico, top 8 por texto, folha de miniaturas no Gemini (1–2 chamadas/cena) | pronto |
| YouTube CC com cota (fuso do Pacífico, 403 reativo) e fallback YouTube → bancos → fotos | pronto |
| Imagens IA via Darkvi com rate limiter 5/min e queda para bancos | pronto (adiantado da Fase 3) |
| Direção Clássico com crossfade de capítulo, destaques e títulos | pronto (adiantado da Fase 4) |
| Edição: transições por intenção (fade pelo preto, flash, dissolve), push-in em vídeo, moldura de foto, títulos animados (contador, máquina de escrever, letterbox), citação em tela cheia sincronizada, light leak, grão e vinheta | pronto |
| Efeitos sonoros: Freesound (CC0) com chave, sintetizados sem ela; pasta `data/sfx/<categoria>/` aceita sons próprios | pronto |
| Render x264 / AMF com fallback, legendas .ass, música da biblioteca com ducking | pronto |
| Upload resumable para o Drive via OAuth | pronto (não testado sem conta) |
| Vídeo de IA (fal.ai), ElevenLabs Music | próximas fases |
