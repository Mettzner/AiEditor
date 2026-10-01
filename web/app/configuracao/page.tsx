"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, Link2, PlugZap, Save, XCircle } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Field, SimpleSelect } from "@/components/fields";
import { api, type Price, type SettingsPayload, type Track } from "@/lib/api";

const KEYS: { id: string; label: string; test?: string; note?: string }[] = [
  { id: "darkvi", label: "Darkvi (TTS + imagens)", test: "darkvi" },
  { id: "anthropic", label: "Anthropic (Claude)", test: "anthropic" },
  { id: "pexels", label: "Pexels", test: "pexels" },
  { id: "pixabay", label: "Pixabay", test: "pixabay" },
  { id: "youtube", label: "YouTube Data API", note: "Fase 3" },
  { id: "gemini", label: "Google Gemini", note: "Fase 2 (análise de vídeo)" },
  { id: "fal", label: "fal.ai (vídeo IA)", note: "Fase 3" },
  { id: "elevenlabs", label: "ElevenLabs (música)", note: "Fase 4" },
  { id: "openai", label: "OpenAI", note: "opcional" },
];

type Settings = SettingsPayload["settings"];

function TestResult({ r }: { r?: { ok: boolean; detail: unknown } }) {
  if (!r) return null;
  const detail = typeof r.detail === "string" ? r.detail : JSON.stringify(r.detail);
  return (
    <p className={`flex items-start gap-1 text-xs ${r.ok ? "text-emerald-300" : "text-red-300"}`}>
      {r.ok ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0" /> : <XCircle className="mt-0.5 size-3.5 shrink-0" />}
      <span className="break-all">{detail}</span>
    </p>
  );
}

export default function ConfiguracaoPage() {
  const [data, setData] = useState<SettingsPayload | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [tests, setTests] = useState<Record<string, { ok: boolean; detail: unknown }>>({});
  const [oauthJson, setOauthJson] = useState("");
  const [prices, setPrices] = useState<Price[]>([]);
  const [tracks, setTracks] = useState<Track[]>([]);

  async function load() {
    try {
      const d = await api.settings();
      setData(d);
      setSettings(d.settings);
      setPrices(await api.prices());
      setTracks(await api.tracks());
    } catch (e) {
      toast.error(`Backend indisponível: ${(e as Error).message}`);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function saveKey(id: string, value: string) {
    try {
      const d = await api.saveSettings({ secrets: { [id]: value } });
      setData(d);
      setKeys((k) => ({ ...k, [id]: "" }));
      toast.success(value ? "Chave salva no Windows Credential Manager" : "Chave removida");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function test(provider: string) {
    setTests((t) => ({ ...t, [provider]: { ok: true, detail: "testando…" } }));
    try {
      const r = await api.test(provider);
      setTests((t) => ({ ...t, [provider]: r }));
    } catch (e) {
      setTests((t) => ({ ...t, [provider]: { ok: false, detail: (e as Error).message } }));
    }
  }

  async function saveSettings() {
    if (!settings) return;
    try {
      const d = await api.saveSettings({ settings });
      setData(d);
      setSettings(d.settings);
      await api.savePrices(prices.map((p) => ({ id: p.id, price: p.price })));
      await api.saveTracks(tracks);
      toast.success("Configuração salva");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function connectGoogle() {
    try {
      const { url } = await api.googleStart();
      window.open(url, "_blank");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  if (!data || !settings) return <p className="text-sm text-muted-foreground">Carregando…</p>;

  const s = settings;
  const patch = (path: string[], value: unknown) =>
    setSettings((prev) => {
      const next = structuredClone(prev) as Settings;
      let cur = next;
      path.slice(0, -1).forEach((k) => (cur = cur[k]));
      cur[path[path.length - 1]] = value;
      return next;
    });

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Configuração</h1>
          <p className="text-sm text-muted-foreground">Chaves, provedores, render e biblioteca de músicas.</p>
        </div>
        <Button onClick={saveSettings}>
          <Save /> Salvar
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Chaves de API</CardTitle>
          <CardDescription>Guardadas no Windows Credential Manager, nunca em arquivo.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {KEYS.map((k) => (
            <div key={k.id} className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2 text-sm">
                <span className="font-medium">{k.label}</span>
                {data.secrets[k.id] ? (
                  <Badge variant="outline" className="border-emerald-500/40 text-emerald-300">
                    configurada
                  </Badge>
                ) : (
                  <Badge variant="outline">não configurada</Badge>
                )}
                {k.note && <span className="text-xs text-muted-foreground">{k.note}</span>}
              </div>
              <div className="flex gap-2">
                <Input
                  type="password"
                  placeholder={data.secrets[k.id] ? "•••••••• (digite para trocar)" : "cole a chave"}
                  value={keys[k.id] ?? ""}
                  onChange={(e) => setKeys((x) => ({ ...x, [k.id]: e.target.value }))}
                />
                <Button variant="outline" onClick={() => saveKey(k.id, keys[k.id] ?? "")} disabled={!keys[k.id]}>
                  Salvar
                </Button>
                {k.test && (
                  <Button variant="outline" onClick={() => test(k.test!)} disabled={!data.secrets[k.id]}>
                    <PlugZap /> Testar conexão
                  </Button>
                )}
              </div>
              <TestResult r={tests[k.test ?? ""]} />
            </div>
          ))}
          {s.darkvi?.remaining != null && (
            <p className="text-sm text-muted-foreground">
              Saldo diário de imagens da Darkvi: <b className="text-foreground">{s.darkvi.remaining}</b>
              {s.darkvi.limit ? ` / ${s.darkvi.limit}` : ""} (lido em{" "}
              {new Date(s.darkvi.updated_at).toLocaleString("pt-BR")})
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Google Drive</CardTitle>
          <CardDescription>
            Crie um client OAuth no Google Cloud Console com o redirect{" "}
            <code className="text-xs">http://localhost:8000/auth/google/callback</code>, cole o JSON abaixo e conecte.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex items-center gap-2 text-sm">
            Status:
            {data.google_connected ? (
              <Badge variant="outline" className="border-emerald-500/40 text-emerald-300">
                conectado
              </Badge>
            ) : (
              <Badge variant="outline">desconectado</Badge>
            )}
            {data.secrets.google_oauth_client && <span className="text-xs text-muted-foreground">client salvo</span>}
          </div>
          <Textarea
            rows={3}
            placeholder='{"web": {"client_id": "...", "client_secret": "...", ...}}'
            value={oauthJson}
            onChange={(e) => setOauthJson(e.target.value)}
          />
          <div className="flex gap-2">
            <Button variant="outline" disabled={!oauthJson} onClick={() => saveKey("google_oauth_client", oauthJson)}>
              Salvar client
            </Button>
            <Button variant="outline" disabled={!data.secrets.google_oauth_client} onClick={connectGoogle}>
              <Link2 /> Conectar conta
            </Button>
            <Button variant="outline" disabled={!data.google_connected} onClick={() => test("google")}>
              <PlugZap /> Testar
            </Button>
            <Button variant="ghost" onClick={load}>
              Atualizar status
            </Button>
          </div>
          <TestResult r={tests.google} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Bancos de vídeo</CardTitle>
          <CardDescription>Ativação e prioridade (1 = primeiro).</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {(s.stock_providers as { id: string; enabled: boolean; priority: number }[]).map((p, i) => (
            <div key={p.id} className="flex items-center gap-4 text-sm">
              <Switch
                checked={p.enabled}
                onCheckedChange={(v) => patch(["stock_providers", String(i), "enabled"], v)}
                disabled={p.id === "storyblocks" || p.id === "shutterstock"}
              />
              <span className="w-32 capitalize">{p.id}</span>
              <Input
                type="number"
                min={1}
                className="w-20"
                value={p.priority}
                onChange={(e) => patch(["stock_providers", String(i), "priority"], Number(e.target.value))}
              />
              {(p.id === "storyblocks" || p.id === "shutterstock") && (
                <span className="text-xs text-muted-foreground">pago · adapter futuro</span>
              )}
            </div>
          ))}
          <div className="flex items-center gap-4 text-sm">
            <Switch checked={s.youtube.enabled} onCheckedChange={(v) => patch(["youtube", "enabled"], v)} />
            <span className="w-32">YouTube</span>
            <Switch
              checked={s.youtube.creative_commons_only}
              onCheckedChange={(v) => patch(["youtube", "creative_commons_only"], v)}
            />
            <span>Só Creative Commons</span>
          </div>
          {!s.youtube.creative_commons_only && (
            <p className="text-xs text-amber-300">
              Sem o filtro Creative Commons, o risco de Content ID e strikes aumenta muito.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Render</CardTitle>
          <CardDescription>Qualidade usa a CPU (libx264). Rápido usa a RX 580 (h264_amf).</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Field label="Modo">
            <SimpleSelect
              value={s.render.mode}
              onChange={(v) => patch(["render", "mode"], v)}
              options={[
                { value: "quality", label: "Qualidade (CPU / x264)" },
                { value: "fast", label: "Rápido (GPU / AMF)" },
              ]}
            />
          </Field>
          <Field label="Preset do x264">
            <SimpleSelect
              value={s.render.x264_preset}
              onChange={(v) => patch(["render", "x264_preset"], v)}
              options={["ultrafast", "superfast", "veryfast", "faster", "fast", "medium"].map((p) => ({
                value: p,
                label: p,
              }))}
            />
          </Field>
          <Field label="CRF (x264)">
            <Input
              type="number"
              min={14}
              max={30}
              value={s.render.crf}
              onChange={(e) => patch(["render", "crf"], Number(e.target.value))}
            />
          </Field>
          <Field label="Bitrate AMF">
            <Input value={s.render.amf_bitrate} onChange={(e) => patch(["render", "amf_bitrate"], e.target.value)} />
          </Field>
          <Field label="Pasta do FFmpeg (opcional)" hint="Vazio = usar o FFmpeg do PATH.">
            <Input
              value={s.ffmpeg_dir ?? ""}
              placeholder="C:\ffmpeg\bin"
              onChange={(e) => patch(["ffmpeg_dir"], e.target.value || null)}
            />
          </Field>
          <Field label="Modelo do whisper" hint="small é rápido; medium é mais preciso e ~2× mais lento.">
            <SimpleSelect
              value={s.transcription.model}
              onChange={(v) => patch(["transcription", "model"], v)}
              options={["base", "small", "medium", "large-v3"].map((m) => ({ value: m, label: m }))}
            />
          </Field>
          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <div>
              <Button variant="outline" onClick={() => test("ffmpeg")}>
                <PlugZap /> Testar FFmpeg
              </Button>
            </div>
            <TestResult r={tests.ffmpeg} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Biblioteca de músicas</CardTitle>
          <CardDescription>
            Coloque faixas em <code className="text-xs">{s.music.library_dir}</code> e marque o clima (tags separadas
            por vírgula).
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {tracks.length === 0 && <p className="text-sm text-muted-foreground">Nenhuma faixa na pasta.</p>}
          {tracks.map((t, i) => (
            <div key={t.file} className="grid grid-cols-[1fr_1fr_1fr] items-center gap-2 text-sm">
              <span className="truncate" title={t.file}>
                {t.file}
              </span>
              <Input
                placeholder="tenso, sombrio, lento"
                value={t.mood.join(", ")}
                onChange={(e) =>
                  setTracks((ts) =>
                    ts.map((x, j) =>
                      j === i ? { ...x, mood: e.target.value.split(",").map((m) => m.trim()).filter(Boolean) } : x,
                    ),
                  )
                }
              />
              <Input
                placeholder="licença / origem"
                value={t.license}
                onChange={(e) => setTracks((ts) => ts.map((x, j) => (j === i ? { ...x, license: e.target.value } : x)))}
              />
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Tabela de preços</CardTitle>
          <CardDescription>Usada na estimativa de custo. Os preços mudam rápido: mantenha atualizada.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {prices.map((p, i) => (
            <div key={p.id} className="grid grid-cols-[120px_1fr_100px] items-center gap-2 text-sm">
              <span>{p.provider}</span>
              <span className="text-muted-foreground">
                {p.unit} {p.note && `· ${p.note}`}
              </span>
              <Input
                type="number"
                step={0.01}
                value={p.price}
                onChange={(e) =>
                  setPrices((ps) => ps.map((x, j) => (j === i ? { ...x, price: Number(e.target.value) } : x)))
                }
              />
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Pastas locais</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-1 text-sm text-muted-foreground">
          <p>
            Cache: <code className="text-xs">{s.folders.cache}</code>
          </p>
          <p>
            Produções: <code className="text-xs">{s.folders.jobs}</code>
          </p>
          <p>
            Fontes extras (.ttf): <code className="text-xs">{s.fonts_dir}</code>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
