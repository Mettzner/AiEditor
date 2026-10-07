// Desenvolvimento: `next dev` (porta 3000) chama a API no uvicorn (porta 8000).
// App instalado: o frontend exportado é servido pelo próprio FastAPI, na mesma origem → URL relativa.
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8000/api" : "/api");
export const APP_VERSION = process.env.NEXT_PUBLIC_APP_VERSION ?? "dev";

export type AiMedia = "both" | "video" | "image";

export interface SubtitleStyle {
  font: string;
  size: number;
  color: string;
  outline_color: string;
  outline: number;
  position: "bottom" | "middle" | "top";
  margin_v: number;
}

export interface MusicConfig {
  enabled: boolean;
  mood: string;
  source: "library" | "generate" | "library_then_generate";
  volume_db: number;
}

export interface Preset {
  language: string;
  search_language: string;
  tts_voice: string | null;
  tts_voice_name: string | null;
  reference_image: string | null;
  reference_key: string | null;
  direction: string;
  real_pct: number;
  ai_media: AiMedia;
  youtube_pct: number;
  avg_scene_seconds: number;
  subtitles: boolean;
  subtitle_style: SubtitleStyle;
  music: MusicConfig;
  font: string;
  color_primary: string;
  color_accent: string;
  drive_folder: string;
  drive_subfolder_per_production: boolean;
  visual_style: string;
  media_style: MediaStyle;
  selection_mode: SelectionMode;
  llm_economy?: boolean;
  period_look?: PeriodLook;
  period_grade?: boolean;
  context_cards?: boolean;
  sfx?: boolean;
  film_look?: boolean;
  default_video_language?: string | null;
}

export type MediaStyle = "real_only" | "real_preferred" | "free";
export type SelectionMode = "fast" | "precise";
export type PeriodLook = "cinematic" | "archival";

export const PERIOD_LOOKS: { value: PeriodLook; label: string; hint: string }[] = [
  {
    value: "cinematic",
    label: "Reconstituição cinematográfica",
    hint: "imagens de época geradas como cena de filme histórico, com figurino e cenário",
  },
  {
    value: "archival",
    label: "Aparência de arquivo",
    hint: "daguerreótipo sépia (século XIX) ou foto preto e branco (até ~1940)",
  },
];

export const MEDIA_STYLES: { value: MediaStyle; label: string; hint: string }[] = [
  { value: "real_only", label: "Só real", hint: "tudo que não for filmagem/foto real é rejeitado" },
  { value: "real_preferred", label: "Real preferido", hint: "estilizado só quando a cena pede (célula, história, lendas…)" },
  { value: "free", label: "Livre", hint: "qualquer estilo concorre; vence o que melhor representa a cena" },
];

export const SELECTION_MODES: { value: SelectionMode; label: string; hint: string }[] = [
  { value: "fast", label: "Rápido", hint: "6 cenas em paralelo, 1 avaliação por cena, sem desempate" },
  { value: "precise", label: "Preciso", hint: "mais candidatos e frames, com desempate (mais lento)" },
];

export interface Channel {
  id: number;
  name: string;
  preset: Preset;
  created_at: string;
}

export type Severity = "info" | "warning" | "error";

export interface ProductionIssue {
  id: number;
  code: string;
  severity: Severity;
  scene: string | null;
  message: string;
  detail: string | null;
  created_at: string;
}

export type ProductionStatus = "queued" | "running" | "done" | "failed" | "cancel_requested" | "cancelled";

export interface Production {
  id: number;
  title: string;
  channel_id: number | null;
  channel_name: string | null;
  status: ProductionStatus;
  step: string | null;
  step_label: string | null;
  progress: number;
  drive_url: string | null;
  output_path: string | null;
  duration_seconds: number | null;
  cost_estimated: number | null;
  cost_actual: number;
  error: string | null;
  created_at: string;
  updated_at: string;
  ai_content: boolean;
  issue_counts: Partial<Record<Severity, number>>;
  step_seconds?: Record<string, number>;
  llm_usage?: { cost: number; tokens: number; top_task: string | null; cache_read: number; calls: number } | null;
  issues: ProductionIssue[];
}

export interface Direction {
  id: string;
  name: string;
  description: string;
  image?: string;
}

