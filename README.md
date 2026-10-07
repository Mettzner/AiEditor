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

Os testes usam banco temporário, provedores simulados, rede bloqueada no cliente HTTP comum e um Credential Manager
vazio (as chaves reais ficam invisíveis): nada é chamado de verdade e nenhuma cota é gasta. Os testes com FFmpeg geram
vídeos sintéticos locais e são pulados se o FFmpeg não estiver instalado.

### Benchmark de interpretação e pesquisa

```powershell
cd server
.venv\Scripts\python -m app.evaluation --out eval\results\offline.json   # offline: sem rede, sem custo
```

Sete casos anotados em `server/eval/cases/` (italiano, plantas semelhantes, evento histórico, pessoa real, flashback,
comparação, documentário longo). O modo offline roda o pós-processamento real do app sobre respostas gravadas do
modelo: mede a lógica, não a qualidade de um modelo real. A avaliação real é opt-in e tem teto:
`set AIEDITOR_LIVE=1` e `python -m app.evaluation --live --budget 1.00`.

## Arquitetura

- `web/`: Next.js + Tailwind + shadcn (Base UI). Fala com a API por REST e acompanha produções por SSE.
- `server/app/api/`: rotas FastAPI. `server/app/worker/`: fila e orquestração (processo separado).
- `server/app/pipeline/`: uma etapa por arquivo; cada uma persiste sua saída em `data/jobs/<id>/` e é
  retomável (o retry pula as etapas concluídas).
- `server/app/providers/`: adapters por categoria (darkvi, stock, llm, storage, music...).
- `server/app/directions/`: direções como plugins (`manifest.json`, `prompt.md`, `overlays.py`).
- `data/` (fora do git): SQLite, settings.json, cache, jobs, músicas, efeitos sonoros (`sfx/`). Chaves ficam no Windows Credential Manager.

Artefatos de cada produção em `data/jobs/<id>/`: `audio/narration.wav`, `transcript.json`, `units.json`,
`context_bible.json`, `plan.json`, `selection.json`, `search_plan.json`, `references.json`, `assets/`,
`timeline.json`, `subs.ass`, `render/`, `artifacts.json` (hashes de dependência de cada etapa),
`llm_usage.json`, `output/final.mp4`, `output/manifest.json` (procedência), `output/creditos.txt`,
`output/direcao.json` e `output/visual_report.json`.

## Configuração e perfis

Tudo editável em Configuração (validado pela API; valor fora da faixa devolve 422 com a lista de erros).

| Chave (`settings.json`) | Padrão | O que faz |
|---|---|---|
| `youtube.ingest_mode` | `reference` | `reference`: o YouTube é pesquisa documental (links, canal, data) e só entra no render com arquivo autorizado associado em Configuração → Mídia autorizada. `download_cc`: baixa trechos CC com yt-dlp (não é fluxo autorizado pela plataforma; marcado no manifesto). |
| `youtube.quota_accounting_mode` | `separate_buckets` | Cota por bucket (search.list: 100 chamadas/dia; demais: 10.000 unidades). `legacy_units` volta ao saldo único antigo. |
| `youtube.buckets.*.daily_limit/reserve` | 100/5 e 10.000/200 | Ajuste se o Google aprovou outra cota para o seu projeto. |
| `selection.youtube_results_per_query` | 50 | Resultados por página do YouTube (1 chamada de busca), sem interferência dos modos rápido/preciso. |
| `selection.max_youtube_pages_per_group` | 2 | Páginas extras só com poucos candidatos úteis e folga de cota (`youtube_min_useful`, `youtube_page_reserve`). |
| `selection.allow_youtube_fallback_from_stock` | ligado | Cena de banco sem resultado bom tenta o YouTube antes da IA. |
| `selection.compare_sources_for_exact` | ligado | Cena de evidência exata compara as fontes reais na mesma escala (uma comparação por cena). |
| `selection.validation_policy` | `strict_for_exact_identity` | Pessoa/evento/espécie exata sem confirmação fica "revisar"; `lenient` deixa o material ilustrativo seguir com aviso. |
| `selection.verify_final_segment` | ligado | Confere o trecho usado (ffprobe sempre; visão nos frames do intervalo só em cenas exatas). |
| `selection.max_segments_per_video` / `max_clips_per_channel` / `segment_min_gap_seconds` | 2 / 4 / 30 | Trechos distintos do mesmo vídeo longo, com limites de repetição. |
| `selection.search_cache_ttl_hours` | 168 (acervos 336) | Validade do cache de busca por provedor; vencidos são apagados na partida do worker. |
| `budget.max_usd_per_production` | 2,00 | Teto de IA (Bíblia, cenas, reescrita, overlays, visão paga). Reserva antes de cada chamada; ao estourar, a produção segue pelo caminho determinístico. 0 = sem teto. |
| `ranking.semantic` | desligado | Ranking semântico local (exige `pip install sentence-transformers`); sem ele, ranking lexical. |
| `ai_images.reuse_cache` | desligado | Reaproveitar imagem gerada com o mesmo prompt/modelo/referência/estilo (confira os termos do provedor). |
| `upload.max_mb` / `max_minutes` / `max_video_mb` | 500 / 240 / 4096 | Limites do upload da narração e da mídia autorizada. |
| `folders.auto_cleanup` | desligado | Apaga os intermediários do render depois do envio (vídeo, manifesto e decisões ficam). |

