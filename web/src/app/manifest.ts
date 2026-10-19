import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "MH IA — Machinery Hunters",
    short_name: "MH IA",
    description: "Consulta interna con fuentes verificables.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#f3f4f2",
    theme_color: "#1d252b",
    lang: "es",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
