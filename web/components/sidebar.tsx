"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ArrowUpCircle, Clapperboard, Film, Settings, Tv, WandSparkles } from "lucide-react";
import { api, APP_VERSION, openExternal, type AppInfo } from "@/lib/api";
import { cn } from "@/lib/utils";

const ITEMS = [
  { href: "/", label: "Produções", icon: Film },
  { href: "/criacao", label: "Criação", icon: WandSparkles },
  { href: "/canais", label: "Canais", icon: Tv },
  { href: "/configuracao", label: "Configuração", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [info, setInfo] = useState<AppInfo | null>(null);

  useEffect(() => {
    api.appInfo().then(setInfo).catch(() => {});
    // primeira execução: assistente de configuração antes do painel
    if (!pathname.startsWith("/bemvindo")) {
      api
        .setupStatus()
        .then((s) => s.needs_setup && router.replace("/bemvindo/"))
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <aside className="sticky top-0 flex h-screen w-64 shrink-0 flex-col border-r bg-sidebar px-4 py-6">
      <div className="mb-8 flex items-center gap-2 px-2 text-sidebar-foreground">
        <Clapperboard className="size-5 text-[#E63946]" />
        <span className="text-lg font-semibold tracking-tight">AiEditor</span>
      </div>
      <nav className="flex flex-col gap-1">
        {ITEMS.map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2.5 text-[0.95rem] text-sidebar-foreground/75 transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground",
                active && "bg-sidebar-accent font-medium text-sidebar-foreground",
              )}
            >
              <Icon className="size-4" />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="mt-auto flex flex-col gap-3 px-2">
        {info?.update && (
          <button
            type="button"
            onClick={() => info.update?.url && openExternal(info.update.url, info.desktop)}
            className="flex items-start gap-2 rounded-md border border-sky-500/40 bg-sky-500/10 p-3 text-left text-xs text-sky-200 hover:bg-sky-500/15"
            title={info.update.notes}
          >
            <ArrowUpCircle className="mt-0.5 size-4 shrink-0" />
            <span>
              <b>Nova versão disponível: {info.update.version}</b>
              <span className="block text-sky-200/70">Clique para baixar o instalador.</span>
            </span>
          </button>
        )}
        <p className="text-xs text-muted-foreground">v{info?.version ?? APP_VERSION}</p>
      </div>
    </aside>
  );
}
