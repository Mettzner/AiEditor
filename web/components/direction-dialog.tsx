"use client";

import { useState } from "react";
import { Clapperboard, Copy, Download, ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, type DirectionReport } from "@/lib/api";
import { cn } from "@/lib/utils";

const SOURCE: Record<string, string> = {
  stock: "Banco",
  stock_photo: "Foto (banco)",
  youtube: "YouTube",
  ai: "IA",
  ai_image: "IA imagem",
  ai_video: "IA vídeo",
  missing: "sem asset",
  migrate_ai: "→ IA",
};

function Pct({ label, meta, final }: { label: string; meta?: number; final?: number }) {
  return (
    <div className="rounded-md border px-3 py-2">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="tabular-nums">
        {final ?? 0}% <span className="text-xs text-muted-foreground">meta {meta ?? 0}%</span>
      </p>
    </div>
  );
}

/** Relatório de direção da produção: o que o planejamento pediu e o que o render usou, cena a cena. */
export function DirectionDialog({ productionId, title }: { productionId: number; title: string }) {
  const [report, setReport] = useState<DirectionReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load(open: boolean) {
    if (!open) return;
    setError(null);
    api
      .direction(productionId)
      .then(setReport)
      .catch((e) => setError((e as Error).message));
  }

  const json = report ? JSON.stringify(report, null, 2) : "";

  function download() {
    const url = URL.createObjectURL(new Blob([json], { type: "application/json" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: `direcao-${productionId}.json` });
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <Dialog onOpenChange={load}>
      <DialogTrigger render={<Button variant="outline" size="sm" />}>
        <Clapperboard /> Direção
      </DialogTrigger>
      <DialogContent className="flex max-h-[88vh] flex-col gap-4 p-6 sm:max-w-6xl">
        <DialogHeader>
          <DialogTitle>Direção · {title}</DialogTitle>
        </DialogHeader>
        {error && <p className="text-sm text-red-300">{error}</p>}
        {!report && !error && <p className="text-sm text-muted-foreground">Carregando…</p>}
        {report && (
          <Tabs defaultValue="cenas" className="min-h-0 flex-1">
            <div className="flex items-center justify-between gap-2">
              <TabsList>
                <TabsTrigger value="cenas">Cenas</TabsTrigger>
                <TabsTrigger value="visual">Visual</TabsTrigger>
                <TabsTrigger value="json">JSON</TabsTrigger>
              </TabsList>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => navigator.clipboard.writeText(json).then(() => toast.success("JSON copiado"))}
                >
                  <Copy /> Copiar JSON
                </Button>
                <Button variant="outline" size="sm" onClick={download}>
                  <Download /> Baixar
                </Button>
              </div>
            </div>

            <TabsContent value="cenas" className="mt-3 flex min-h-0 flex-col gap-4 overflow-y-auto pr-1">
              <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-6">
                <div className="rounded-md border px-3 py-2">
                  <p className="text-xs text-muted-foreground">Cenas</p>
                  <p className="tabular-nums">
                    {report.resumo.cenas} · {report.resumo.duracao_total}s
                  </p>
                </div>
                <div className="rounded-md border px-3 py-2">
                  <p className="text-xs text-muted-foreground">Duração por cena</p>
                  <p className="tabular-nums">
                    {report.resumo.duracao_cena.media}s{" "}
                    <span className="text-xs text-muted-foreground">
                      ({report.resumo.duracao_cena.min}–{report.resumo.duracao_cena.max})
                    </span>
                  </p>
                </div>
                <Pct
                  label="Bancos"
                  meta={report.resumo.composicao_meta_pct.stock}
                  final={report.resumo.composicao_final_pct.stock}
                />
                <Pct
                  label="YouTube"
                  meta={report.resumo.composicao_meta_pct.youtube}
                  final={report.resumo.composicao_final_pct.youtube}
                />
                <Pct label="IA" meta={report.resumo.composicao_meta_pct.ai} final={report.resumo.composicao_final_pct.ai} />
                <div className="rounded-md border px-3 py-2">
                  <p className="text-xs text-muted-foreground">Transcrição</p>
                  <p className={cn("tabular-nums", (report.resumo.transcricao.divergencia ?? 0) > 0.15 && "text-amber-300")}>
                    {Math.round((report.resumo.transcricao.divergencia ?? 0) * 100)}% divergência
                  </p>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                Direção {report.config.direcao} · clima musical “{report.resumo.clima_musical ?? "—"}” ·{" "}
                {report.resumo.chamadas_visao ?? 0} chamada(s) de visão · {report.resumo.buscas ?? 0} busca(s) ·{" "}
                {report.resumo.crossfades} crossfade(s) · {report.resumo.overlays} overlay(s) · estilo visual “
                {report.config.estilo_visual || "—"}”
              </p>

              <div className="overflow-x-auto rounded-md border">
                <table className="w-full text-left text-xs">
                  <thead className="bg-muted/50 text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2">Cena</th>
                      <th className="px-3 py-2">Narração</th>
                      <th className="px-3 py-2">Intenção visual · buscas</th>
                      <th className="px-3 py-2">Fonte</th>
                      <th className="px-3 py-2">Asset escolhido</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.cenas.map((s) => (
                      <tr key={s.id} className="border-t align-top">
                        <td className="px-3 py-2 whitespace-nowrap">
                          <p className="font-mono">{s.id}</p>
                          <p className="text-muted-foreground">{s.tempo}</p>
                          <p className="text-muted-foreground">
                            {s.duracao}s · {s.plano.energy}
                          </p>
                          {s.plano.chapter_break && <p className="text-sky-300">capítulo: {s.plano.chapter_title}</p>}
                          {s.plano.highlight && <p className="text-[#ff8a93]">destaque: {s.plano.highlight}</p>}
                        </td>
                        <td className="max-w-56 px-3 py-2">{s.texto}</td>
                        <td className="max-w-72 px-3 py-2">
                          {s.plano.subject && (
                            <p>
                              <span className="text-muted-foreground">assunto:</span> {s.plano.subject}
                              {s.plano.literal === false && <span className="text-sky-300"> (figurado)</span>}
                            </p>
                          )}
                          <p>{s.plano.visual_intent}</p>
                          {s.plano.must_avoid && s.plano.must_avoid.length > 0 && (
                            <p className="text-muted-foreground">evitar: {s.plano.must_avoid.join(", ")}</p>
                          )}
                          <p className="mt-1 text-muted-foreground">{(s.plano.queries ?? []).join(" · ")}</p>
                          <p className="text-muted-foreground">
                            {s.plano.kind} · afinidade banco {s.plano.affinity?.stock} / YT {s.plano.affinity?.youtube} / IA{" "}
                            {s.plano.affinity?.ai}
                          </p>
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap">
                          <p>{SOURCE[s.fonte_final] ?? s.fonte_final}</p>
                          {s.migrou && (
                            <p className="text-amber-300">alocada: {SOURCE[s.fonte_alocada] ?? s.fonte_alocada}</p>
                          )}
                          {typeof s.render === "object" && s.render.motion && (
                            <p className="text-muted-foreground">{s.render.motion.type}</p>
                          )}
                          {typeof s.render === "object" && s.render.transition_in.type !== "cut" && (
                            <p className="text-muted-foreground">{s.render.transition_in.type}</p>
                          )}
                          {typeof s.render === "string" && <p className="text-amber-300">{s.render}</p>}
                        </td>
                        <td className="max-w-72 px-3 py-2">
                          {s.asset.seen && <p>visto: {s.asset.seen}</p>}
                          {s.asset.prompt ? (
                            <p className="text-muted-foreground">prompt: {s.asset.prompt}</p>
                          ) : (
                            <>
                              <p>
                                {s.asset.provider} · nota {s.asset.score ?? "—"} · início{" "}
                                {s.asset.in_point ?? s.asset.in ?? 0}s
                              </p>
                              <p className="text-muted-foreground">
                                via {s.asset.method ?? "—"} · {s.asset.vision_calls ?? 0} visão ·{" "}
                                {s.asset.searches ?? 0} busca(s)
                                {s.asset.reason && s.asset.method?.startsWith("vision") ? ` · ${s.asset.reason}` : ""}
                              </p>
                              <p className="line-clamp-2 text-muted-foreground">{s.asset.title}</p>
                              {s.asset.page_url && (
                                <a
                                  href={s.asset.page_url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="inline-flex items-center gap-1 text-sky-300 hover:underline"
                                >
                                  <ExternalLink className="size-3" /> ver clipe
                                </a>
                              )}
                            </>
                          )}
                          {s.problemas.map((p) => (
                            <p key={p} className="text-amber-300">
                              {p}
                            </p>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </TabsContent>

            <TabsContent value="visual" className="mt-3 min-h-0 overflow-y-auto pr-1">
              <p className="mb-2 text-xs text-muted-foreground">
                O que cada cena precisava mostrar e o que a IA de visão viu no asset escolhido. Linhas em amarelo
                (nota abaixo de 7 ou sem validação) merecem revisão.
              </p>
              <div className="overflow-x-auto rounded-md border">
                <table className="w-full text-left text-xs">
                  <thead className="bg-muted/50 text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2">Cena</th>
                      <th className="px-3 py-2">Narração</th>
                      <th className="px-3 py-2">Assunto esperado</th>
                      <th className="px-3 py-2">O que apareceu</th>
                      <th className="px-3 py-2">Estilo</th>
                      <th className="px-3 py-2">Fonte</th>
                      <th className="px-3 py-2 text-right">Nota</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(report.visual ?? []).map((v) => (
                      <tr key={v.cena} className={cn("border-t align-top", v.revisar && "bg-amber-500/10")}>
                        <td className="px-3 py-2 font-mono">{v.cena}</td>
                        <td className="max-w-64 px-3 py-2">{v.narracao}</td>
                        <td className="px-3 py-2">{v.assunto_esperado ?? "—"}</td>
                        <td className="max-w-72 px-3 py-2">{v.visto}</td>
                        <td className="max-w-56 px-3 py-2">
                          {v.estilo ?? "—"}
                          {v.style_reason && <p className="text-muted-foreground">{v.style_reason}</p>}
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap">
                          {SOURCE[v.fonte ?? ""] ?? v.fonte ?? "—"}
                          {v.provedor && <span className="text-muted-foreground"> · {v.provedor}</span>}
                        </td>
                        <td className={cn("px-3 py-2 text-right tabular-nums", v.revisar && "text-amber-300")}>
                          {v.nota ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </TabsContent>

            <TabsContent value="json" className="mt-3 min-h-0 overflow-auto">
              <pre className="rounded-md bg-muted/50 p-4 font-mono text-xs leading-relaxed">{json}</pre>
            </TabsContent>
          </Tabs>
        )}
      </DialogContent>
    </Dialog>
  );
}
