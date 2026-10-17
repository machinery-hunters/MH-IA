import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { RegisterServiceWorker } from "@/components/RegisterServiceWorker";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "MH IA", template: "%s · MH IA" },
  description: "Consulta interna de Machinery Hunters con fuentes verificables.",
  icons: { icon: "/icons/icon-192.png", apple: "/icons/icon-192.png" },
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: "#1d252b",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="es">
      <body>
        {children}
        <RegisterServiceWorker />
      </body>
    </html>
  );
}
