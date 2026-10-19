"use client";

import { useEffect } from "react";

/**
 * Registra el service worker mínimo que vuelve la GUI instalable (PWA).
 * Requiere contexto seguro: localhost o HTTPS (`tailscale serve`).
 */
export function RegisterServiceWorker() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Sin service worker la app sigue funcionando; solo no se puede instalar.
    });
  }, []);

  return null;
}
