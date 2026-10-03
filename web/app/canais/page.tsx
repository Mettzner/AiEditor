"use client";

import { useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ChannelForm } from "@/components/channel-form";
import { api, type Channel } from "@/lib/api";
import { cn } from "@/lib/utils";

export default function CanaisPage() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [selected, setSelected] = useState<number | "new" | null>(null);

  function load(select?: number | "new" | null) {
    return api
      .channels()
      .then((cs) => {
        setChannels(cs);
        setSelected(select !== undefined ? select : (cs[0]?.id ?? "new"));
      })
      .catch((e) => toast.error(`Backend indisponível: ${(e as Error).message}`));
  }

  useEffect(() => {
    api
      .channels()
      .then((cs) => {
        setChannels(cs);
        setSelected(cs[0]?.id ?? "new");
      })
      .catch((e) => toast.error(`Backend indisponível: ${(e as Error).message}`));
  }, []);

  const current = typeof selected === "number" ? channels.find((c) => c.id === selected) ?? null : null;

  return (
    <div className="mx-auto max-w-7xl">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Canais</h1>
          <p className="text-sm text-muted-foreground">Narrador, acabamento e destino de cada canal.</p>
        </div>
        <Button onClick={() => setSelected("new")}>
          <Plus /> Novo canal
        </Button>
      </div>
      <div className="grid gap-6 md:grid-cols-[280px_1fr]">
        <div className="flex flex-col gap-1">
          {channels.map((c) => (
            <button
              key={c.id}
              onClick={() => setSelected(c.id)}
              className={cn(
                "rounded-md border border-transparent px-4 py-3 text-left text-[0.95rem] hover:bg-muted",
                selected === c.id && "bg-muted font-medium",
              )}
            >
              {c.name}
              <span className="block text-xs text-muted-foreground">
                {c.preset.tts_voice_name ?? (c.preset.tts_voice ? "narrador definido" : "sem narrador")}
              </span>
            </button>
          ))}
          {channels.length === 0 && <p className="px-3 text-sm text-muted-foreground">Nenhum canal.</p>}
        </div>
        {selected !== null && (
          <Card>
            <CardContent>
              <ChannelForm
                key={String(selected)}
                channel={current}
                onSaved={(c) => load(c.id)}
                onDeleted={() => load()}
              />
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
