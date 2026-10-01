import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/sidebar";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "AiEditor",
  description: "Roteiro + narração → vídeo montado e renderizado",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="pt-BR" className={`${geistSans.variable} ${geistMono.variable} dark h-full antialiased`}>
      <body className="flex min-h-full bg-background text-foreground">
        <TooltipProvider>
          <Sidebar />
          <main className="min-w-0 flex-1 px-8 py-7">{children}</main>
          <Toaster theme="dark" richColors position="bottom-right" />
        </TooltipProvider>
      </body>
    </html>
  );
}
