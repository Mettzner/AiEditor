# Implementação das melhorias do AiEditor — relatório

Período: 2026-10-06 a 2026-10-07. Base: HEAD `7315321` (a referência `82caaa4` existe no histórico; o HEAD já tinha
avançado 52 commits). Trabalho em 20 commits na `main`, sem push. Objetivos: `Prompt_Claude_Code_AiEditor.md`.

## 1. Resultado dos checks

| Check | Antes (baseline) | Depois |
|---|---|---|
| `pytest tests -q` (server) | 178 passaram, 23 pulados | **305 passaram, 23 pulados** (os pulados são os testes `live`, que só rodam com `AIEDITOR_LIVE=1`) |
| `npx tsc --noEmit` (web) | limpo | limpo |
| `npm run lint` (web) | limpo | limpo |
| `npm run build` (export estático) | não executado no baseline | ok, 7 rotas estáticas, incluindo `/revisao` |
| Benchmark offline `python -m app.evaluation` | não existia | 7 casos, 0 falhas, todas as anotações atendidas (ver §5) |
| Tela de revisão no navegador | — | conferida com produção de exemplo em dados temporários (porta 8011): carga da API, filtros e "Fixar" funcionando |
| Empacotamento Windows (`build.ps1`, PyInstaller, Inno Setup) | — | **não executado** (só `test_packaging.py`, que passa) |

Nenhuma chamada paga, produção real, download do YouTube, publicação ou push foi feito. Caminhos externos foram
validados com simulações; os testes ficaram isolados da rede e do Credential Manager.

## 2. O que mudou, por fase

### Fase A — fundamentos
- **A1** A seleção não pula mais o YouTube antes do cache: o cache é lido primeiro e só a busca remota reserva cota;
  acerto, falta, vazio, erro e bloqueio de cota são contados à parte (`search_cache` no `timing_report.json`).
- **A2** Cota do YouTube por bucket, conforme a documentação oficial conferida em 2026-10-06
  (developers.google.com/youtube/v3/determine_quota_cost: search.list com bucket próprio de 100 chamadas/dia, demais
  endpoints em 10.000 unidades, reset à meia-noite do Pacífico). Reserva atômica por `UPDATE` condicional no SQLite
  (vale entre API e worker), reserva por tentativa enviada (retentativas e páginas incluídas), falha sem resposta
  contada como incerta, bloqueio reativo distinto para limite diário, limite por minuto e credencial. Regime antigo
  disponível (`legacy_units`). Migração 002 copia o saldo legado para o bucket `legacy` sem converter unidades e só
  estima (para cima) o dia corrente nos buckets novos.
- **A3** Cache de visão pela requisição efetiva: prompt renderizado (narração, intenção, must_show, must_avoid,
  contexto, cena anterior, estilos, papel visual), bytes da folha, identidade de cada linha, modelos configurados e
  versão do schema. Imagem gerada é identificada pelos bytes. Descrição observável (o que a IA viu) separada em
  `asset_description`, reaproveitada no ranking de texto sem herdar adequação.
- **A4** Single-flight (`app/singleflight.py`) para buscas e avaliações idênticas, entre threads e processos.
  Temporários exclusivos (`.part`/`.tmp`) e gravação atômica com nova tentativa quando o Windows trava o replace.
  Resultado vazio cacheado; erro transitório com TTL curto.
- **A5** Estados `validated`, `unvalidated`, `review_required`, `rejected` (`pipeline/validation.py`), com política
  configurável. Nota de texto nunca vira aprovação visual e não é comparada com nota de visão.
- **A6** Livro-razão de custos (`budget.Ledger`, tabela `cost_entry`): reserva antes de toda chamada paga
  (Bíblia, cenas, reescrita, overlays, visão paga) e reconciliação com o custo real; reservas órfãs liberadas na
  retomada. Preço com moeda, data e regime; modelo sem preço fica explícito (estimado pelo mais caro); modelo que
  de fato respondeu é registrado. Estimativa com faixa, custo por tarefa, hipóteses e aviso quando o teto pode cortar
  o planejamento.

### Fase B — interpretação semântica (sem chamada extra)
- Bíblia com entidades (id estável, aliases, nome científico, atributos ditos × inferidos, evidência), afirmações
  (alegações com modalidade e `needs_source`) e ambiguidades. Cenas com `entity_ids`, `claim_ids`, trecho de
  evidência, `visual_role`, `required_identity`, `must_not_imply`, `documentary_query`, `comparison`.