export interface DirectionScene {
  id: string;
  tempo: string;
  duracao: number;
  texto: string;
  plano: {
    subject?: string | null;
    literal?: boolean | null;
    must_show?: string[] | null;
    must_avoid?: string[] | null;
    visual_intent: string | null;
    kind: string | null;
    energy: string | null;
    affinity: { stock: number; youtube: number; ai: number } | null;
    queries: string[] | null;
    ai_kind: string | null;
    chapter_break: boolean | null;
    chapter_title: string | null;
    highlight: string | null;
  };
  fonte_alocada: string;
  fonte_final: string;
  migrou: boolean;
  asset: {
    provider?: string;
    title?: string;
    query?: string;
    score?: number;
    in?: number;
    clip_duration?: number;
    author?: string;
    page_url?: string;
    prompt?: string;
    reason?: string;
    method?: string;
    seen?: string;
    vision_calls?: number;
    searches?: number;
    in_point?: number;
    license?: string;
  };
  render:
    | { transition_in: { type: string; duration?: number }; motion: { type: string } | null; in: number }
    | string;
  problemas: string[];
}

export interface VisualRow {
  cena: string;
  narracao: string;
  assunto_esperado: string | null;
  visto: string;
  fonte: string | null;
  provedor: string | null;
  metodo: string | null;
  nota: number | null;
  estilo?: string | null;
  estilos_aceitos?: string[] | null;
  style_reason?: string | null;
  revisar: boolean;
}

export interface DirectionReport {
  producao: { id: number; title: string; channel_name: string; created_at: string; drive_url: string | null };
  config: Record<string, string | number | boolean>;
  resumo: {
    duracao_total: number;
    cenas: number;
    duracao_cena: { media: number; min: number; max: number };
    composicao_meta_pct: Record<string, number>;
    composicao_final_pct: Record<string, number>;
    chamadas_visao?: number;
    buscas?: number;
    crossfades: number;
    overlays: number;
    clima_musical: string | null;
    musica: unknown;
    transcricao: { fonte: string | null; divergencia: number | null; idioma_detectado: string | null };
  };
  cenas: DirectionScene[];
  visual?: VisualRow[];
  brief?: Record<string, unknown> | null;
  overlays: unknown[];
  problemas: { code: string; severity: Severity; scene: string | null; message: string }[];
}

export interface Estimate {
  duration_seconds: number;
  words: number;
  scenes: number;
  seconds: { ai: number; youtube: number; stock: number };
  ai_media: AiMedia;
  cost: { tts: number; llm: number; ai: number; music: number; total: number };
  quotas: {
    darkvi_images_needed: number;
    darkvi_remaining: number | null;
    darkvi_limit: number | null;
    darkvi_updated_at: string | null;
    darkvi_enough: boolean;
    youtube_scenes: number;
    youtube_scenes_fit: number;
    youtube_units_needed: number;
    youtube_available: number;
    youtube_daily_quota: number;
  };
  time_minutes: number;
}

export interface Voice {
  id: string;
  name: string;
  language: string | null;
  languages: string[];
  accent: string | null;
  age: string | null;
  is_new: boolean;
  preview_url: string | null;
}

export interface SettingsPayload {
  settings: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  secrets: Record<string, boolean>;
  google_connected: boolean;
  google_client_embedded?: boolean;
}

export interface AppInfo {
  version: string;
  desktop: boolean;
  frozen: boolean;
  data_dir: string;
  update: { version: string; url: string | null; notes: string } | null;
  ffmpeg: { available: boolean; version: string | null; amf: boolean };
}

export interface ModelDownload {
  size: string | null;
  status: "idle" | "downloading" | "done" | "error";
  bytes: number;
  total: number;
  progress: number;
  error: string | null;
  downloaded: Record<string, boolean>;
}

export interface SetupStatus {
  needs_setup: boolean;
  keys: Record<string, boolean>;
  minimum_ok: boolean;
  google: { connected: boolean; client: boolean };
  model: { size: string; ready: boolean; download: ModelDownload; options: Record<string, string>; recommended: string };
  channels: number;
  desktop: boolean;
}

export interface YoutubeQuotaBucket {
  bucket: string;
  unit: "calls" | "units";
  used: number;
  attempts: number;
  uncertain: number;
  exhausted: boolean;
  minute_blocked: boolean;
  daily_limit: number;
  reserve: number;
  available: number;
  origin: string | null;
}

export interface YoutubeQuota {
  day: string;
  mode: "separate_buckets" | "legacy_units";
  buckets: Record<string, YoutubeQuotaBucket>;
  unit: "calls" | "units";
  used: number;
  exhausted: boolean;
  daily_quota: number;
  reserve: number;
  available: number;
  searches_left: number;
}

export interface Price {
  id: number;
  provider: string;
  unit: string;
  price: number;
  note: string | null;
}

