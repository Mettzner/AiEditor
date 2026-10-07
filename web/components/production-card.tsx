"use client";

import { useState } from "react";
import Link from "next/link";
import {
  Check,
  ChevronDown,
  Copy,
  ExternalLink,
  FolderOpen,
  ListChecks,
  RotateCcw,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Progress } from "@/components/ui/progress";
import { ConfirmDelete } from "@/components/confirm-delete";
import { DirectionDialog } from "@/components/direction-dialog";
import { api, fmtDuration, fmtMoney, type Production, type Severity } from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS: Record<Production["status"], { label: string; className: string }> = {
  preparing: { label: "Preparando", className: "bg-muted text-muted-foreground" },
  queued: { label: "Na fila", className: "bg-muted text-muted-foreground" },
  waiting_provider: { label: "Aguardando lote", className: "bg-amber-500/15 text-amber-300" },
  running: { label: "Em produção", className: "bg-sky-500/15 text-sky-300" },
  done: { label: "Concluído", className: "bg-emerald-500/15 text-emerald-300" },
  failed: { label: "Falhou", className: "bg-red-500/15 text-red-300" },
  cancel_requested: { label: "Cancelando", className: "bg-amber-500/15 text-amber-300" },
  cancelled: { label: "Cancelado", className: "bg-muted text-muted-foreground" },
};

const STEP_LABEL: Record<string, string> = {
  audio: "Áudio",
  transcribe: "Transcrição",
  plan: "Planejamento",
  select: "Seleção de cenas",
  generate: "Geração de IA",
  direct: "Direção",
  render: "Render",
  upload: "Upload",
};

const SEVERITY: Record<Severity, { label: string; className: string }> = {
  error: { label: "erro", className: "border-red-500/40 text-red-300" },
  warning: { label: "aviso", className: "border-amber-500/40 text-amber-300" },
  info: { label: "info", className: "border-sky-500/40 text-sky-300" },
};

