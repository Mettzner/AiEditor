"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Plus, WifiOff } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { ProductionCard } from "@/components/production-card";
import { API_URL, type Production } from "@/lib/api";

export default function ProducoesPage() {
  const [items, setItems] = useState<Production[] | null>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    const es = new EventSource(`${API_URL}/productions/events`);
    es.addEventListener("snapshot", (e) => {
      setItems(JSON.parse((e as MessageEvent).data));
      setOffline(false);
    });
    es.addEventListener("update", (e) => {
      const changed: Production[] = JSON.parse((e as MessageEvent).data);
      setItems((prev) => {
        const map = new Map((prev ?? []).map((p) => [p.id, p]));
        changed.forEach((p) => map.set(p.id, p));
        return [...map.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
      });
    });
    es.onerror = () => setOffline(true);
    es.onopen = () => setOffline(false);
    return () => es.close();
  }, []);

  // remove da lista o que foi excluído (o SSE só envia alterações)
  useEffect(() => {
    const t = setInterval(async () => {
      try {
        const res = await fetch(`${API_URL}/productions`);
        if (res.ok) setItems(await res.json());
      } catch {}
    }, 15000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="mx-auto max-w-7xl">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Produções</h1>
          <p className="text-sm text-muted-foreground">Acompanhe cada vídeo do roteiro ao Drive.</p>
        </div>
        <Link href="/criacao" className={buttonVariants()}>
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
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {items.map((p) => (
            <ProductionCard key={p.id} p={p} />
          ))}
        </div>
      )}
    </div>
  );
}
