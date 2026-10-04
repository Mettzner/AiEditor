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
import { ModelDownloadPanel } from "@/components/model-download";
import { api, type AppInfo, type Price, type SettingsPayload, type Track, type YoutubeQuota } from "@/lib/api";

const KEYS: { id: string; label: string; test?: string; note?: string }[] = [
  { id: "darkvi", label: "Darkvi (TTS + imagens)", test: "darkvi" },
  { id: "anthropic", label: "Anthropic (Claude)", test: "anthropic" },
  { id: "pexels", label: "Pexels", test: "pexels" },
  { id: "pixabay", label: "Pixabay", test: "pixabay" },
  { id: "youtube", label: "YouTube Data API", test: "youtube", note: "o teste gasta 1 unidade" },
  { id: "gemini", label: "Google Gemini", test: "gemini", note: "avalia a folha de miniaturas; sem chave, só ranking de texto" },
  {
    id: "freesound",
    label: "Freesound (efeitos sonoros)",
    test: "freesound",
    note: "grátis em freesound.org/apiv2/apply; sem chave, os efeitos são sintetizados",
  },
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
  const [ytQuota, setYtQuota] = useState<YoutubeQuota | null>(null);
  const [info, setInfo] = useState<AppInfo | null>(null);

  async function load() {
    try {
      const d = await api.settings();
      setData(d);
      setSettings(d.settings);
      setPrices(await api.prices());
      setTracks(await api.tracks());
      setYtQuota(await api.youtubeQuota());
      setInfo(await api.appInfo());
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
      const { url, opened } = await api.googleStart();
      if (!opened) window.open(url, "_blank"); // no app de desktop o backend já abriu o navegador padrão
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
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Configuração</h1>
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
            {data.google_client_embedded
              ? "Esta instalação já traz o client OAuth do AiEditor: basta conectar com a sua conta Google."
              : "Crie um client OAuth do tipo “App para computador” no Google Cloud Console, cole o JSON abaixo e conecte."}
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
            <span className="text-xs text-muted-foreground">sempre só Creative Commons, em HD</span>
          </div>
          {ytQuota && (
            <div className="rounded-md border p-3 text-sm">
              <p>
                Cota do YouTube hoje ({ytQuota.day}, horário do Pacífico):{" "}
                <b className={ytQuota.exhausted || ytQuota.searches_left === 0 ? "text-amber-300" : ""}>
                  {ytQuota.available} un. disponíveis
                </b>{" "}
                · ≈ {ytQuota.searches_left} cena(s) · {ytQuota.used} usadas de {ytQuota.daily_quota} (reserva de{" "}
                {ytQuota.reserve})
              </p>
              <p className="text-xs text-muted-foreground">
                {ytQuota.exhausted
                  ? "O Google recusou por cota hoje: as cenas do YouTube vão para os bancos até a meia-noite do Pacífico."
                  : "Zera à meia-noite do Pacífico (4h ou 5h em Brasília). Cada cena do YouTube gasta 101 unidades; buscas em cache não gastam."}
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Modelos do Claude por etapa</CardTitle>
          <CardDescription>
            Tarefas simples usam modelos mais baratos. O custo e os tokens de cada produção aparecem no card e em
            llm_usage.json; os preços por modelo estão na tabela de preços abaixo.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-3">
          {(
            [
              ["plan", "Brief + planejamento das cenas"],
              ["rewrite", "Reescrita de queries"],
              ["overlay", "Correção de idioma dos overlays"],
            ] as const
          ).map(([task, label]) => (
            <Field key={task} label={label}>
              <SimpleSelect
                value={s.llm?.[task]?.model ?? null}
                onChange={(v) => patch(["llm", task, "model"], v)}
                options={[
                  { value: "claude-haiku-4-5", label: "Haiku 4.5 (mais barato)" },
                  { value: "claude-sonnet-5-5", label: "Sonnet 5.5" },
                  { value: "claude-opus-5-5", label: "Opus 5.5 (mais caro)" },
                ]}
              />
            </Field>
          ))}
          <Field label="Esforço do planejamento" hint="Raciocínio é cobrado como saída; baixo passou nos testes.">
            <SimpleSelect
              value={s.llm?.plan?.effort ?? "low"}
              onChange={(v) => patch(["llm", "plan", "effort"], v)}
              options={["low", "medium", "high"].map((e) => ({ value: e, label: e }))}
            />
          </Field>
          <Field label="Validade do cache do prompt" hint="1 h custa mais para gravar; vale se as etapas ficarem distantes.">
            <SimpleSelect
              value={s.llm?.cache_ttl ?? "5m"}
              onChange={(v) => patch(["llm", "cache_ttl"], v)}
              options={[
                { value: "5m", label: "5 minutos" },
                { value: "1h", label: "1 hora" },
              ]}
            />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Seleção de cenas</CardTitle>
          <CardDescription>
            Cada cena: busca, filtro técnico, top {s.selection.prerank_keep} por texto, 1 folha de miniaturas na IA de
            visão (+1 no desempate) e download só do vencedor.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-3">
          <Field label="Modelo de visão (Gemini Flash)" hint="O teste do Gemini lista os modelos disponíveis.">
            <Input value={s.selection.gemini_model} onChange={(e) => patch(["selection", "gemini_model"], e.target.value)} />
          </Field>
          <Field label="Nota mínima" hint="Abaixo disso, tenta a próxima fonte.">
            <Input
              type="number"
              step={0.5}
              value={s.selection.min_score}
              onChange={(e) => patch(["selection", "min_score"], Number(e.target.value))}
            />
          </Field>
          <Field label="Aceite direto (nota / vantagem)" hint="Acima disso, não há desempate.">
            <div className="flex gap-2">
              <Input
                type="number"
                step={0.5}
                value={s.selection.accept_score}
                onChange={(e) => patch(["selection", "accept_score"], Number(e.target.value))}
              />
              <Input
                type="number"
                step={0.5}
                value={s.selection.accept_gap}
                onChange={(e) => patch(["selection", "accept_gap"], Number(e.target.value))}
              />
            </div>
          </Field>
          <Field label="Franquias sempre bloqueadas" className="sm:col-span-3" hint="Separadas por vírgula. Valem em qualquer cena, mesmo quando o estilo estilizado é aceito.">
            <Textarea
              rows={2}
              value={(s.selection.blocked_franchises ?? []).join(", ")}
              onChange={(e) =>
                patch(
                  ["selection", "blocked_franchises"],
                  e.target.value.split(",").map((x: string) => x.trim()).filter(Boolean),
                )
              }
            />
          </Field>
          {s.gemini?.blocked_until && new Date(s.gemini.blocked_until) > new Date() && (
            <p className="text-sm text-amber-300 sm:col-span-3">
              Gemini pausado até {new Date(s.gemini.blocked_until).toLocaleString("pt-BR")}: {s.gemini.blocked_reason}.
              Até lá, as cenas são escolhidas pelo ranking de texto. O plano gratuito do Google permite poucas
              avaliações por dia; ative o faturamento no Google AI Studio para usar a validação visual em vídeos
              completos.
            </p>
          )}
          <Field label="Cache de buscas (dias)">
            <Input
              type="number"
              min={1}
              value={s.selection.search_cache_days}
              onChange={(e) => patch(["selection", "search_cache_days"], Number(e.target.value))}
            />
          </Field>
          <Field label="Queries por cena (bancos / YouTube)">
            <div className="flex gap-2">
              <Input
                type="number"
                min={1}
                max={3}
                value={s.selection.stock_queries_per_scene}
                onChange={(e) => patch(["selection", "stock_queries_per_scene"], Number(e.target.value))}
              />
              <Input
                type="number"
                min={1}
                max={3}
                value={s.selection.youtube_queries_per_scene}
                onChange={(e) => patch(["selection", "youtube_queries_per_scene"], Number(e.target.value))}
              />
            </div>
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Render</CardTitle>
          <CardDescription>
            Qualidade usa a CPU (libx264). Rápido usa a placa de vídeo AMD (h264_amf) e só aparece quando ela funciona
            neste PC.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Field label="Modo">
            <SimpleSelect
              value={s.render.mode}
              onChange={(v) => patch(["render", "mode"], v)}
              options={[
                { value: "quality", label: "Qualidade (CPU / x264)" },
                ...(info?.ffmpeg.amf || s.render.mode === "fast"
                  ? [{ value: "fast", label: info?.ffmpeg.amf ? "Rápido (GPU / AMF)" : "Rápido (AMF indisponível neste PC)" }]
                  : []),
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
          <Field
            label="Modelo de transcrição (whisper)"
            hint={`Em uso: ${s.transcription.model}. small é rápido; medium é mais preciso e ~2× mais lento.`}
            className="sm:col-span-2"
          >
            <ModelDownloadPanel current={s.transcription.model} onReady={(m) => patch(["transcription", "model"], m)} />
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