- Validação determinística (`pipeline/semantics.py`): negação vira "não sugerir", metáfora nunca é evidência exata,
  espécie com nome popular ambíguo fica pendente, trecho de evidência precisa existir na narração, gráfico só com
  fonte. Correções ficam em `unresolved` (nada é reparado em silêncio).
- Tempo narrado separado da viabilidade de filmagem: um fato de 2005 continua "passado" e o prompt de visão diz
  "2005 (passado recente)" em vez de "present day".
- Cena que atravessa contextos é dividida na fronteira, salvo comparação declarada; `context_id` do modelo só
  sobrescreve com comparação. Reconciliação final aponta contradições, entidades inexistentes, ambiguidades
  importantes e volta de contexto não declarada (avisos `SCRIPT_CONTRADICTION`, `INTERPRETATION_REVIEW`).
- Gráfico de barras determinístico para números citados com fonte (`pipeline/charts.py`).

### Fase C — pesquisa
- Plano de busca por bloco (contexto + entidade/assunto) com catálogo de candidatos compartilhado, consulta
  documental que preserva nome científico, ano e lugar, orçamento por importância; artefato `search_plan.json`.
- `youtube_results_per_query` (50) separado dos bancos e imune aos modos; paginação só com poucos candidatos úteis e
  folga de cota. Amostra para a visão diversa por canal.
- Alocação como preferência: banco sem resultado tenta o YouTube antes da IA; cena de evidência exata compara as
  fontes reais na mesma escala (uma comparação por cena). O relatório mostra meta e alcançado do YouTube no
  material real.
- Ranking híbrido: lexical sempre; sem nenhum nome da identidade exigida a nota é limitada (qualidade não compensa);
  ranking semântico opcional (`providers/embeddings.py`), desligado e sem instalação automática.

### Fase D — segmentos e procedência
- **Decisão do usuário (2026-10-07):** YouTube configurável, padrão "referência". No modo referência os vídeos
  achados viram pesquisa documental por cena (`references.json`) e só entram no render com arquivo autorizado
  associado (tabela `authorized_media`, API `/api/authorized-media`, tela em Configuração). O download por yt-dlp
  continua como opção explícita, com aviso, e cada trecho é marcado no manifesto como obtenção não autorizada pela
  plataforma.
- Índice temporal de arquivos autorizados (`pipeline/media_index.py`): cortes do FFmpeg ou amostragem espaçada,
  frames com timestamp medido, aprofundamento só perto do ponto promissor, corte conferido pelo ffprobe.
- Conferência do trecho final: ffprobe sempre (falha → próxima opção); visão nos frames do intervalo usado só nas
  cenas exatas/de importância alta; reprovado → próxima opção (`SEGMENT_REJECTED`); sem como conferir →
  `unverified` (cena exata vai para revisão).
- Uso por segmento (`select/segments.py`): trechos distintos do mesmo vídeo longo, sem sobreposição, com intervalo
  mínimo e limites por vídeo e por canal (canal só para YouTube); vídeos únicos e segmentos contados à parte.
- Manifesto de procedência e créditos prontos para publicação (`pipeline/provenance.py`), com aviso de IA e
  afirmações sem fonte verificada (nada inventado).

### Fase E — economia e retomada
- Mídia autorizada local consultada antes de qualquer API; reaproveitamento opcional e limitado de imagem gerada.
- Batch persistente (`batch_job`): id do lote, custom_id, hash da requisição, resultado e uso; a produção fica
  "aguardando lote" e o worker a retoma no horário agendado, sem laço ocupado; prazo vencido cancela o lote e segue
  sem desconto; cancelar a produção cancela o lote; resultado inválido do lote não é cobrado de novo.
- Artefatos com hashes de dependência (`app/artifacts.py`, `artifacts.json`): a retomada confere saídas e entradas;
  só o que depende da mudança é refeito. Seleção descarta só as cenas que mudaram no plano (escolha fixada fica);
  render refaz só o intermediário da cena alterada e a transição seguinte.

### Fase F — interface e robustez
- Revisão integrada (`/revisao`, `api/review.py`): leitura completa, filtros, métricas por fonte/cache/custo,
  trocar/fixar/editar trecho/buscar mais/corrigir interpretação, entidade e idioma, render parcial, manifesto e
  créditos exportáveis, preview servido só de dentro da pasta da produção.
- Upload em streaming com limites e ffprobe antes de a produção existir; estado `preparing` (o worker só pega
  `queued`); `preparing` órfão vira falha. Settings e preços com validação tipada (422). Erro do OAuth escapado.
  Host e Origin externos recusados (proteção contra DNS rebinding e CSRF local).
