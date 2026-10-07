# Plano de evolução do AiEditor (checkpoint persistente)

Fonte dos objetivos: `Prompt_Claude_Code_AiEditor.md`. Fonte de verdade: o código no HEAD.
Relatório final: [IMPLEMENTACAO_MELHORIAS_AIEDITOR.md](IMPLEMENTACAO_MELHORIAS_AIEDITOR.md).

## Baseline (2026-10-06, HEAD 7315321)

- `pytest tests -q`: 178 passaram, 23 pulados (testes `live`, só com `AIEDITOR_LIVE=1`).
- `npx tsc --noEmit` e `npm run lint` (web): limpos.
- Árvore limpa; 52 commits; a referência anterior `82caaa4` existe no histórico.

## Inventário do que já existia (não duplicar)

| Recurso | Onde |
|---|---|
| Bíblia de Contexto (blocos de época/lugar, beats, entidades recorrentes) | `pipeline/context.py`, `plan.py` |
| Cache local de respostas do LLM (hash de etapa+modelo+prompt+entrada) | `providers/llm/anthropic.py` (`LlmCache`) |
| Cache de buscas (7 dias) e de visão | `pipeline/select/search.py`, `funnel.py` |
| Batch API (bloqueante, sem persistência) | `anthropic.py::_run_batch` |
| Teto de gasto só para visão paga | `budget.py::VisionBudget` |
| Cota do YouTube em saldo único (busca=100, detalhes=1) | `providers/youtube/quota.py` |
| Relatório de direção | `pipeline/report.py`, `api/productions.py` |
| Migrações versionadas | `migrations.py` |

## Fases e estado

Estados: **existente**, **implementado**, **validado** (teste automatizado passando), **bloqueado**, **adiado**.
A matriz detalhada, com evidências, fica no relatório final.

1. Fase A — fundamentos (cache antes da cota, buckets de cota, cache de visão, single-flight, estados de
   validação, orçamento com reserva).
2. Fase F (operação crítica) — upload `preparing`, escape de HTML, validação de settings.
3. Fase B — interpretação semântica tipada (entidades, afirmações, papel visual, tempo narrativo).
4. Fase C — pesquisa (resultados por query do YouTube, paginação, fallback banco → YouTube, consultas).
5. Fase D — segmentos e procedência (uso por segmento, manifesto, referências não incorporáveis).
6. Fase E — economia (Batch persistente, hashes de dependência, TTL de cache por provedor).
7. Fase F — revisão integrada (API + tela), métricas e exportação.
8. Avaliação offline (dataset + benchmark) e relatório.

## Estado em 2026-10-07

Fases A–F, dataset e benchmark implementados e testados (305 testes passando, 23 `live` pulados; tsc, lint e build
do frontend limpos). Matriz de requisitos com evidências, decisões e pendências: ver o relatório final.

## Próximo passo

Validar com chamadas reais e orçamento pequeno (esquemas de saída estruturada maiores) — seção "Limitações" e
"Checkpoint" do relatório final.
