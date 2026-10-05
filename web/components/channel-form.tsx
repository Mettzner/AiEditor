"use client";

import { useState } from "react";
import { Save, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { ConfirmDelete } from "@/components/confirm-delete";
import { Field, SimpleSelect } from "@/components/fields";
import { VoicePicker } from "@/components/voice-picker";
import { api, DEFAULT_PRESET, type Channel, type Preset } from "@/lib/api";

export const AI_MEDIA = [
  { value: "both", label: "Vídeos e imagens" },
  { value: "video", label: "Só vídeos" },
  { value: "image", label: "Só imagens" },
];

export const LANGS = [
  { value: "en", label: "Inglês" },
  { value: "pt", label: "Português" },
  { value: "es", label: "Espanhol" },
];

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-4">
      <div>
        <h3 className="text-sm font-medium text-muted-foreground">{title}</h3>
        {hint && <p className="text-xs text-muted-foreground/80">{hint}</p>}
      </div>
      {children}
    </section>
  );
}

/**
 * Identidade fixa do canal. Direção, idiomas, estilo visual, composição e ritmo são escolhidos na
 * Criação de cada vídeo (e podem ser salvos como padrão do canal no último passo).
 */
export function ChannelForm({
  channel,
  onSaved,
  onDeleted,
}: {
  channel: Channel | null;
  onSaved: (c: Channel) => void;
  onDeleted: () => void;
}) {
  const [name, setName] = useState(channel?.name ?? "");
  const [preset, setPreset] = useState<Preset>(channel?.preset ?? DEFAULT_PRESET);
  const [saving, setSaving] = useState(false);

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
      const { productions_removed: n } = await api.deleteChannel(channel.id);
      toast.success(n ? `Canal excluído com ${n} produç${n === 1 ? "ão" : "ões"}` : "Canal excluído");
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

  return (
    <div className="flex flex-col gap-7">
      <Field label="Nome do canal">
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="The Fourth Encounter" />
      </Field>

      <Separator />
      <Section title="Narração">
        <Field label="Narrador padrão" hint="Pode ser trocado na Criação de cada vídeo.">
          <VoicePicker
            value={preset.tts_voice}
            onChange={(v) => setPreset((p) => ({ ...p, tts_voice: v?.id ?? null, tts_voice_name: v?.name ?? null }))}
          />
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
          <ConfirmDelete
            title={`Excluir o canal "${channel.name}"?`}
            trigger={<Button variant="destructive" />}
            label={
              <>
                <Trash2 /> Excluir canal
              </>
            }
            onConfirm={remove}
          >
            Apaga o canal e todas as produções dele, com os arquivos neste computador (narração, cenas e vídeos).
            Não dá para desfazer. As cópias já enviadas ao Google Drive não são apagadas.
          </ConfirmDelete>
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