Perfis sugeridos: **documental** (`validation_policy` estrita, `compare_sources_for_exact` e `verify_final_segment`
ligados, `ingest_mode` reference + mídia autorizada); **econômico** (modo rápido, `max_youtube_pages_per_group` 1,
teto baixo, `llm_economy` na Criação para usar a Batch API); **ilustrativo** (`validation_policy` lenient).

## Revisão

Cada produção concluída tem **Revisar** (`/revisao/?id=<id>`): narração, intenção, entidades, papel visual, origem
e estado de validação por cena; preview do trecho; alternativas com nota e motivo; referências do YouTube; filtros
(revisar, identidade incerta, sem visão, nota baixa, afirmação sem fonte); métricas por fonte, cache e custo. Ações:
trocar pela alternativa, fixar, editar o trecho, buscar mais (dentro do teto), corrigir assunto, busca documental,
entidade, papel visual e idioma, e **Renderizar de novo** — só as etapas e cenas afetadas são refeitas.

## Estado (Fase 1 + adiantamentos)

| Item | Estado |
|---|---|
| Canais/presets, wizard, fila, SSE, retry/cancelamento | pronto |
| TTS Darkvi, upload de áudio, loudnorm | pronto (formatos de resposta da Darkvi a confirmar) |
| Transcrição faster-whisper + alinhamento ao roteiro + fallback SRT | pronto |
| Planejamento com Claude (saída estruturada) + fallback determinístico | pronto |
| Interpretação do roteiro: Bíblia de Contexto com resumo, intenção, gênero e estrutura narrativa (raciocínio alto, 1 chamada por vídeo); cada cena declara o que transmite e quais personagens aparecem (aparência fixa) | pronto |
| Idioma do vídeo selecionável (26 idiomas de escrita latina ou cirílica), pré-preenchido com o detectado no roteiro; narrador, legendas e textos na tela seguem ele | pronto |
| Automático, sem campo na tela: buscas (inglês), estilo visual e representação de época (decididos pela Bíblia) | pronto |
| Seleção econômica: cache por provedor (lido antes da cota, single-flight), filtro técnico, ranking com identidade e diversidade, folha de miniaturas no Gemini (1–2 chamadas/cena) | pronto |
| Visão reserva: sem cota do Gemini (ou com erro), o Claude avalia a folha de miniaturas e as imagens geradas; a escolha às cegas só acontece sem nenhuma das duas | pronto |
| Prompt de imagem guiado pelo roteiro: abre com a frase do plano (visual_intent), traz o que o momento transmite, o estilo visual do vídeo e a aparência fixa dos personagens | pronto |
| YouTube primeiro como pesquisa (modo referência) ou download CC explícito; cota por bucket com reserva atômica; 50 resultados por página e paginação adaptativa; vídeos verticais/Shorts descartados | pronto |
| Interpretação tipada: entidades, afirmações (alegações com fonte pendente), papel visual, identidade exigida, tempo narrado, divisão por contexto, reconciliação | pronto |
| Estados de validação (validada, não validada, revisar, rejeitada), conferência do trecho final e uso por segmento | pronto |
| Teto de gasto com reserva/reconciliação em todas as chamadas pagas; estimativa com faixa e hipóteses | pronto |
| Batch API persistente (sem ocupar o worker), retomada por hashes de dependência, render parcial por cena | pronto |
| Manifesto de procedência, créditos prontos para publicação, mídia autorizada, revisão integrada | pronto |
| Ritmo: tempo em tela perto da média pedida, teto de média × 1,5 por clipe (cenas longas viram tomadas com clipes diferentes, fragmentos curtos são juntados) | pronto |
| Imagens IA via Darkvi com rate limiter 5/min e queda para bancos | pronto (adiantado da Fase 3) |
| Direção Clássico com crossfade de capítulo, destaques e títulos | pronto (adiantado da Fase 4) |
| Edição: transições por intenção (fade pelo preto, flash, dissolve), push-in em vídeo, moldura de foto, títulos animados (contador, máquina de escrever, letterbox), citação em tela cheia sincronizada, light leak, grão e vinheta | pronto |
| Efeitos sonoros: Freesound (CC0) com chave, sintetizados sem ela; pasta `data/sfx/<categoria>/` aceita sons próprios | pronto |
| Render x264 / AMF com fallback, legendas .ass, música da biblioteca com ducking | pronto |
| Upload resumable para o Drive via OAuth | pronto (não testado sem conta) |
| Vídeo de IA (fal.ai), ElevenLabs Music | próximas fases |
