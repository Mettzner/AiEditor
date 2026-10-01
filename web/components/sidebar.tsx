"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Clapperboard, Film, Settings, Tv, WandSparkles } from "lucide-react";
import { cn } from "@/lib/utils";

const ITEMS = [
  { href: "/", label: "Produções", icon: Film },
  { href: "/criacao", label: "Criação", icon: WandSparkles },
  { href: "/canais", label: "Canais", icon: Tv },
  { href: "/configuracao", label: "Configuração", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="sticky top-0 flex h-screen w-56 shrink-0 flex-col border-r bg-sidebar px-3 py-5">
      <div className="mb-8 flex items-center gap-2 px-2 text-sidebar-foreground">
        <Clapperboard className="size-5 text-[#E63946]" />
        <span className="text-base font-semibold tracking-tight">AiEditor</span>
      </div>
      <nav className="flex flex-col gap-1">
        {ITEMS.map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm text-sidebar-foreground/75 transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground",
                active && "bg-sidebar-accent font-medium text-sidebar-foreground",
              )}
            >
              <Icon className="size-4" />
              {label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