export function ProductionCard({ p }: { p: Production }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const status = STATUS[p.status];
  const active = p.status === "running" || p.status === "queued" || p.status === "cancel_requested";

  async function act(fn: () => Promise<unknown>, ok: string) {
    try {
      await fn();
      toast.success(ok);
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function copyTitle() {
    try {
      await navigator.clipboard.writeText(p.title);
      setCopied(true);
      toast.success("Título copiado");
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Não foi possível copiar o título");
    }
  }

  const times = ["select", "generate", "render"].filter((k) => p.step_seconds?.[k] != null);

  return (
    <Card className="gap-0 py-3 [--card-spacing:--spacing(4)]">
      <CardContent>
        <Collapsible open={open} onOpenChange={setOpen}>
          {/* linha principal: identificação · progresso · status · ações */}
          <div className="flex items-center gap-4">
            <div className="w-52 min-w-0 shrink-0">
              <div className="flex min-w-0 items-center gap-1">
                <h3 className="truncate font-medium leading-tight" title={p.title}>
                  {p.title}
                </h3>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  className="shrink-0 text-muted-foreground"
                  aria-label="Copiar título"
                  title="Copiar título"
                  onClick={copyTitle}
                >
                  {copied ? <Check /> : <Copy />}
                </Button>
              </div>
              <p className="truncate text-xs text-muted-foreground">
                {p.channel_name ?? "—"} · #{p.id}
              </p>
            </div>
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="truncate text-muted-foreground" title={p.step_label ?? ""}>
                  {p.step_label ?? "—"}
                </span>
                <span className="tabular-nums">{Math.round(p.progress)}%</span>
              </div>
              <Progress value={p.progress} />
            </div>
            <span className={cn("shrink-0 rounded-sm px-2 py-0.5 text-xs font-medium", status.className)}>
              {status.label}
            </span>
            <div className="flex shrink-0 items-center gap-1.5">
              {(p.status === "done" || p.status === "failed" || ["direct", "render", "upload"].includes(p.step ?? "")) && (
                <DirectionDialog productionId={p.id} title={p.title} />
              )}
              {(p.status === "done" || p.status === "failed" || p.status === "cancelled") && (
                <Link href={`/revisao/?id=${p.id}`} className={buttonVariants({ variant: "outline", size: "sm" })}>
                  <ListChecks /> Revisar
                </Link>
              )}
              {(p.status === "running" || p.status === "queued") && (
                <Button variant="ghost" size="sm" onClick={() => act(() => api.cancel(p.id), "Cancelamento pedido")}>
                  <X /> Cancelar
                </Button>
              )}
              {(p.status === "failed" || p.status === "cancelled") && (
                <Button
                  variant="outline"
                  size="sm"
                  title="Continua da etapa que parou: o que já foi feito (áudio, plano, clipes, cenas prontas) é reaproveitado"
                  onClick={() => act(() => api.retry(p.id), "Produção retomada de onde parou")}
                >
                  <RotateCcw /> Retomar{p.step && STEP_LABEL[p.step] ? ` de: ${STEP_LABEL[p.step]}` : ""}
                </Button>
              )}
              {!active && (
                <ConfirmDelete
                  title={`Excluir "${p.title}"?`}
                  trigger={<Button variant="ghost" size="icon-sm" aria-label="Excluir" />}
                  label={<Trash2 />}
                  onConfirm={() => act(() => api.deleteProduction(p.id), "Produção excluída")}
                >
                  <p>
                    Apaga a produção e todos os arquivos dela neste computador: narração, cenas baixadas, plano e
                    vídeo final. Não dá para desfazer.
                  </p>
                  {p.drive_url && <p className="mt-2">A cópia enviada ao Google Drive não é apagada.</p>}
                </ConfirmDelete>
              )}
            </div>
          </div>

          {/* linha de detalhes: tempos · duração · custo · Drive · IA · problemas */}
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            {times.length > 0 && (
              <span title={JSON.stringify(p.step_seconds)}>
                {times.map((k) => `${STEP_LABEL[k]} ${fmtDuration(p.step_seconds![k])}`).join(" · ")}
              </span>
            )}
            {p.status === "done" && (
              <>
                <span>Duração {fmtDuration(p.duration_seconds)}</span>
                <span>Custo {fmtMoney(p.cost_actual)}</span>
                {p.drive_url ? (
                  <a
                    href={p.drive_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-sky-300 hover:underline"
                  >
                    <ExternalLink className="size-3" /> Abrir no Drive
                  </a>
                ) : (
                  p.output_path && (
                    <span className="inline-flex items-center gap-1" title={p.output_path}>
                      <FolderOpen className="size-3" /> salvo localmente
                    </span>
                  )
                )}
              </>
            )}
            {p.llm_usage && (
              <span
                title={`Claude + Gemini nesta produção${p.llm_usage.cache_read > 0 ? ` · ${(p.llm_usage.cache_read / 1000).toFixed(1)} mil tokens do cache` : ""}${p.llm_usage.top_task ? ` · mais cara: ${p.llm_usage.top_task}` : ""}`}
              >
                IA {fmtMoney(p.llm_usage.cost)} · {(p.llm_usage.tokens / 1000).toFixed(1)} mil tokens
              </span>
            )}
            {p.ai_content && p.status === "done" && (
              <span
                className="inline-flex items-center gap-1 text-violet-300"
                title="Contém mídia gerada por IA: marque “conteúdo alterado” ao publicar no YouTube."
              >
                <Sparkles className="size-3" /> mídia de IA
              </span>
            )}
            {p.status === "failed" && p.error && (
              <span className="max-w-xl truncate text-red-300" title={p.error}>
                {p.error}
              </span>
            )}
            {p.issues.length > 0 && (
              <CollapsibleTrigger className="ml-auto flex items-center gap-1.5">
                {(["error", "warning", "info"] as Severity[]).map((sev) =>
                  p.issue_counts[sev] ? (
                    <Badge key={sev} variant="outline" className={SEVERITY[sev].className}>
                      {p.issue_counts[sev]} {SEVERITY[sev].label}
                    </Badge>
                  ) : null,
                )}
                <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
              </CollapsibleTrigger>
            )}
          </div>

          {p.issues.length > 0 && (
            <CollapsibleContent>
              <ul className="mt-2 flex max-h-56 flex-col gap-1.5 overflow-y-auto pr-1">
                {p.issues.map((i) => (
                  <li key={i.id} className="rounded-md bg-muted/50 px-2 py-1.5 text-xs">
                    <div className="flex items-center gap-1.5">
                      <span className={cn("font-mono text-[10px]", SEVERITY[i.severity].className.split(" ")[1])}>
                        {i.code}
                      </span>
                      {i.scene && <span className="text-muted-foreground">cena {i.scene}</span>}
                    </div>
                    <p>{i.message}</p>
                    {i.detail && (
                      <details className="mt-0.5 text-muted-foreground">
                        <summary className="cursor-pointer">detalhe técnico</summary>
                        <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all text-[10px]">
                          {i.detail}
                        </pre>
                      </details>
                    )}
                  </li>
                ))}
              </ul>
            </CollapsibleContent>
          )}
        </Collapsible>
      </CardContent>
    </Card>
  );
}
