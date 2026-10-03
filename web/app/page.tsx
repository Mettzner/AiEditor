"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Plus, WifiOff } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { ProductionCard } from "@/components/production-card";
import { API_URL, type Production } from "@/lib/api";

// Ordem fixa: mais nova primeiro, pelo id (as datas chegam em formatos diferentes do SSE e do REST, e
// comparar como texto fazia o card que recebia progresso trocar de lugar).
const byNewest = (list: Production[]) => [...list].sort((a, b) => b.id - a.id);

export default function ProducoesPage() {
  const [items, setItems] = useState<Production[] | null>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    // O servidor encerra cada conexão SSE a cada ~25 s e o navegador reconecta sozinho.
    // Só mostra "sem conexão" se a reconexão não acontecer em alguns segundos.
    let offlineTimer: ReturnType<typeof setTimeout> | null = null;
    const online = () => {
      if (offlineTimer) clearTimeout(offlineTimer);
      offlineTimer = null;
      setOffline(false);
    };
    const es = new EventSource(`${API_URL}/productions/events`);
    es.addEventListener("snapshot", (e) => {
      setItems(byNewest(JSON.parse((e as MessageEvent).data)));
      online();
    });
    es.addEventListener("update", (e) => {
      const changed: Production[] = JSON.parse((e as MessageEvent).data);
      setItems((prev) => {
        const map = new Map((prev ?? []).map((p) => [p.id, p]));
        changed.forEach((p) => map.set(p.id, p));
        return byNewest([...map.values()]);
      });
    });
    es.onerror = () => {
      offlineTimer ??= setTimeout(() => setOffline(true), 6000);
    };
    es.onopen = online;
    return () => {
      if (offlineTimer) clearTimeout(offlineTimer);
      es.close();
    };
  }, []);

  // remove da lista o que foi excluído (o SSE só envia alterações)
  useEffect(() => {
    const t = setInterval(async () => {
      try {
        const res = await fetch(`${API_URL}/productions`);
        if (res.ok) setItems(byNewest(await res.json()));
      } catch {}
    }, 15000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="w-full">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Produções</h1>
          <p className="text-sm text-muted-foreground">Acompanhe cada vídeo do roteiro ao Drive.</p>
        </div>
        <Link href="/criacao" className={buttonVariants({ size: "lg" })}>
          <Plus /> Incluir
        </Link>
      </div>

      {offline && (
        <div className="mb-4 flex items-center gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
          <WifiOff className="size-4" /> Sem conexão com o backend em {API_URL}. Ele está rodando?
        </div>
      )}

      {items === null ? (
        <p className="text-sm text-muted-foreground">Carregando…</p>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed p-12 text-center text-sm text-muted-foreground">
          Nenhuma produção ainda. Clique em <b>Incluir</b> para criar a primeira.
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {items.map((p) => (
            <ProductionCard key={p.id} p={p} />
          ))}
        </div>
      )}
    </div>
  );
}
