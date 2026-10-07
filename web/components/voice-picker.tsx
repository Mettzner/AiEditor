"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Combobox as ComboboxPrimitive } from "@base-ui/react/combobox";
import { Pause, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import { api, type Voice } from "@/lib/api";

const LANG: Record<string, string> = {
  en: "inglês", pt: "português", es: "espanhol", fr: "francês", de: "alemão", it: "italiano",
  ja: "japonês", ko: "coreano", tr: "turco", ru: "russo", pl: "polonês", sv: "sueco", fi: "finlandês",
  hu: "húngaro", bg: "búlgaro", zh: "chinês", ar: "árabe", cs: "tcheco", el: "grego", hi: "hindi",
  hr: "croata", nl: "holandês", ro: "romeno", sk: "eslovaco", ta: "tâmil", uk: "ucraniano", fil: "filipino",
  id: "indonésio", da: "dinamarquês", no: "norueguês", vi: "vietnamita", ms: "malaio",
};
const AGE: Record<string, string> = { young: "jovem", middle_aged: "adulto", old: "idoso" };

let cache: Promise<Voice[]> | null = null;
function loadVoices() {
  cache ??= api.voices().catch((e) => {
    cache = null;
    throw e;
  });
  return cache;
}

function describe(v: Voice) {
  const langs = v.languages.length ? v.languages : v.language ? [v.language] : [];
  // idioma principal primeiro; listas longas (vozes multilíngues) viram "+N idiomas"
  const ordered = [...new Set([v.language, ...langs].filter(Boolean) as string[])];
  const shown = ordered.length > 4 ? `${ordered.slice(0, 3).map((l) => LANG[l] ?? l).join(", ")} +${ordered.length - 3} idiomas`
    : ordered.map((l) => LANG[l] ?? l).join(", ");
  return [shown, v.accent, v.age && (AGE[v.age] ?? v.age)]
    .filter(Boolean)
    .join(" · ");
}

function speaks(v: Voice, language: string) {
  return v.language === language || v.languages.includes(language);
}

/**
 * Dropdown com busca pelos narradores da Darkvi. Mostra só o nome; o id fica por trás. Com `language`, os
 * narradores que falam o idioma do vídeo vêm primeiro e há um aviso se o escolhido não fala.
 */
export function VoicePicker({
  value,
  onChange,
  language,
}: {
  value: string | null;
  onChange: (voice: { id: string; name: string } | null) => void;
  language?: string | null;
}) {
  const [voices, setVoices] = useState<Voice[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    loadVoices()
      .then(setVoices)
      .catch((e) => setError((e as Error).message));
    return () => audio.current?.pause();
  }, []);

  const items = useMemo(() => {
    const list = voices ?? [];
    const sorted = language ? [...list.filter((v) => speaks(v, language)), ...list.filter((v) => !speaks(v, language))]
      : list;
    return ComboboxPrimitive.createItems(sorted, { getValue: (v) => v.id, getLabel: (v) => v.name });
  }, [voices, language]);
  const selected = voices?.find((v) => v.id === value) ?? null;

  function toggle(v: Voice | null) {
    if (!v?.preview_url) return;
    if (playing === v.id) {
      audio.current?.pause();
      setPlaying(null);
      return;
    }
    audio.current?.pause();
    audio.current = new Audio(v.preview_url);
    audio.current.onended = () => setPlaying(null);
    audio.current.play();
    setPlaying(v.id);
  }

  if (error) return <p className="text-xs text-red-300">Não foi possível carregar os narradores: {error}</p>;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex gap-2">
        <Combobox
          items={items}
          value={value}
          onValueChange={(id) => {
            const v = voices?.find((x) => x.id === id);
            onChange(v ? { id: v.id, name: v.name } : null);
          }}
          disabled={!voices}
        >
          <ComboboxInput
            className="w-full"
            placeholder={voices ? `Buscar entre ${voices.length} narradores…` : "Carregando narradores…"}
          />
          <ComboboxContent>
            <ComboboxEmpty>Nenhum narrador com esse nome.</ComboboxEmpty>
            <ComboboxList>
              {(v: Voice) => (
                <ComboboxItem key={v.id} value={v.id}>
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="flex items-center gap-1.5">
                      {v.name}
                      {v.is_new && (
                        <span className="rounded bg-[#E63946]/20 px-1 text-[10px] text-[#ff8a93]">novo</span>
                      )}
                    </span>
                    <span className="truncate text-xs text-muted-foreground">{describe(v)}</span>
                  </span>
                </ComboboxItem>
              )}
            </ComboboxList>
          </ComboboxContent>
        </Combobox>
        <Button
          variant="outline"
          size="icon"
          aria-label="Ouvir prévia"
          disabled={!selected?.preview_url}
          onClick={() => toggle(selected)}
        >
          {playing && playing === selected?.id ? <Pause /> : <Play />}
        </Button>
      </div>
      {selected && <p className="text-xs text-muted-foreground">{describe(selected)}</p>}
      {selected && language && !speaks(selected, language) && (
        <p className="text-xs text-amber-300">
          Este narrador não lista {LANG[language] ?? language} entre seus idiomas; a pronúncia pode ficar estranha.
        </p>
      )}
    </div>
  );
}
