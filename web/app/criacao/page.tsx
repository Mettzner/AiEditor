"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, ArrowLeft, ArrowRight, Check, Play, Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { AI_MEDIA } from "@/components/channel-form";
import { Field, SimpleSelect, SplitSlider } from "@/components/fields";
import { api, fmtDuration, fmtMoney, type Channel, type Direction, type Estimate } from "@/lib/api";
import { cn } from "@/lib/utils";

const STEPS = ["Conteúdo", "Direção", "Composição e ritmo", "Resumo"];
const WPM: Record<string, number> = { en: 150, pt: 145, es: 150 };

interface Composition {
  direction: string;
  real_pct: number;
  ai_media: "both" | "video" | "image";
  youtube_pct: number;
  avg_scene_seconds: number;
  subtitles: boolean;
  music_enabled: boolean;
}

function fromChannel(c: Channel): Composition {
  const p = c.preset;
  return {
    direction: p.direction,
    real_pct: p.real_pct,
    ai_media: p.ai_media,
    youtube_pct: p.youtube_pct,
    avg_scene_seconds: p.avg_scene_seconds,
    subtitles: p.subtitles,
    music_enabled: p.music.enabled,
  };
}

export default function CriacaoPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [directions, setDirections] = useState<Direction[]>([]);
  const [channelId, setChannelId] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [script, setScript] = useState("");
  const [audioMode, setAudioMode] = useState<"tts" | "upload">("tts");
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [audioSeconds, setAudioSeconds] = useState<number | null>(null);
  const [comp, setComp] = useState<Composition | null>(null);
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    Promise.all([api.channels(), api.directions()])
      .then(([cs, ds]) => {
        setChannels(cs);
        setDirections(ds);
        if (cs.length === 1) pickChannel(cs[0]);
      })
      .catch((e) => toast.error(`Backend indisponível: ${(e as Error).message}`));
  }, []);

  const channel = channels.find((c) => c.id === channelId) ?? null;

  function pickChannel(c: Channel) {
    setChannelId(c.id);
    setComp(fromChannel(c));
  }

  function pickAudio(file: File | null) {
    setAudioFile(file);
    setAudioSeconds(null);
    if (!file) return;
    const el = new Audio(URL.createObjectURL(file));
    el.onloadedmetadata = () => setAudioSeconds(el.duration);
  }

  const words = useMemo(() => script.split(/\s+/).filter(Boolean).length, [script]);
  const wpm = WPM[channel?.preset.language ?? "en"] ?? 150;
  const duration = audioMode === "upload" && audioSeconds ? audioSeconds : (words / wpm) * 60;

  const preview = useMemo(() => {
    if (!comp) return null;
    const real = (duration * comp.real_pct) / 100;
    const yt = (real * comp.youtube_pct) / 100;
    return {
      scenes: duration ? Math.max(1, Math.round(duration / comp.avg_scene_seconds)) : 0,
      real,
      yt,
      stock: real - yt,
      ai: duration - real,
    };
  }, [comp, duration]);

  const valid = [
    !!channel &&
      title.trim().length > 0 &&
      words > 0 &&
      (audioMode === "upload" ? !!audioFile : !!channel?.preset.tts_voice),
    !!comp?.direction,
    !!comp && comp.avg_scene_seconds >= 2 && comp.avg_scene_seconds <= 20,
    true,
  ];

  function goNext() {
    const next = step + 1;
    setStep(next);
    if (next !== 3 || !channel || !comp) return;
    setEstimate(null);
    api
      .estimate({
        channel_id: channel.id,
        script,
        config: { ...comp },
        audio_seconds: audioMode === "upload" ? audioSeconds : null,
        audio_mode: audioMode,
      })
      .then(setEstimate)
      .catch((e) => toast.error((e as Error).message));
  }

  async function submit() {
    if (!channel || !comp) return;
    setSubmitting(true);
    const fd = new FormData();
    fd.append("channel_id", String(channel.id));
    fd.append("title", title);
    fd.append("script", script);
    fd.append("config", JSON.stringify(comp));
    if (audioMode === "upload" && audioFile) fd.append("audio", audioFile);
    try {
      await api.createProduction(fd);
      toast.success("Produção na fila");
      router.push("/");
    } catch (e) {
      toast.error((e as Error).message);
      setSubmitting(false);
    }
  }

  const setC = <K extends keyof Composition>(k: K, v: Composition[K]) => setComp((c) => (c ? { ...c, [k]: v } : c));

  return (
    <div className="mx-auto flex min-h-[calc(100vh-3.5rem)] max-w-3xl flex-col">
      <h1 className="mb-5 text-xl font-semibold tracking-tight">Nova produção</h1>
      <ol className="mb-6 flex items-center gap-2">
        {STEPS.map((s, i) => (
          <li key={s} className="flex flex-1 items-center gap-2">
            <span
              className={cn(
                "flex size-6 shrink-0 items-center justify-center rounded-full border text-xs",
                i < step && "border-emerald-500 bg-emerald-500/20 text-emerald-300",
                i === step && "border-foreground font-medium",
                i > step && "text-muted-foreground",
              )}
            >
              {i < step ? <Check className="size-3.5" /> : i + 1}
            </span>
            <span className={cn("truncate text-sm", i !== step && "text-muted-foreground")}>{s}</span>
            {i < STEPS.length - 1 && <span className="h-px flex-1 bg-border" />}
          </li>
        ))}
      </ol>

      <Card className="flex-1">
        <CardContent className="flex flex-col gap-5">
          {step === 0 && (
            <>
              <Field label="Canal" hint={channels.length === 0 ? "Crie um canal primeiro, na tela Canais." : undefined}>
                <SimpleSelect
                  value={channelId ? String(channelId) : null}
                  onChange={(v) => {
                    const c = channels.find((x) => x.id === Number(v));
                    if (c) pickChannel(c);
                  }}
                  options={channels.map((c) => ({ value: String(c.id), label: c.name }))}
                />
              </Field>
              <Field label="Título">
                <Input value={title} onChange={(e) => setTitle(e.target.value)} />
              </Field>
              <Field
                label="Roteiro"
                hint={`${words} palavras · duração estimada ≈ ${fmtDuration((words / wpm) * 60)}`}
              >
                <Textarea value={script} onChange={(e) => setScript(e.target.value)} rows={12} />
              </Field>
              <Field label="Áudio da narração">
                <RadioGroup value={audioMode} onValueChange={(v) => setAudioMode(v as "tts" | "upload")}>
                  <label className="flex items-center gap-2 text-sm">
                    <RadioGroupItem value="tts" /> Gerar automaticamente (TTS com a voz do canal)
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <RadioGroupItem value="upload" /> Enviar áudio (mp3, wav ou m4a)
                  </label>
                </RadioGroup>
                {audioMode === "tts" && channel && !channel.preset.tts_voice && (
                  <p className="flex items-center gap-1.5 text-xs text-amber-300">
                    <AlertTriangle className="size-3.5" /> Este canal não tem voz TTS definida.
                  </p>
                )}
                {audioMode === "upload" && (
                  <div className="flex items-center gap-3">
                    <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-1.5 text-sm hover:bg-muted">
                      <Upload className="size-4" /> Escolher arquivo
                      <input
                        type="file"
                        accept=".mp3,.wav,.m4a,audio/*"
                        className="hidden"
                        onChange={(e) => pickAudio(e.target.files?.[0] ?? null)}
                      />
                    </label>
                    <span className="truncate text-xs text-muted-foreground">
                      {audioFile ? `${audioFile.name} · ${fmtDuration(audioSeconds)}` : "nenhum arquivo"}
                    </span>
                  </div>
                )}
              </Field>
            </>
          )}

          {step === 1 && comp && (
            <div className="grid gap-3 sm:grid-cols-3">
              {directions.map((d) => (
                <button
                  key={d.id}
                  onClick={() => setC("direction", d.id)}
                  className={cn(
                    "overflow-hidden rounded-lg border text-left transition-colors hover:border-foreground/40",
                    comp.direction === d.id && "border-foreground ring-1 ring-foreground",
                  )}
                >
                  {d.image && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={api.directionImage(d.id)} alt="" className="aspect-video w-full object-cover" />
                  )}
                  <div className="p-3">
                    <p className="text-sm font-medium">{d.name}</p>
                    <p className="text-xs text-muted-foreground">{d.description}</p>
                  </div>
                </button>
              ))}
            </div>
          )}

          {step === 2 && comp && preview && (
            <>
              <SplitSlider left="Real" right="IA" value={comp.real_pct} onChange={(v) => setC("real_pct", v)} />
              <Field label="Tipo de mídia de IA">
                <SimpleSelect
                  value={comp.ai_media}
                  onChange={(v) => setC("ai_media", v as Composition["ai_media"])}
                  options={AI_MEDIA}
                  disabled={comp.real_pct === 100}
                />
              </Field>
              <SplitSlider
                left="YouTube"
                right="Bancos"
                value={comp.youtube_pct}
                onChange={(v) => setC("youtube_pct", v)}
                disabled={comp.real_pct === 0}
              />
              <Field label="Duração média por cena (s)" hint="Entre 2 e 20 s; a direção varia em torno dessa média.">
                <Input
                  type="number"
                  min={2}
                  max={20}
                  step={0.5}
                  value={comp.avg_scene_seconds}
                  onChange={(e) => setC("avg_scene_seconds", Number(e.target.value))}
                  className="w-32"
                />
              </Field>
              <div className="flex gap-8">
                <label className="flex items-center gap-2 text-sm">
                  <Switch checked={comp.subtitles} onCheckedChange={(v) => setC("subtitles", v)} /> Legendas
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <Switch checked={comp.music_enabled} onCheckedChange={(v) => setC("music_enabled", v)} /> Música de
                  fundo
                </label>
              </div>
              <pre className="rounded-lg bg-muted/60 p-4 font-mono text-sm leading-relaxed">
                {`Duração ≈ ${fmtDuration(duration)} · ≈ ${preview.scenes} cenas
Real ${fmtDuration(preview.real)}  →  YouTube ${fmtDuration(preview.yt)} · Bancos ${fmtDuration(preview.stock)}
IA   ${fmtDuration(preview.ai)}  →  ${AI_MEDIA.find((m) => m.value === comp.ai_media)?.label.toLowerCase()}`}
              </pre>
            </>
          )}

          {step === 3 && comp && channel && (
            <div className="flex flex-col gap-5 text-sm">
              <dl className="grid grid-cols-[160px_1fr] gap-x-4 gap-y-1.5">
                <dt className="text-muted-foreground">Canal</dt>
                <dd>{channel.name}</dd>
                <dt className="text-muted-foreground">Título</dt>
                <dd>{title}</dd>
                <dt className="text-muted-foreground">Roteiro</dt>
                <dd>{words} palavras</dd>
                <dt className="text-muted-foreground">Áudio</dt>
                <dd>{audioMode === "upload" ? audioFile?.name : `TTS · ${channel.preset.tts_voice}`}</dd>
                <dt className="text-muted-foreground">Direção</dt>
                <dd>{directions.find((d) => d.id === comp.direction)?.name}</dd>
                <dt className="text-muted-foreground">Composição</dt>
                <dd>
                  Real {comp.real_pct}% (YouTube {comp.youtube_pct}% / Bancos {100 - comp.youtube_pct}%) · IA{" "}
                  {100 - comp.real_pct}% ({AI_MEDIA.find((m) => m.value === comp.ai_media)?.label.toLowerCase()})
                </dd>
                <dt className="text-muted-foreground">Ritmo</dt>
                <dd>{comp.avg_scene_seconds}s por cena</dd>
                <dt className="text-muted-foreground">Acabamento</dt>
                <dd>
                  Legendas {comp.subtitles ? "sim" : "não"} · Música {comp.music_enabled ? "sim" : "não"}
                </dd>
              </dl>

              {!estimate ? (
                <p className="text-muted-foreground">Calculando estimativa…</p>
              ) : (
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="rounded-lg border p-4">
                    <p className="mb-2 font-medium">Custo e tempo estimados</p>
                    <dl className="grid grid-cols-2 gap-y-1 text-muted-foreground">
                      <dt>TTS</dt>
                      <dd className="text-right">{fmtMoney(estimate.cost.tts)}</dd>
                      <dt>LLMs</dt>
                      <dd className="text-right">{fmtMoney(estimate.cost.llm)}</dd>
                      <dt>Geração de IA</dt>
                      <dd className="text-right">{fmtMoney(estimate.cost.ai)}</dd>
                      <dt>Música</dt>
                      <dd className="text-right">{fmtMoney(estimate.cost.music)}</dd>
                      <dt className="text-foreground">Total</dt>
                      <dd className="text-right text-foreground">{fmtMoney(estimate.cost.total)}</dd>
                      <dt>Tempo</dt>
                      <dd className="text-right">≈ {estimate.time_minutes} min</dd>
                    </dl>
                  </div>
                  <div className="rounded-lg border p-4">
                    <p className="mb-2 font-medium">Uso de cotas</p>
                    <dl className="grid grid-cols-2 gap-y-1 text-muted-foreground">
                      <dt>Imagens Darkvi</dt>
                      <dd className={cn("text-right", !estimate.quotas.darkvi_enough && "text-amber-300")}>
                        {estimate.quotas.darkvi_images_needed} /{" "}
                        {estimate.quotas.darkvi_remaining ?? "saldo desconhecido"}
                      </dd>
                      <dt>YouTube Data API</dt>
                      <dd className="text-right">
                        {estimate.quotas.youtube_units_needed} / {estimate.quotas.youtube_daily_quota} un.
                      </dd>
                    </dl>
                    {!estimate.quotas.darkvi_enough && (
                      <p className="mt-2 text-xs text-amber-300">
                        O saldo de imagens de hoje não cobre esta produção; as cenas excedentes vão para os bancos.
                      </p>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="mt-5 flex justify-between">
        <Button variant="outline" onClick={() => setStep((s) => s - 1)} disabled={step === 0}>
          <ArrowLeft /> Voltar
        </Button>
        {step < 3 ? (
          <Button onClick={goNext} disabled={!valid[step]}>
            Próximo <ArrowRight />
          </Button>
        ) : (
          <Button onClick={submit} disabled={submitting}>
            <Play /> {submitting ? "Enviando…" : "Iniciar produção"}
          </Button>
        )}
      </div>
    </div>
  );
}
