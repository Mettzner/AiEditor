"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, Download, ExternalLink, Pin, PinOff, RefreshCw, Search } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { SimpleSelect } from "@/components/fields";
import {
  API_URL,
  api,
  fmtMoney,
  type ReviewFlag,
  type ReviewPayload,
  type ReviewScene,
  type ValidationStatus,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS: Record<ValidationStatus, { label: string; className: string }> = {
  validated: { label: "Validada", className: "bg-emerald-500/15 text-emerald-300" },
  unvalidated: { label: "Não validada", className: "bg-muted text-muted-foreground" },
  review_required: { label: "Revisar", className: "bg-amber-500/15 text-amber-300" },
  rejected: { label: "Sem asset", className: "bg-red-500/15 text-red-300" },
};

const FILTERS: { id: ReviewFlag | "all"; label: string }[] = [
  { id: "all", label: "Todas" },
  { id: "review_required", label: "Revisar" },
  { id: "identity_uncertain", label: "Identidade incerta" },
  { id: "no_vision", label: "Sem visão" },
  { id: "low_score", label: "Nota baixa" },
  { id: "claim_without_source", label: "Afirmação sem fonte" },
];

const ROLES = [
  { value: "exact_evidence", label: "Evidência exata" },
  { value: "contextual_illustration", label: "Ilustração" },
  { value: "explanation", label: "Explicação" },
  { value: "metaphor", label: "Metáfora" },
  { value: "reconstruction", label: "Reconstituição" },
  { value: "comparison", label: "Comparação" },
  { value: "data_explanation", label: "Dados (gráfico)" },
];

const SOURCE: Record<string, string> = {
  youtube: "YouTube",
  stock: "Banco de vídeo",
  stock_photo: "Foto de banco",
  ai_image: "Imagem gerada",
  archive: "Acervo",
  chart: "Gráfico",
  missing: "Sem asset",
  migrate_ai: "Para IA",
  pendente: "Pendente",
};

const fmtTime = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

function Preview({ pid, scene }: { pid: number; scene: ReviewScene }) {
  if (!scene.asset) return <div className="aspect-video w-full rounded-md bg-muted" />;
  const url = api.assetUrl(pid, scene.asset);
  if (scene.is_image)
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={url} alt={scene.subject ?? ""} className="aspect-video w-full rounded-md object-cover" />;
  const from = scene.provider === "pexels" || scene.provider === "pixabay" ? (scene.in_point ?? 0) : 0;
  const to = from + (scene.end - scene.start);
  return (
    <video
      src={`${url}#t=${from.toFixed(2)},${to.toFixed(2)}`}
      controls
      muted
      preload="metadata"
      className="aspect-video w-full rounded-md bg-black"
    />
  );
}

function SceneCard({ pid, s, reload }: { pid: number; s: ReviewScene; reload: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [queries, setQueries] = useState("");
  const [inPoint, setInPoint] = useState(String(s.in_point ?? 0));
  const [subject, setSubject] = useState(s.subject ?? "");
  const [docQuery, setDocQuery] = useState(s.documentary_query);

  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    try {
      await fn();
      toast.success(ok);
      await reload();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const status = STATUS[s.validation.status];
  return (
    <Card>
      <CardContent className="grid gap-4 p-4 lg:grid-cols-[320px_1fr]">
        <div className="space-y-2">
          <Preview pid={pid} scene={s} />
          <p className="text-xs text-muted-foreground">
            {SOURCE[s.source] ?? s.source}
            {s.provider ? ` · ${s.provider}` : ""}
            {s.score != null ? ` · nota ${s.score.toFixed(1)} (${s.validation.score_basis})` : ""}
          </p>
          {s.page_url && (
            <a href={s.page_url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs text-sky-300">
              <ExternalLink className="size-3" /> origem
            </a>
          )}
          {s.obtained_how && <p className="text-xs text-muted-foreground">Obtenção: {s.obtained_how}</p>}
        </div>
        <div className="min-w-0 space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-muted-foreground">
              {s.id} · {fmtTime(s.start)}–{fmtTime(s.end)}
            </span>
            <span className={cn("rounded-sm px-2 py-0.5 text-xs font-medium", status.className)}>{status.label}</span>
            {s.pinned && <Badge variant="outline">fixada</Badge>}
            {s.segment_check && <Badge variant="outline">trecho: {s.segment_check.status}</Badge>}
          </div>
          <p>{s.text}</p>
          {s.meaning && <p className="text-xs text-muted-foreground">Intenção: {s.meaning}</p>}
          {s.seen && <p className="text-xs text-muted-foreground">A IA viu: {s.seen}</p>}
          {s.validation.reasons.length > 0 && (
            <ul className="list-disc pl-4 text-xs text-amber-200">
              {s.validation.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
          {s.unresolved.length > 0 && (
            <p className="text-xs text-amber-200">Interpretação: {s.unresolved.join("; ")}</p>
          )}
          {s.claims.some((c) => c.needs_source) && (
            <p className="text-xs text-amber-200">
              Afirmação sem fonte: {s.claims.filter((c) => c.needs_source).map((c) => `“${c.quote}”`).join(", ")}
            </p>
          )}

          <div className="grid gap-2 sm:grid-cols-2">
            <SimpleSelect
              value={s.visual_role}
              onChange={(v) => run(() => api.fixInterpretation(pid, s.id, { visual_role: v }), "Papel visual corrigido")}
              options={ROLES}
              disabled={busy}
            />
            <div className="flex gap-2">
              <Input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Assunto" />
              <Button
                size="sm"
                variant="outline"
                disabled={busy || subject === (s.subject ?? "")}
                onClick={() => run(() => api.fixInterpretation(pid, s.id, { subject }), "Assunto corrigido")}
              >
                Salvar
              </Button>
            </div>
            <div className="flex gap-2 sm:col-span-2">
              <Input
                value={docQuery}
                onChange={(e) => setDocQuery(e.target.value)}
                placeholder="Busca documental (nome próprio, nome científico, ano)"
              />
              <Button
                size="sm"
                variant="outline"
                disabled={busy || docQuery === s.documentary_query}
                onClick={() =>
                  run(() => api.fixInterpretation(pid, s.id, { documentary_query: docQuery }), "Busca documental salva")
                }
              >
                Salvar
              </Button>
            </div>
          </div>
          {s.entities.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Entidades:{" "}
              {s.entities.map((e) => (
                <button
                  key={e.id}
                  className="mr-2 underline decoration-dotted"
                  onClick={() => {
                    const sci = window.prompt(`Nome científico de “${e.name}” (vazio para manter)`, e.scientific_name ?? "");
                    if (sci && sci !== e.scientific_name)
                      run(() => api.fixEntity(pid, e.id, { scientific_name: sci }), "Entidade corrigida");
                  }}
                >
                  {e.name}
                  {e.scientific_name ? ` (${e.scientific_name})` : ""}
                </button>
              ))}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={busy || !s.asset}
              onClick={() => run(() => api.pinAsset(pid, s.id, !s.pinned), s.pinned ? "Cena liberada" : "Cena fixada")}
            >
              {s.pinned ? <PinOff /> : <Pin />} {s.pinned ? "Liberar" : "Fixar"}
            </Button>
            {s.asset && !s.is_image && (
              <>
                <Input
                  type="number"
                  step="0.1"
                  min={0}
                  className="w-24"
                  value={inPoint}
                  onChange={(e) => setInPoint(e.target.value)}
                  title="Início do trecho no vídeo original (s)"
                />
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy || Number(inPoint) === (s.in_point ?? 0)}
                  onClick={() => run(() => api.editInterval(pid, s.id, Number(inPoint)), "Trecho atualizado")}
                >
                  Usar trecho
                </Button>
              </>
            )}
            <Input
              className="min-w-40 flex-1"
              value={queries}
              onChange={(e) => setQueries(e.target.value)}
              placeholder="Buscar mais (consultas separadas por ;)"
            />
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const r = await api.searchMore(
                    pid,
                    s.id,
                    queries.split(";").map((q) => q.trim()).filter(Boolean),
                  );
                  if (r.budget_blocked) toast.warning("Teto de gasto atingido: avaliação pelo texto");
                  return r;
                }, "Busca concluída")
              }
            >
              <Search /> Buscar
            </Button>
          </div>

          {s.alternatives.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-medium">Alternativas</p>
              <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
                {s.alternatives.map((a) => (
                  <div key={a.index} className="rounded-md border p-2 text-xs">
                    {a.thumbnail && a.thumbnail.startsWith("http") && (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={a.thumbnail} alt="" className="mb-1 aspect-video w-full rounded object-cover" />
                    )}
                    <p className="truncate" title={a.title}>
                      {a.title || a.provider}
                    </p>
                    <p className="text-muted-foreground">
                      {a.provider} · {a.score != null ? a.score.toFixed(1) : "–"} {a.method ? `(${a.method})` : ""}
                    </p>
                    {a.reason && <p className="truncate text-muted-foreground" title={a.reason}>{a.reason}</p>}
                    <Button
                      size="sm"
                      variant="ghost"
                      className="mt-1 h-6 px-2"
                      disabled={busy}
                      onClick={() => run(() => api.replaceAsset(pid, s.id, { alternative: a.index }), "Cena trocada")}
                    >
                      Usar
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {s.references.length > 0 && (
            <details className="text-xs">
              <summary className="cursor-pointer">Referências do YouTube ({s.references.length})</summary>
              <ul className="mt-1 space-y-1">
                {s.references.map((r) => (
                  <li key={r.youtube_id} className="flex flex-wrap items-center gap-2">
                    <a href={r.url} target="_blank" rel="noreferrer" className="text-sky-300">
                      {r.title || r.youtube_id}
                    </a>
                    <span className="text-muted-foreground">{r.channel}</span>
                    <Badge variant="outline">
                      {r.status === "authorized_available" ? "arquivo autorizado" : "só referência"}
                    </Badge>
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-muted-foreground">
                Para usar uma referência no vídeo, associe um arquivo autorizado em Configuração → Mídia autorizada.
              </p>
            </details>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function Review() {
  const params = useSearchParams();
  const pid = Number(params.get("id"));
  const [data, setData] = useState<ReviewPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<ReviewFlag | "all">("all");

  const load = useCallback(async () => {
    try {
      setData(await api.review(pid));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [pid]);

  useEffect(() => {
    if (!pid) return;
    let alive = true;
    api
      .review(pid)
      .then((d) => alive && setData(d))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [pid]);

  const scenes = useMemo(
    () => (data?.scenes ?? []).filter((s) => filter === "all" || s.flags.includes(filter)),
    [data, filter],
  );

  if (!pid) return <p className="text-sm text-muted-foreground">Produção não informada.</p>;
  if (error) return <p className="text-sm text-red-300">{error}</p>;
  if (!data) return <p className="text-sm text-muted-foreground">Carregando…</p>;
  const m = data.metrics;
  const editable = ["done", "failed", "cancelled"].includes(data.production.status);

  return (
    <div className="w-full space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link href="/" className={buttonVariants({ variant: "ghost", size: "sm" })}>
          <ArrowLeft /> Produções
        </Link>
        <h1 className="min-w-0 flex-1 truncate text-xl font-semibold">Revisão · {data.production.title}</h1>
        <div className="w-56">
          <SimpleSelect
            value={data.production.language}
            onChange={async (v) => {
              try {
                await api.fixLanguage(pid, v);
                toast.success("Idioma corrigido: a produção volta para a fila");
                await load();
              } catch (e) {
                toast.error((e as Error).message);
              }
            }}
            options={data.languages.map((l) => ({ value: l.code, label: l.name }))}
            disabled={!editable}
          />
        </div>
        <a href={`${API_URL}/productions/${pid}/manifest`} target="_blank" rel="noreferrer"
           className={buttonVariants({ variant: "outline", size: "sm" })}>
          <Download /> Manifesto
        </a>
        <a href={`${API_URL}/productions/${pid}/credits`} target="_blank" rel="noreferrer"
           className={buttonVariants({ variant: "outline", size: "sm" })}>
          Créditos
        </a>
        <Button
          size="sm"
          disabled={!editable}
          onClick={async () => {
            try {
              await api.rerender(pid);
              toast.success("Render parcial na fila: só as cenas alteradas são refeitas");
              await load();
            } catch (e) {
              toast.error((e as Error).message);
            }
          }}
        >
          <RefreshCw /> Renderizar de novo
        </Button>
      </div>

      {!editable && (
        <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
          A produção está em andamento: as edições ficam disponíveis quando ela terminar.
        </p>
      )}

      <div className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardContent className="p-3">
            <p className="text-xs text-muted-foreground">Validação</p>
            {(Object.keys(STATUS) as ValidationStatus[]).map((k) => (
              <p key={k}>
                {STATUS[k].label}: <b>{m.validation[k] ?? 0}</b>
              </p>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-3">
            <p className="text-xs text-muted-foreground">Por fonte (cenas · segundos · validadas)</p>
            {Object.entries(m.by_source).map(([k, v]) => (
              <p key={k}>
                {SOURCE[k] ?? k}: {v.scenes} · {Math.round(v.seconds)}s · {v.validated}
              </p>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-3">
            <p className="text-xs text-muted-foreground">Pesquisa</p>
            <p>Buscas: {m.search.searches ?? "–"} · visão: {m.search.vision_calls ?? "–"}</p>
            <p>Cache: {m.search.cache ? `${m.search.cache.cache_hit ?? 0} acertos / ${m.search.cache.cache_miss ?? 0} faltas` : "–"}</p>
            <p>Referências do YouTube: {m.youtube_references}</p>
            {m.segments && (
              <p>
                Vídeos únicos: {m.segments.unique_assets} · segmentos: {m.segments.segments}
              </p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-3">
            <p className="text-xs text-muted-foreground">Custo de IA</p>
            <p>Total registrado: {fmtMoney(data.production.cost_actual)}</p>
            {Object.entries(m.cost_by_task).map(([k, v]) => (
              <p key={k} className="text-xs">
                {k}: {fmtMoney(v.actual_usd)} ({v.settled}×){v.unknown_price ? " · preço desconhecido" : ""}
              </p>
            ))}
          </CardContent>
        </Card>
      </div>

      <div className="flex flex-wrap gap-2">
        {FILTERS.map((f) => {
          const n = f.id === "all" ? data.scenes.length : data.scenes.filter((s) => s.flags.includes(f.id as ReviewFlag)).length;
          return (
            <Button key={f.id} size="sm" variant={filter === f.id ? "default" : "outline"} onClick={() => setFilter(f.id)}>
              {f.label} ({n})
            </Button>
          );
        })}
      </div>

      <div className="flex flex-col gap-3">
        {scenes.map((s) => (
          <SceneCard key={s.id} pid={pid} s={s} reload={load} />
        ))}
      </div>
    </div>
  );
}

export default function RevisaoPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Carregando…</p>}>
      <Review />
    </Suspense>
  );
}
