export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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
}

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
  issues: ProductionIssue[];
}

export interface Direction {
  id: string;
  name: string;
  description: string;
  image?: string;
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
    youtube_units_needed: number;
    youtube_daily_quota: number;
  };
  time_minutes: number;
}

export interface Voice {
  id: string;
  name: string;
  language?: string | null;
  accent?: string | null;
  preview_url?: string | null;
}

export interface SettingsPayload {
  settings: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  secrets: Record<string, boolean>;
  google_connected: boolean;
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
  deleteChannel: (id: number) => request(`/channels/${id}`, { method: "DELETE" }),
  uploadReference: (id: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<Channel>(`/channels/${id}/reference`, { method: "POST", body: fd });
  },

  productions: () => request<Production[]>("/productions"),
  createProduction: (fd: FormData) => request<Production>("/productions", { method: "POST", body: fd }),
  cancel: (id: number) => request(`/productions/${id}/cancel`, { method: "POST" }),
  retry: (id: number) => request(`/productions/${id}/retry`, { method: "POST" }),
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
  googleStart: () => request<{ url: string }>("/auth/google/start"),
  health: () => request<{ ok: boolean; ffmpeg: boolean }>("/health"),
};

export const DEFAULT_PRESET: Preset = {
  language: "en",
  search_language: "en",
  tts_voice: null,
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
};

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