export interface Track {
  file: string;
  mood: string[];
  license: string;
  source: string;
  uses: Record<string, number>;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, init);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {}
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  channels: () => request<Channel[]>("/channels"),
  createChannel: (name: string, preset: Preset) => request<Channel>("/channels", json("POST", { name, preset })),
  updateChannel: (id: number, name: string, preset: Preset) =>
    request<Channel>(`/channels/${id}`, json("PUT", { name, preset })),
  deleteChannel: (id: number) =>
    request<{ ok: boolean; productions_removed: number }>(`/channels/${id}`, { method: "DELETE" }),
  uploadReference: (id: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<Channel>(`/channels/${id}/reference`, { method: "POST", body: fd });
  },

  productions: () => request<Production[]>("/productions"),
  createProduction: (fd: FormData) => request<Production>("/productions", { method: "POST", body: fd }),
  cancel: (id: number) => request(`/productions/${id}/cancel`, { method: "POST" }),
  retry: (id: number) => request(`/productions/${id}/retry`, { method: "POST" }),
  direction: (id: number) => request<DirectionReport>(`/productions/${id}/direction`),
  deleteProduction: (id: number) => request(`/productions/${id}`, { method: "DELETE" }),
  estimate: (body: {
    channel_id: number;
    script: string;
    config: Record<string, unknown>;
    audio_seconds?: number | null;
    audio_mode?: string;
  }) => request<Estimate>("/estimate", json("POST", body)),

  directions: () => request<Direction[]>("/directions"),
  directionImage: (id: string) => `${API_URL}/directions/${id}/image`,
  voices: () => request<Voice[]>("/tts/voices"),
  languages: () => request<{ value: string; label: string }[]>("/languages"),
  detectLanguage: (script: string) =>
    request<{ language: string | null }>("/detect-language", json("POST", { script })),

  settings: () => request<SettingsPayload>("/settings"),
  saveSettings: (body: { settings?: Record<string, unknown>; secrets?: Record<string, string> }) =>
    request<SettingsPayload>("/settings", json("PUT", body)),
  test: (provider: string) =>
    request<{ ok: boolean; detail: unknown }>(`/settings/test/${provider}`, { method: "POST" }),
  prices: () => request<Price[]>("/prices"),
  savePrices: (items: { id: number; price: number }[]) => request<Price[]>("/prices", json("PUT", items)),
  tracks: () => request<Track[]>("/music/tracks"),
  saveTracks: (items: Pick<Track, "file" | "mood" | "license" | "source">[]) =>
    request<Track[]>("/music/tracks", json("PUT", items)),
  googleStart: () => request<{ url: string; opened: boolean }>("/auth/google/start"),
  appInfo: () => request<AppInfo>("/app/info"),
  openUrl: (url: string) => request<{ ok: boolean }>("/app/open-url", json("POST", { url })),
  setupStatus: () => request<SetupStatus>("/setup/status"),
  setupModel: (size: string) => request<ModelDownload>("/setup/model", json("POST", { size })),
  setupModelStatus: () => request<ModelDownload>("/setup/model"),
  setupDone: () => request<{ ok: boolean }>("/setup/done", json("POST", {})),
  youtubeQuota: () => request<YoutubeQuota>("/youtube/quota"),
  health: () => request<{ ok: boolean; ffmpeg: boolean }>("/health"),
};

export const DEFAULT_PRESET: Preset = {
  language: "en",
  search_language: "en",
  tts_voice: null,
  tts_voice_name: null,
  reference_image: null,
  reference_key: null,
  direction: "classico",
  real_pct: 70,
  ai_media: "both",
  youtube_pct: 40,
  avg_scene_seconds: 6,
  subtitles: true,
  subtitle_style: {
    font: "Montserrat Bold",
    size: 54,
    color: "#FFFFFF",
    outline_color: "#000000",
    outline: 3,
    position: "bottom",
    margin_v: 70,
  },
  music: { enabled: true, mood: "tenso, sombrio, lento", source: "library", volume_db: -22 },
  font: "Montserrat Bold",
  color_primary: "#FFFFFF",
  color_accent: "#E63946",
  drive_folder: "/Canais",
  drive_subfolder_per_production: true,
  visual_style: "",
  media_style: "real_preferred",
  selection_mode: "fast",
  period_look: "cinematic",
  period_grade: true,
  context_cards: true,
  sfx: true,
  film_look: true,
};

/** Abre um link fora do app: no desktop pelo navegador padrão (via backend); no navegador, em nova aba. */
export async function openExternal(url: string, desktop: boolean) {
  if (desktop) {
    try {
      const r = await api.openUrl(url);
      if (r.ok) return;
    } catch {}
  }
  window.open(url, "_blank");
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return `${h ? `${h}:` : ""}${mm}:${String(sec).padStart(2, "0")}`;
}

export function fmtMoney(v: number | null | undefined): string {
  return v == null ? "—" : `US$ ${v.toFixed(2)}`;
}
