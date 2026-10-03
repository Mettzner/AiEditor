"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, CheckCircle2, ExternalLink, Link2, PlugZap, XCircle } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Field, SimpleSelect } from "@/components/fields";
import { LANGS } from "@/components/channel-form";
import { ModelDownloadPanel } from "@/components/model-download";
import { api, DEFAULT_PRESET, openExternal, type SetupStatus } from "@/lib/api";

const STEPS = ["Boas-vindas", "Chaves de API", "Google Drive", "Transcrição", "Primeiro canal"];

const KEYS: { id: string; label: string; url: string; note: string }[] = [
  { id: "darkvi", label: "Darkvi", url: "https://darkvi.com", note: "narração e imagens de IA (ou envie o áudio pronto)" },
  { id: "pexels", label: "Pexels", url: "https://www.pexels.com/api/", note: "banco de vídeos grátis (um banco é obrigatório)" },
  { id: "pixabay", label: "Pixabay", url: "https://pixabay.com/api/docs/", note: "banco de vídeos grátis" },
  { id: "anthropic", label: "Claude (Anthropic)", url: "https://console.anthropic.com/settings/keys", note: "planejamento das cenas (recomendado)" },
  { id: "gemini", label: "Google Gemini", url: "https://aistudio.google.com/apikey", note: "confere se cada clipe mostra o assunto certo" },
  { id: "youtube", label: "YouTube Data API", url: "https://console.cloud.google.com/apis/library/youtube.googleapis.com", note: "clipes Creative Commons (opcional)" },
];