- Download com limite, cancelamento, temporário exclusivo, esquema http(s) e destino dentro das pastas do app.
- Cancelamento alcança ffmpeg, downloads e yt-dlp no meio; histórico paginado; SSE incremental pelo índice de
  `updated_at`; limpeza segura de intermediários; backup do banco antes de migrar; versão única.
- Correção de bug antigo: laço infinito em `Selector.finish` quando todos os downloads falhavam.

## 3. Matriz de requisitos

Estados: existente · implementado · validado (teste automatizado) · parcial · adiado. Evidência = arquivo de teste.

| Req. | Estado | Evidência |
|---|---|---|
| A1 cache antes da cota | validado | `test_quota_cache.py::test_cota_zerada_com_cache_valido_nao_busca`, `test_selector.py::test_3b_...` |
| A2 buckets, reserva atômica, retries, páginas, dia, reativo | validado | `test_quota_cache.py` (11 testes, inclui 4 processos concorrentes e migração) |
| A3 cache de visão por requisição/bytes, descrição separada | validado | `test_vision_cache.py` (14 testes) |
| A4 single-flight e temporários | validado | `test_quota_cache.py::test_buscas_identicas...`, `test_vision_cache.py::test_avaliacoes...`, `test_operations.py` |
| A5 estados de validação | validado | `test_selector.py` (5 testes de validação), `test_provenance.py` |
| A6 orçamento, preços, estimativa | validado | `test_budget.py` (7 testes novos) |
| B1 entidades e afirmações | validado | `test_semantics.py` |
| B2 papel visual por cena | validado | `test_semantics.py`, benchmark |
| B3 contexto × direção, tempo narrado, gráficos | validado | `test_semantics.py::test_passado_recente...`, `test_cena_que_atravessa...`, `test_grafico...` |
| B4 roteiros longos, reconciliação | parcial | reconciliação e janelas validadas (`documentario_longo`); **escalada seletiva de modelo adiada** (ambiguidades vão para revisão) |
| B5 plantas, pessoas, eventos | validado | `test_semantics.py::test_especie_ambigua...`, benchmark `plantas_semelhantes`, `pessoa_real_curie` |
| C1 plano de busca por bloco | validado | `test_search_plan.py::test_plano_agrupa...`, `test_artefato_do_plano_de_busca` |
| C2 50 por página, paginação adaptativa, amostra diversa | validado | `test_search_plan.py` |
| C3 alocação como preferência, comparação entre fontes | validado | `test_search_plan.py::test_cena_de_banco...`, `test_cena_exata_compara...` |
| C4 consultas específicas | validado | `test_search_plan.py::test_consulta_documental...` |
| C5 ranking híbrido | parcial | identidade e fallback validados; **ranking semântico não testado com a biblioteca real** (não instalada; teste com simulação) |
| D1 pesquisa × mídia reutilizável | validado | `test_provenance.py` (modo referência, arquivo autorizado, download_cc marcado, API) |
| D2 índice temporal | parcial | cortes, frames medidos, refino validados com FFmpeg real; **transcrição local como pista não implementada**; descrição por segmento só quando a visão avalia |
| D3 confirmação do segmento | validado | `test_provenance.py::test_miniatura_certa_e_trecho_errado...`, `test_sem_conferencia...` |
| D4 uso por segmento | validado | `test_provenance.py` (3 testes) |
| D5 manifesto e claims | validado | `test_provenance.py::test_manifesto_e_creditos`, `test_review.py::test_manifesto...` |
| E1 catálogo e cache de geração | validado | `test_provenance.py::test_catalogo_local...`, `test_reaproveitamento...` |
| E2 modelos por tarefa, prefixo, modelo efetivo | existente + implementado | modelo efetivo/fallback registrado (`test_budget.py::test_preco_desconhecido...`); **comparação medida de aquecer janela adiada** (sem chamadas reais) |
| E3 Batch persistente | parcial | `test_resume.py` (reinício, prazo, espera fora do worker, cancelamento); **um lote por pedido (janelas não agrupadas num lote só); Batch do Gemini para visão não implementado** |
| E4 invalidação e retomada | validado | `test_resume.py` (5 testes) |
| F revisão integrada | validado | `test_review.py` (11 testes, inclui contrato com `web/lib/api.ts`) + conferência no navegador |
| F upload, preparing, validação, escape, origem | validado | `test_operations.py` |
| F download, cancelamento, histórico, SSE, disco, backup, versão | validado | `test_operations.py`, `test_review.py::test_historico...`; cancelamento do ffmpeg sem teste automatizado próprio |
| Migrações e leitura de dados legados | validado | `test_resume.py::test_presets_producoes_e_banco_legados...`, `test_quota_cache.py::test_migracao...`, `test_packaging.py` |

