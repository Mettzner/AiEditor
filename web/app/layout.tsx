import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/sidebar";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const mono = JetBrains_Mono({
  variable: "--font-mono-jb",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "AiEditor",
  description: "Roteiro + narração → vídeo montado e renderizado",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="pt-BR" className={`${inter.variable} ${mono.variable} dark h-full antialiased`}>
      <body className="flex min-h-full bg-background text-foreground">
        <TooltipProvider>
          <Sidebar />
          <main className="min-w-0 flex-1 px-10 py-8">{children}</main>
          <Toaster theme="dark" richColors position="bottom-right" />
        </TooltipProvider>
      </body>
    </html>
  );
}
