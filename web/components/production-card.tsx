"use client";

import { useState } from "react";
import { ChevronDown, ExternalLink, FolderOpen, RotateCcw, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Progress } from "@/components/ui/progress";
import { api, fmtDuration, fmtMoney, type Production, type Severity } from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS: Record<Production["status"], { label: string; className: string }> = {
  queued: { label: "Na fila", className: "bg-muted text-muted-foreground" },
  running: { label: "Em produção", className: "bg-sky-500/15 text-sky-300" },
  done: { label: "Concluído", className: "bg-emerald-500/15 text-emerald-300" },
  failed: { label: "Falhou", className: "bg-red-500/15 text-red-300" },
  cancel_requested: { label: "Cancelando", className: "bg-amber-500/15 text-amber-300" },
  cancelled: { label: "Cancelado", className: "bg-muted text-muted-foreground" },
};

const SEVERITY: Record<Severity, { label: string; className: string }> = {
  error: { label: "erro", className: "border-red-500/40 text-red-300" },
  warning: { label: "aviso", className: "border-amber-500/40 text-amber-300" },
  info: { label: "info", className: "border-sky-500/40 text-sky-300" },
};

export function ProductionCard({ p }: { p: Production }) {
  const [open, setOpen] = useState(false);
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

  return (
    <Card className="gap-3">
      <CardHeader className="gap-1">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate font-medium leading-tight" title={p.title}>
              {p.title}
            </h3>
            <p className="text-xs text-muted-foreground">
              {p.channel_name ?? "—"} · #{p.id}
            </p>
          </div>
          <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-xs font-medium", status.className)}>
            {status.label}
          </span>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between text-xs">
            <span className="truncate text-muted-foreground">{p.step_label ?? "—"}</span>
            <span className="tabular-nums">{Math.round(p.progress)}%</span>
          </div>
          <Progress value={p.progress} />
        </div>

        {p.status === "done" && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
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
          </div>
        )}
        {p.status === "failed" && p.error && <p className="line-clamp-2 text-xs text-red-300">{p.error}</p>}
        {p.ai_content && p.status === "done" && (
          <p className="text-xs text-muted-foreground">
            Contém mídia gerada por IA: marque “conteúdo alterado” ao publicar no YouTube.
          </p>
        )}

        {p.issues.length > 0 && (
          <Collapsible open={open} onOpenChange={setOpen}>
            <CollapsibleTrigger className="flex w-full items-center gap-1.5 text-xs">
              {(["error", "warning", "info"] as Severity[]).map((sev) =>
                p.issue_counts[sev] ? (
                  <Badge key={sev} variant="outline" className={SEVERITY[sev].className}>
                    {p.issue_counts[sev]} {SEVERITY[sev].label}
                  </Badge>
                ) : null,
              )}
              <ChevronDown className={cn("ml-auto size-3.5 transition-transform", open && "rotate-180")} />
            </CollapsibleTrigger>
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
          </Collapsible>
        )}

        <div className="flex justify-end gap-1.5">
          {(p.status === "running" || p.status === "queued") && (
            <Button variant="ghost" size="sm" onClick={() => act(() => api.cancel(p.id), "Cancelamento pedido")}>
              <X /> Cancelar
            </Button>
          )}
          {(p.status === "failed" || p.status === "cancelled") && (
            <Button variant="outline" size="sm" onClick={() => act(() => api.retry(p.id), "Produção retomada")}>
              <RotateCcw /> Tentar novamente
            </Button>
          )}
          {!active && (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Excluir"
              onClick={() => act(() => api.deleteProduction(p.id), "Produção excluída")}
            >
              <Trash2 />
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