export default function BemVindoPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [st, setSt] = useState<SetupStatus | null>(null);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [tests, setTests] = useState<Record<string, { ok: boolean; detail: unknown }>>({});
  const [channel, setChannel] = useState({ name: "", language: "pt", visual_style: "" });
  const [busy, setBusy] = useState(false);

  const refresh = () => api.setupStatus().then(setSt).catch((e) => toast.error((e as Error).message));
  useEffect(() => {
    refresh();
  }, []);
  // enquanto o usuário faz login no navegador, acompanha a conexão do Google
  useEffect(() => {
    if (step !== 2 || st?.google.connected) return;
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [step, st?.google.connected]);

  async function saveAndTest(id: string) {
    try {
      if (keys[id]) await api.saveSettings({ secrets: { [id]: keys[id] } });
      const r = await api.test(id);
      setTests((t) => ({ ...t, [id]: r }));
      refresh();
    } catch (e) {
      setTests((t) => ({ ...t, [id]: { ok: false, detail: (e as Error).message } }));
    }
  }

  async function saveKeys() {
    const filled = Object.fromEntries(Object.entries(keys).filter(([, v]) => v.trim()));
    if (Object.keys(filled).length) await api.saveSettings({ secrets: filled });
    await refresh();
  }

  async function connectGoogle() {
    try {
      const { url, opened } = await api.googleStart();
      if (!opened) window.open(url, "_blank");
      toast.info("Faça o login no navegador e volte para cá.");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function finish() {
    setBusy(true);
    try {
      if (channel.name.trim()) {
        await api.createChannel(channel.name.trim(), {
          ...DEFAULT_PRESET,
          language: channel.language,
          visual_style: channel.visual_style,
        });
      }
      await api.setupDone();
      toast.success("Tudo pronto!");
      router.replace("/");
    } catch (e) {
      toast.error((e as Error).message);
      setBusy(false);
    }
  }

  async function next() {
    if (step === 1) await saveKeys();
    setStep((s) => s + 1);
  }

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="mb-1 text-2xl font-semibold tracking-tight">Bem-vindo ao AiEditor</h1>
      <p className="mb-6 text-sm text-muted-foreground">Configuração inicial: leva uns 5 minutos.</p>
      <div className="mb-6 flex items-center gap-2 text-xs">
        {STEPS.map((s, i) => (
          <span key={s} className={i === step ? "font-medium text-foreground" : "text-muted-foreground"}>
            {i + 1}. {s}
            {i < STEPS.length - 1 && <span className="mx-2 text-muted-foreground">›</span>}
          </span>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{STEPS[step]}</CardTitle>
          <CardDescription>
            {step === 0 && "Roteiro + narração viram um vídeo montado, com legendas e música, salvo no seu Google Drive."}
            {step === 1 &&
              "Cada pessoa usa as próprias chaves. Elas ficam guardadas no Gerenciador de Credenciais do Windows, nunca em arquivo."}
            {step === 2 && "Os vídeos prontos vão para uma pasta do seu Google Drive. Dá para pular e conectar depois."}
            {step === 3 && "O modelo que transcreve a narração é baixado uma vez para este computador."}
            {step === 4 && "Um canal guarda o idioma e o estilo padrão dos seus vídeos."}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          {step === 0 && (
            <ul className="list-disc space-y-1.5 pl-5 text-sm text-muted-foreground">
              <li>Você vai informar suas chaves de API (Darkvi e um banco de vídeo são o mínimo para começar).</li>
              <li>Conectar sua conta Google (opcional; sem ela, o vídeo fica salvo neste computador).</li>
              <li>Baixar o modelo de transcrição e criar o primeiro canal.</li>
            </ul>
          )}

          {step === 1 &&
            KEYS.map((k) => (
              <Field
                key={k.id}
                label={`${k.label}${st?.keys[k.id] ? " (já configurada)" : ""}`}
                hint={k.note}
              >
                <div className="flex gap-2">
                  <Input
                    type="password"
                    placeholder={st?.keys[k.id] ? "•••••••• (deixe vazio para manter)" : "cole a chave aqui"}
                    value={keys[k.id] ?? ""}
                    onChange={(e) => setKeys((s) => ({ ...s, [k.id]: e.target.value }))}
                  />
                  <Button variant="outline" onClick={() => saveAndTest(k.id)}>
                    <PlugZap /> Testar
                  </Button>
                  <Button variant="ghost" size="icon" title="Onde gerar a chave" onClick={() => openExternal(k.url, !!st?.desktop)}>
                    <ExternalLink />
                  </Button>
                </div>
                {tests[k.id] && (
                  <p className={`flex items-start gap-1 text-xs ${tests[k.id].ok ? "text-emerald-300" : "text-red-300"}`}>
                    {tests[k.id].ok ? <CheckCircle2 className="size-3.5" /> : <XCircle className="size-3.5" />}
                    <span className="break-all">
                      {typeof tests[k.id].detail === "string" ? (tests[k.id].detail as string) : JSON.stringify(tests[k.id].detail)}
                    </span>
                  </p>
                )}
              </Field>
            ))}

          {step === 2 && (
            <div className="flex flex-col gap-3">
              {st?.google.connected ? (
                <p className="flex items-center gap-2 text-sm text-emerald-300">
                  <CheckCircle2 className="size-4" /> Conta Google conectada.
                </p>
              ) : st?.google.client ? (
                <div>
                  <Button onClick={connectGoogle}>
                    <Link2 /> Conectar Google
                  </Button>
                  <p className="mt-2 text-xs text-muted-foreground">
                    No primeiro login o Google avisa que o app não é verificado: clique em “Avançado” e continue.
                  </p>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">
                  Esta instalação não traz o client OAuth do Google. Configure depois em Configuração → Google Drive.
                </p>
              )}
            </div>
          )}

          {step === 3 && st && (
            <>
              {st.model.ready && (
                <p className="flex items-center gap-2 text-sm text-emerald-300">
                  <CheckCircle2 className="size-4" /> Modelo {st.model.size} pronto.
                </p>
              )}
              <ModelDownloadPanel current={st.model.size} onReady={() => refresh()} />
            </>
          )}

          {step === 4 && (
            <>
              <Field label="Nome do canal">
                <Input value={channel.name} onChange={(e) => setChannel((c) => ({ ...c, name: e.target.value }))} />
              </Field>
              <Field label="Idioma dos vídeos">
                <SimpleSelect
                  value={channel.language}
                  onChange={(v) => setChannel((c) => ({ ...c, language: v }))}
                  options={LANGS}
                  className="sm:w-64"
                />
              </Field>
              <Field label="Estilo visual (opcional)">
                <Textarea
                  rows={3}
                  placeholder="noturno, granulado, tons frios"
                  value={channel.visual_style}
                  onChange={(e) => setChannel((c) => ({ ...c, visual_style: e.target.value }))}
                />
              </Field>
            </>
          )}
        </CardContent>
      </Card>

      <div className="mt-6 flex justify-between">
        <Button variant="outline" size="lg" onClick={() => setStep((s) => s - 1)} disabled={step === 0}>
          <ArrowLeft /> Voltar
        </Button>
        {step < STEPS.length - 1 ? (
          <div className="flex gap-2">
            {step === 1 && st && !st.minimum_ok && (
              <span className="self-center text-xs text-amber-300">Informe ao menos Pexels ou Pixabay.</span>
            )}
            <Button size="lg" onClick={next} disabled={step === 3 && !!st && !st.model.ready}>
              {step === 3 && st && !st.model.ready ? "Baixe o modelo para continuar" : "Avançar"} <ArrowRight />
            </Button>
          </div>
        ) : (
          <Button size="lg" onClick={finish} disabled={busy || !channel.name.trim()}>
            Concluir <CheckCircle2 />
          </Button>
        )}
      </div>
    </div>
  );
}
