"use client";

import { useEffect, useState } from "react";
import { Play, Save, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Field, SimpleSelect, SplitSlider } from "@/components/fields";
import { api, DEFAULT_PRESET, type Channel, type Direction, type Preset, type Voice } from "@/lib/api";

const LANGS = [
  { value: "en", label: "Inglês" },
  { value: "pt", label: "Português" },
  { value: "es", label: "Espanhol" },
];

export const AI_MEDIA = [
  { value: "both", label: "Vídeos e imagens" },
  { value: "video", label: "Só vídeos" },
  { value: "image", label: "Só imagens" },
];

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-4">
      <h3 className="text-sm font-medium text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

export function ChannelForm({
  channel,
  directions,
  onSaved,
  onDeleted,
}: {
  channel: Channel | null;
  directions: Direction[];
  onSaved: (c: Channel) => void;
  onDeleted: () => void;
}) {
  const [name, setName] = useState(channel?.name ?? "");
  const [preset, setPreset] = useState<Preset>(channel?.preset ?? DEFAULT_PRESET);
  const [voices, setVoices] = useState<Voice[] | null>(null);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api
      .voices()
      .then(setVoices)
      .catch((e) => setVoiceError((e as Error).message));
  }, []);

  const set = <K extends keyof Preset>(k: K, v: Preset[K]) => setPreset((p) => ({ ...p, [k]: v }));

  async function save() {
    if (!name.trim()) return toast.error("Dê um nome ao canal");
    setSaving(true);
    try {
      const saved = channel ? await api.updateChannel(channel.id, name, preset) : await api.createChannel(name, preset);
      toast.success("Canal salvo");
      onSaved(saved);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!channel) return;
    try {
      await api.deleteChannel(channel.id);
      toast.success("Canal excluído");
      onDeleted();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function uploadRef(file: File) {
    if (!channel) return toast.error("Salve o canal antes de enviar a imagem");
    try {
      const c = await api.uploadReference(channel.id, file);
      setPreset(c.preset);
      toast.success("Imagem de referência enviada");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  const voice = voices?.find((v) => v.id === preset.tts_voice);

  return (
    <div className="flex flex-col gap-7">
      <Section title="Identidade">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Nome do canal">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="The Fourth Encounter" />
          </Field>
          <Field label="Direção padrão">
            <SimpleSelect
              value={preset.direction}
              onChange={(v) => set("direction", v)}
              options={directions.map((d) => ({ value: d.id, label: d.name }))}
            />
          </Field>
          <Field label="Idioma do roteiro / narração">
            <SimpleSelect value={preset.language} onChange={(v) => set("language", v)} options={LANGS} />
          </Field>
          <Field label="Idioma das buscas">
            <SimpleSelect value={preset.search_language} onChange={(v) => set("search_language", v)} options={LANGS} />
          </Field>
        </div>
        <Field
          label="Estilo visual"
          hint="Texto livre, injetado nos prompts de busca, geração e ranqueamento."
        >
          <Textarea
            value={preset.visual_style}
            onChange={(e) => set("visual_style", e.target.value)}
            placeholder="noturno, granulado, tons frios"
            rows={2}
          />
        </Field>
      </Section>

      <Separator />
      <Section title="Narração">
        <Field
          label="Voz TTS (Darkvi)"
          hint={voiceError ? `Não foi possível carregar as vozes: ${voiceError}. Digite o id da voz.` : undefined}
        >
          {voices && voices.length > 0 ? (
            <div className="flex gap-2">
              <SimpleSelect
                value={preset.tts_voice}
                onChange={(v) => set("tts_voice", v)}
                options={voices.map((v) => ({
                  value: v.id,
                  label: [v.name, v.language, v.accent].filter(Boolean).join(" · "),
                }))}
              />
              <Button
                variant="outline"
                size="icon"
                aria-label="Ouvir prévia"
                disabled={!voice?.preview_url}
                onClick={() => voice?.preview_url && new Audio(voice.preview_url).play()}
              >
                <Play />
              </Button>
            </div>
          ) : (
            <Input
              value={preset.tts_voice ?? ""}
              onChange={(e) => set("tts_voice", e.target.value || null)}
              placeholder="id da voz"
            />
          )}
        </Field>
        <Field
          label="Imagem de referência de estilo (opcional)"
          hint="Enviada à Darkvi nas gerações de imagem. Requer plano pago."
        >
          <div className="flex items-center gap-3">
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-1.5 text-sm hover:bg-muted">
              <Upload className="size-4" /> Escolher imagem
              <input
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => e.target.files?.[0] && uploadRef(e.target.files[0])}
              />
            </label>
            <span className="truncate text-xs text-muted-foreground">
              {preset.reference_image ? preset.reference_image.split(/[\\/]/).pop() : "nenhuma"}
            </span>
          </div>
        </Field>
      </Section>

      <Separator />
      <Section title="Composição e ritmo">
        <SplitSlider left="Real" right="IA" value={preset.real_pct} onChange={(v) => set("real_pct", v)} />
        <SplitSlider
          left="YouTube"
          right="Bancos"
          value={preset.youtube_pct}
          onChange={(v) => set("youtube_pct", v)}
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Tipo de mídia de IA">
            <SimpleSelect
              value={preset.ai_media}
              onChange={(v) => set("ai_media", v as Preset["ai_media"])}
              options={AI_MEDIA}
            />
          </Field>
          <Field label="Duração média por cena (s)">
            <Input
              type="number"
              min={2}
              max={20}
              step={0.5}
              value={preset.avg_scene_seconds}
              onChange={(e) => set("avg_scene_seconds", Number(e.target.value))}
            />
          </Field>
        </div>
      </Section>

      <Separator />
      <Section title="Acabamento">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Fonte dos textos">
            <Input value={preset.font} onChange={(e) => set("font", e.target.value)} />
          </Field>
          <Field label="Cor principal">
            <Input type="color" value={preset.color_primary} onChange={(e) => set("color_primary", e.target.value)} />
          </Field>
          <Field label="Cor de destaque">
            <Input type="color" value={preset.color_accent} onChange={(e) => set("color_accent", e.target.value)} />
          </Field>
        </div>
        <div className="flex items-center gap-3">
          <Switch checked={preset.subtitles} onCheckedChange={(v) => set("subtitles", v)} />
          <span className="text-sm">Legendas</span>
        </div>
        {preset.subtitles && (
          <div className="grid gap-4 sm:grid-cols-4">
            <Field label="Fonte">
              <Input
                value={preset.subtitle_style.font}
                onChange={(e) => set("subtitle_style", { ...preset.subtitle_style, font: e.target.value })}
              />
            </Field>
            <Field label="Tamanho">
              <Input
                type="number"
                value={preset.subtitle_style.size}
                onChange={(e) => set("subtitle_style", { ...preset.subtitle_style, size: Number(e.target.value) })}
              />
            </Field>
            <Field label="Cor">
              <Input
                type="color"
                value={preset.subtitle_style.color}
                onChange={(e) => set("subtitle_style", { ...preset.subtitle_style, color: e.target.value })}
              />
            </Field>
            <Field label="Posição">
              <SimpleSelect
                value={preset.subtitle_style.position}
                onChange={(v) =>
                  set("subtitle_style", { ...preset.subtitle_style, position: v as "bottom" | "middle" | "top" })
                }
                options={[
                  { value: "bottom", label: "Embaixo" },
                  { value: "middle", label: "Meio" },
                  { value: "top", label: "Em cima" },
                ]}
              />
            </Field>
          </div>
        )}
        <div className="flex items-center gap-3">
          <Switch checked={preset.music.enabled} onCheckedChange={(v) => set("music", { ...preset.music, enabled: v })} />
          <span className="text-sm">Música de fundo</span>
        </div>
        {preset.music.enabled && (
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Clima padrão">
              <Input value={preset.music.mood} onChange={(e) => set("music", { ...preset.music, mood: e.target.value })} />
            </Field>
            <Field label="Fonte">
              <SimpleSelect
                value={preset.music.source}
                onChange={(v) => set("music", { ...preset.music, source: v as Preset["music"]["source"] })}
                options={[
                  { value: "library", label: "Biblioteca" },
                  { value: "library_then_generate", label: "Biblioteca, depois geração" },
                  { value: "generate", label: "Geração (ElevenLabs)" },
                ]}
              />
            </Field>
            <Field label="Volume (dB)">
              <Input
                type="number"
                step={1}
                value={preset.music.volume_db}
                onChange={(e) => set("music", { ...preset.music, volume_db: Number(e.target.value) })}
              />
            </Field>
          </div>
        )}
      </Section>

      <Separator />
      <Section title="Destino">
        <Field label="Pasta no Google Drive">
          <Input value={preset.drive_folder} onChange={(e) => set("drive_folder", e.target.value)} />
        </Field>
        <div className="flex items-center gap-3">
          <Switch
            checked={preset.drive_subfolder_per_production}
            onCheckedChange={(v) => set("drive_subfolder_per_production", v)}
          />
          <span className="text-sm">Criar subpasta por produção</span>
        </div>
      </Section>

      <div className="flex justify-between border-t pt-5">
        {channel ? (
          <Button variant="destructive" onClick={remove}>
            <Trash2 /> Excluir canal
          </Button>
        ) : (
          <span />
        )}
        <Button onClick={save} disabled={saving}>
          <Save /> {saving ? "Salvando…" : "Salvar canal"}
        </Button>
      </div>
    </div>
  );
}
