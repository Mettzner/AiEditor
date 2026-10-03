"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Download } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { SimpleSelect } from "@/components/fields";
import { api, type ModelDownload } from "@/lib/api";

const OPTIONS: { value: string; label: string }[] = [
  { value: "small", label: "small (recomendado, ~484 MB)" },
  { value: "base", label: "base (leve, ~145 MB)" },
  { value: "medium", label: "medium (mais preciso, ~1,5 GB)" },
];

/** Escolha e download do modelo de transcrição (faster-whisper), com barra de progresso. */
export function ModelDownloadPanel({ current, onReady }: { current: string; onReady?: (size: string) => void }) {
  const [size, setSize] = useState(OPTIONS.some((o) => o.value === current) ? current : "small");
  const [st, setSt] = useState<ModelDownload | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    api.setupModelStatus().then(setSt).catch(() => {});
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, []);

  function poll() {
    if (timer.current) clearInterval(timer.current);
    timer.current = setInterval(async () => {
      try {
        const s = await api.setupModelStatus();
        setSt(s);
        if (s.status !== "downloading" && timer.current) {
          clearInterval(timer.current);
          timer.current = null;
          if (s.status === "done" && s.size) {
            toast.success(`Modelo ${s.size} pronto`);
            onReady?.(s.size);
          }
          if (s.status === "error") toast.error(s.error ?? "Falha no download");
        }
      } catch {}
    }, 800);
  }

  async function start() {
    try {
      setSt(await api.setupModel(size));
      poll();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  const downloading = st?.status === "downloading";
  const ready = st?.downloaded?.[size];
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <SimpleSelect value={size} onChange={setSize} options={OPTIONS} className="sm:w-80" disabled={downloading} />
        {ready ? (
          <Button variant="outline" onClick={() => onReady?.(size)}>
            <CheckCircle2 className="text-emerald-300" /> Baixado: usar este
          </Button>
        ) : (
          <Button onClick={start} disabled={downloading}>
            <Download /> {downloading ? "Baixando…" : "Baixar modelo"}
          </Button>
        )}
      </div>
      {downloading && st && (
        <div className="flex flex-col gap-1.5">
          <Progress value={Math.round(st.progress * 100)} />
          <p className="text-xs text-muted-foreground">
            {(st.bytes / 1048576).toFixed(0)} de ~{(st.total / 1048576).toFixed(0)} MB
          </p>
        </div>
      )}
      {st?.status === "error" && <p className="text-xs text-red-300">{st.error}</p>}
    </div>
  );
}
