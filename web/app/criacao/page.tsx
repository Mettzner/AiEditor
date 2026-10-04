"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, ArrowLeft, ArrowRight, Check, Play, Sparkles, Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { AI_MEDIA } from "@/components/channel-form";
import { Field, SimpleSelect, SplitSlider } from "@/components/fields";
import { VoicePicker } from "@/components/voice-picker";
import {
  api,
  fmtDuration,
  fmtMoney,
  MEDIA_STYLES,
  SELECTION_MODES,
  type Channel,
  type Direction,
  type Estimate,
  type MediaStyle,
  type SelectionMode,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const STEPS = ["Conteúdo", "Direção", "Composição e ritmo", "Resumo"];
const WPM = 150; // só para a estimativa na tela; o servidor usa o idioma detectado no roteiro

/**
 * Escolhas da Criação; vêm pré-preenchidas com o padrão salvo do canal. Idioma, buscas, estilo visual e época não
 * aparecem aqui: o sistema interpreta tudo a partir do roteiro.
 */
interface Choices {
  direction: string;
  real_pct: number;
  ai_media: "both" | "video" | "image";
  youtube_pct: number;
  avg_scene_seconds: number;
  subtitles: boolean;
  music_enabled: boolean;
  media_style: MediaStyle;
  selection_mode: SelectionMode;
  llm_economy: boolean;
  period_grade: boolean;
  context_cards: boolean;
  sfx: boolean;
  film_look: boolean;
  tts_voice: string | null;
  tts_voice_name: string | null;
}

function fromChannel(c: Channel): Choices {
  const p = c.preset;
  return {
    direction: p.direction,
    real_pct: p.real_pct,
    ai_media: p.ai_media,
    youtube_pct: p.youtube_pct,
    avg_scene_seconds: p.avg_scene_seconds,
    subtitles: p.subtitles,
    music_enabled: p.music.enabled,
    media_style: p.media_style ?? "real_preferred",
    selection_mode: p.selection_mode ?? "fast",
    llm_economy: p.llm_economy ?? false,
    period_grade: p.period_grade ?? true,
    context_cards: p.context_cards ?? true,
    sfx: p.sfx ?? true,
    film_look: p.film_look ?? true,
    tts_voice: p.tts_voice,
    tts_voice_name: p.tts_voice_name,
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
  const [c, setChoices] = useState<Choices | null>(null);
  const [saveDefault, setSaveDefault] = useState(false);
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

  const channel = channels.find((x) => x.id === channelId) ?? null;

  function pickChannel(ch: Channel) {
    setChannelId(ch.id);
    setChoices(fromChannel(ch));
  }

  function pickAudio(file: File | null) {
    setAudioFile(file);
    setAudioSeconds(null);
    if (!file) return;
    const el = new Audio(URL.createObjectURL(file));
    el.onloadedmetadata = () => setAudioSeconds(el.duration);
  }

  const set = <K extends keyof Choices>(k: K, v: Choices[K]) => setChoices((x) => (x ? { ...x, [k]: v } : x));

  const words = useMemo(() => script.split(/\s+/).filter(Boolean).length, [script]);
  const duration = audioMode === "upload" && audioSeconds ? audioSeconds : (words / WPM) * 60;

  const preview = useMemo(() => {
    if (!c) return null;
    const real = (duration * c.real_pct) / 100;
    const yt = (real * c.youtube_pct) / 100;
    return {
      scenes: duration ? Math.max(1, Math.round(duration / c.avg_scene_seconds)) : 0,
      real,
      yt,
      stock: real - yt,
      ai: duration - real,
    };
  }, [c, duration]);

  const valid = [
    !!channel && !!c && title.trim().length > 0 && words > 0 && (audioMode === "upload" ? !!audioFile : !!c.tts_voice),
    !!c?.direction,
    !!c && c.avg_scene_seconds >= 2 && c.avg_scene_seconds <= 20,
    true,
  ];

  function goNext() {
    const next = step + 1;
    setStep(next);
    if (next !== 3 || !channel || !c) return;
    setEstimate(null);
    api
      .estimate({
        channel_id: channel.id,
        script,
        config: { ...c },
        audio_seconds: audioMode === "upload" ? audioSeconds : null,
        audio_mode: audioMode,
      })
      .then(setEstimate)
      .catch((e) => toast.error((e as Error).message));
  }

  async function submit() {
    if (!channel || !c) return;
    setSubmitting(true);
    const fd = new FormData();
    fd.append("channel_id", String(channel.id));
    fd.append("title", title);
    fd.append("script", script);
    fd.append("config", JSON.stringify(c));
    fd.append("save_as_default", String(saveDefault));
    if (audioMode === "upload" && audioFile) fd.append("audio", audioFile);
    try {
      await api.createProduction(fd);
      toast.success(saveDefault ? "Produção na fila e padrão do canal atualizado" : "Produção na fila");
      router.push("/");
    } catch (e) {
      toast.error((e as Error).message);
      setSubmitting(false);
    }
  }

  const aiLabel = c ? AI_MEDIA.find((m) => m.value === c.ai_media)?.label.toLowerCase() : "";

  return (
    <div className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-5xl flex-col">
      <h1 className="mb-6 text-2xl font-semibold tracking-tight">Nova produção</h1>
      <ol className="mb-6 flex items-center gap-2">
        {STEPS.map((s, i) => (
          <li key={s} className="flex flex-1 items-center gap-2">
            <span
              className={cn(
                "flex size-7 shrink-0 items-center justify-center rounded-md border text-xs",
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
        <CardContent className="flex flex-col gap-6">
          {step === 0 && (
            <>
              <Field
                label="Canal"
                hint={channels.length === 0 ? "Crie um canal primeiro, na tela Canais." : undefined}
              >
                <SimpleSelect
                  value={channelId ? String(channelId) : null}
                  onChange={(v) => {
                    const ch = channels.find((x) => x.id === Number(v));
                    if (ch) pickChannel(ch);
                  }}
                  options={channels.map((x) => ({ value: String(x.id), label: x.name }))}
                  className="sm:w-96"
                />
              </Field>
              <Field label="Título">
                <Input value={title} onChange={(e) => setTitle(e.target.value)} />
              </Field>
              <Field
                label="Roteiro"
                hint={`${words} palavras · duração estimada ≈ ${fmtDuration((words / WPM) * 60)} · o idioma é detectado no roteiro`}
              >
                <Textarea value={script} onChange={(e) => setScript(e.target.value)} className="min-h-72" />
              </Field>
              <Field label="Áudio da narração">
                <RadioGroup value={audioMode} onValueChange={(v) => setAudioMode(v as "tts" | "upload")}>
                  <label className="flex items-center gap-2 text-sm">
                    <RadioGroupItem value="tts" /> Gerar automaticamente com um narrador
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <RadioGroupItem value="upload" /> Enviar áudio (mp3, wav ou m4a)
                  </label>
                </RadioGroup>
              </Field>
              {audioMode === "tts" && c && (
                <Field label="Narrador" hint="Vem do canal; troque aqui só para este vídeo.">
                  <VoicePicker
                    value={c.tts_voice}
                    onChange={(v) =>
                      setChoices((x) => (x ? { ...x, tts_voice: v?.id ?? null, tts_voice_name: v?.name ?? null } : x))
                    }
                  />
                  {!c.tts_voice && (
                    <p className="flex items-center gap-1.5 text-xs text-amber-300">
                      <AlertTriangle className="size-3.5" /> Selecione um narrador.
                    </p>
                  )}
                </Field>
              )}
              {audioMode === "upload" && (
                <div className="flex items-center gap-3">
                  <label className="inline-flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted">
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
            </>
          )}

          {step === 1 && c && (
            <>
              <Field label="Estilo de direção">
                <div className="grid gap-3 sm:grid-cols-3">
                  {directions.map((d) => (
                    <button
                      key={d.id}
                      onClick={() => set("direction", d.id)}
                      className={cn(
                        "overflow-hidden rounded-md border text-left transition-colors hover:border-foreground/40",
                        c.direction === d.id && "border-foreground ring-1 ring-foreground",
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
              </Field>
              <p className="flex items-start gap-2 rounded-md border border-dashed p-4 text-sm text-muted-foreground">
                <Sparkles className="mt-0.5 size-4 shrink-0" />
                <span>
                  Estilo visual, época e sua representação, idioma da narração e buscas (sempre em inglês) são
                  interpretados automaticamente a partir do roteiro: o sistema lê o texto inteiro, entende a história,
                  o lugar, o tempo e o tom, e monta o vídeo em cima disso.
                </span>
              </p>
            </>
          )}

          {step === 2 && c && preview && (
            <>
              <SplitSlider left="Real" right="IA" value={c.real_pct} onChange={(v) => set("real_pct", v)} />
              <Field
                label="Estilo de mídia aceito"
                hint={MEDIA_STYLES.find((m) => m.value === c.media_style)?.hint}
              >
                <SimpleSelect
                  value={c.media_style}
                  onChange={(v) => set("media_style", v as MediaStyle)}
                  options={MEDIA_STYLES}
                  className="sm:w-64"
                />
              </Field>
              <Field label="Tipo de mídia de IA">
                <SimpleSelect
                  value={c.ai_media}
                  onChange={(v) => set("ai_media", v as Choices["ai_media"])}
                  options={AI_MEDIA}
                  disabled={c.real_pct === 100}
                  className="sm:w-64"
                />
              </Field>
              <SplitSlider
                left="YouTube"
                right="Bancos"
                value={c.youtube_pct}
                onChange={(v) => set("youtube_pct", v)}
                disabled={c.real_pct === 0}
              />
              <Field label="Duração média por cena (s)" hint="Entre 2 e 20 s; a direção varia em torno dessa média.">
                <Input
                  type="number"
                  min={2}
                  max={20}
                  step={0.5}
                  value={c.avg_scene_seconds}
                  onChange={(e) => set("avg_scene_seconds", Number(e.target.value))}
                  className="w-32"
                />
              </Field>
              <Field label="Modo de seleção" hint={SELECTION_MODES.find((m) => m.value === c.selection_mode)?.hint}>
                <SimpleSelect
                  value={c.selection_mode}
                  onChange={(v) => set("selection_mode", v as SelectionMode)}
                  options={SELECTION_MODES}
                  className="sm:w-64"
                />
              </Field>
              <div className="flex flex-wrap gap-x-8 gap-y-3">
                <label className="flex items-center gap-2 text-sm">
                  <Switch checked={c.subtitles} onCheckedChange={(v) => set("subtitles", v)} /> Legendas
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <Switch checked={c.music_enabled} onCheckedChange={(v) => set("music_enabled", v)} /> Música de
                  fundo
                </label>
                <label className="flex items-center gap-2 text-sm" title="Leve tratamento de cor uniforme nas cenas de época (ex.: tons quentes e dessaturados no século XIX)">
                  <Switch checked={c.period_grade} onCheckedChange={(v) => set("period_grade", v)} /> Cor de época
                </label>
                <label className="flex items-center gap-2 text-sm" title="Card com lugar e data quando o roteiro muda de época ou de lugar, no idioma do vídeo">
                  <Switch checked={c.context_cards} onCheckedChange={(v) => set("context_cards", v)} /> Card de
                  lugar e data
                </label>
                <label className="flex items-center gap-2 text-sm" title="Whoosh nas transições, impacto nos títulos, riser antes das revelações e teclas no card de lugar (Freesound ou sintetizados)">
                  <Switch checked={c.sfx} onCheckedChange={(v) => set("sfx", v)} /> Efeitos sonoros
                </label>
                <label className="flex items-center gap-2 text-sm" title="Grão de filme e vinheta suaves no vídeo inteiro">
                  <Switch checked={c.film_look} onCheckedChange={(v) => set("film_look", v)} /> Textura de filme
                </label>
              </div>
              <pre className="rounded-md bg-muted/60 p-4 font-mono text-sm leading-relaxed">
                {`Duração ≈ ${fmtDuration(duration)} · ≈ ${preview.scenes} cenas
Real ${fmtDuration(preview.real)}  →  YouTube ${fmtDuration(preview.yt)} · Bancos ${fmtDuration(preview.stock)}
IA   ${fmtDuration(preview.ai)}  →  ${aiLabel}`}
              </pre>
            </>
          )}

          {step === 3 && c && channel && (
            <div className="flex flex-col gap-6 text-sm">
              <dl className="grid grid-cols-[180px_1fr] gap-x-4 gap-y-2">
                <dt className="text-muted-foreground">Canal</dt>
                <dd>{channel.name}</dd>
                <dt className="text-muted-foreground">Título</dt>
                <dd>{title}</dd>
                <dt className="text-muted-foreground">Roteiro</dt>
                <dd>{words} palavras · idioma detectado automaticamente</dd>
                <dt className="text-muted-foreground">Áudio</dt>
                <dd>{audioMode === "upload" ? audioFile?.name : `Narrador ${c.tts_voice_name ?? "—"}`}</dd>
                <dt className="text-muted-foreground">Direção</dt>
                <dd>{directions.find((d) => d.id === c.direction)?.name}</dd>
                <dt className="text-muted-foreground">Estilo visual e época</dt>
                <dd>Interpretados do roteiro · buscas em inglês</dd>
                <dt className="text-muted-foreground">Composição</dt>
                <dd>
                  Real {c.real_pct}% (YouTube {c.youtube_pct}% / Bancos {100 - c.youtube_pct}%) · IA {100 - c.real_pct}%
                  ({aiLabel})
                </dd>
                <dt className="text-muted-foreground">Estilo e seleção</dt>
                <dd>
                  {MEDIA_STYLES.find((m) => m.value === c.media_style)?.label} ·{" "}
                  {SELECTION_MODES.find((m) => m.value === c.selection_mode)?.label}
                </dd>
                <dt className="text-muted-foreground">Ritmo</dt>
                <dd>{c.avg_scene_seconds}s por cena</dd>
                <dt className="text-muted-foreground">Acabamento</dt>
                <dd>
                  Legendas {c.subtitles ? "sim" : "não"} · Música {c.music_enabled ? "sim" : "não"} · Efeitos sonoros{" "}
                  {c.sfx ? "sim" : "não"} · Textura de filme {c.film_look ? "sim" : "não"}
                </dd>
                <dt className="text-muted-foreground">Época</dt>
                <dd>
                  Cor de época {c.period_grade ? "sim" : "não"} · Card de lugar e data {c.context_cards ? "sim" : "não"}
                </dd>
              </dl>

              {!estimate ? (
                <p className="text-muted-foreground">Calculando estimativa…</p>
              ) : (
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="rounded-md border p-4">
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
                  <div className="rounded-md border p-4">
                    <p className="mb-2 font-medium">Uso de cotas</p>
                    <dl className="grid grid-cols-2 gap-y-1 text-muted-foreground">
                      <dt>Imagens Darkvi</dt>
                      <dd className={cn("text-right", !estimate.quotas.darkvi_enough && "text-amber-300")}>
                        {estimate.quotas.darkvi_images_needed} /{" "}
                        {estimate.quotas.darkvi_remaining ?? "saldo desconhecido"}
                      </dd>
                      <dt>Cenas do YouTube</dt>
                      <dd
                        className={cn(
                          "text-right",
                          estimate.quotas.youtube_scenes_fit < estimate.quotas.youtube_scenes && "text-amber-300",
                        )}
                      >
                        {estimate.quotas.youtube_scenes_fit} de {estimate.quotas.youtube_scenes} cabem hoje
                      </dd>
                      <dt>Cota YouTube restante</dt>
                      <dd className="text-right">{estimate.quotas.youtube_available} un.</dd>
                    </dl>
                    {estimate.quotas.youtube_scenes_fit < estimate.quotas.youtube_scenes && (
                      <p className="mt-2 text-xs text-amber-300">
                        A cota do YouTube de hoje cobre {estimate.quotas.youtube_scenes_fit} de{" "}
                        {estimate.quotas.youtube_scenes} cenas. As restantes usarão bancos de vídeo.
                      </p>
                    )}
                    {!estimate.quotas.darkvi_enough && (
                      <p className="mt-2 text-xs text-amber-300">
                        O saldo de imagens de hoje não cobre esta produção; as cenas excedentes vão para os bancos.
                      </p>
                    )}
                  </div>
                </div>
              )}

              <label className="flex items-start gap-3 rounded-md border p-4">
                <Switch
                  checked={c.llm_economy}
                  onCheckedChange={(v) => set("llm_economy", v)}
                  className="mt-0.5"
                />
                <span>
                  <span className="font-medium">Econômico (sem pressa)</span>
                  <span className="block text-xs text-muted-foreground">
                    O planejamento vai pela Batch API do Claude: metade do preço, mas a produção fica “Aguardando
                    IA” por até 24 h (normalmente menos de 1 h). Bom para enfileirar à noite.
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-3 rounded-md border p-4">
                <Switch checked={saveDefault} onCheckedChange={setSaveDefault} className="mt-0.5" />
                <span>
                  <span className="font-medium">Salvar estas configurações como padrão do canal {channel.name}</span>
                  <span className="block text-xs text-muted-foreground">
                    Narrador, direção, composição, ritmo e acabamento vêm preenchidos assim nos próximos vídeos deste
                    canal.
                  </span>
                </span>
              </label>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="mt-6 flex justify-between">
        <Button variant="outline" size="lg" onClick={() => setStep((s) => s - 1)} disabled={step === 0}>
          <ArrowLeft /> Voltar
        </Button>
        {step < 3 ? (
          <Button size="lg" onClick={goNext} disabled={!valid[step]}>
            Próximo <ArrowRight />
          </Button>
        ) : (
          <Button size="lg" onClick={submit} disabled={submitting}>
            <Play /> {submitting ? "Enviando…" : "Iniciar produção"}
          </Button>
        )}
      </div>
    </div>
  );
}