Casos mínimos da seção 11: 1 `test_quota_cache`/`test_selector::test_3b` · 2 `test_quota_cache::test_buscas_identicas`
· 3 `test_quota_cache` · 4 e 5 `test_vision_cache` · 6 `test_provenance::test_miniatura_certa...` · 7
`test_provenance::test_video_longo...` · 8 `test_semantics` · 9 `test_semantics::test_passado_recente...` · 10
`test_semantics::test_cena_que_atravessa...`/`test_contexto_pedido...` · 11 `test_selector::test_sem_visao...` · 12
`test_resume::test_reinicio...` · 13 `test_budget::test_teto_impede...` · 14 `test_operations::test_upload...` · 15
`test_resume::test_mudanca_invalida...` · 16 `test_resume::test_presets...` · 17 `test_review::test_contrato...` · 18
`test_provenance::test_corte_do_trecho...` (vídeo sintético gerado pelo FFmpeg).

## 4. Decisões

- **YouTube em modo referência por padrão** (escolha do usuário): a licença CC não substitui a autorização de obtenção
  da plataforma. Efeito: sem mídia autorizada associada, o render usa as outras fontes; o yt-dlp segue disponível.
- Cota: regime oficial por bucket como padrão; limites editáveis porque o projeto pode ter cota aprovada diferente.
- Teto de gasto passou a valer para todas as chamadas pagas, com reserva do pior caso (saída = max_tokens). Em
  roteiros longos isso pode acionar o caminho determinístico: a estimativa avisa antes.
- `PROMPT_VERSION` v2→v3 e chave nova do cache de visão: respostas e notas antigas não são reaproveitadas (custo
  único em retentativas de produções antigas), para nunca herdar avaliação de outra requisição.
- Interpretação semântica sem chamada extra: os campos novos vão nos mesmos esquemas da Bíblia e das cenas.
- Gráficos só determinísticos e só com fonte.

## 5. Benchmark (offline)

`python -m app.evaluation` — 7 casos, 0 falhas. Papel visual 7/7, identidade 7/7, tempo narrado 13/13, "não sugerir"
3/3, entidades 9/9 (somados), nenhuma cena atravessando contexto, contradições e blocos de contexto como anotados,
precisão@1 do ranking 1,0 nos 4 casos com candidatos, cobertura do YouTube 1,0, custo previsto US$ 0,35–1,53/min em
roteiros curtos e US$ 0,04–0,13/min no documentário longo de 300 unidades (8 chamadas de LLM previstas).
**Esses números medem a lógica do app sobre respostas gravadas; não são medição de qualidade de um modelo real nem
comparação de melhoria.** O baseline não roda este benchmark (os campos avaliados não existiam), então não há
comparação antes/depois medida. A avaliação real (`--live --budget`) está pronta e não foi executada.

## 6. Limitações e pendências concretas

1. Nenhuma chamada real foi feita. **Risco a conferir primeiro:** os esquemas de saída estruturada da Bíblia e das
   cenas cresceram (entidades, afirmações, campos por cena); a API pode recusar uma gramática grande demais. Rodar
   uma produção curta ou `python -m app.evaluation --live --budget 0.50` e observar.
2. Batch: um lote por pedido (as janelas do planejamento não são agrupadas num único lote); Batch do Gemini para
   visão não implementado.
3. Escalada seletiva de modelo para ambiguidades importantes: não implementada (vão para revisão).
4. Índice de arquivos autorizados sem transcrição local como pista de localização.
5. Ranking semântico: caminho opcional sem teste com `sentence-transformers` real.
6. Cancelamento do ffmpeg/yt-dlp sem teste automatizado próprio (o caminho do download tem).
7. Empacotamento Windows (`build.ps1`) não executado; os módulos novos são importados estaticamente, mas o
   instalador não foi gerado nem testado.
8. A tela de revisão mostra miniaturas só das alternativas com URL pública (frames locais de mídia autorizada não
   aparecem como miniatura).
9. Proteção local por Host/Origin; não há token de sessão (app local, loopback).
10. Durante a investigação de um teste travado usei `taskkill /F /IM python.exe`, que pode ter encerrado outros
    processos Python abertos na máquina (por exemplo, a API/worker do AiEditor em uso). Se algo seu parou nesse
    momento, basta reabrir.

## 7. Checkpoint

Fase atual: concluída a implementação viável das fases A–F, dataset e benchmark. Próximo passo exato: validar com
chamadas reais e orçamento pequeno (item 1 de §6) e decidir se o perfil documental deve virar padrão dos canais.
