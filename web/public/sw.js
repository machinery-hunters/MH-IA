/*
 * Service worker mínimo: vuelve la GUI instalable (PWA) y NADA MÁS.
 * Deliberadamente SIN caché ni manejo de fetch: las respuestas de la API
 * contienen información de la empresa y no deben quedar en el dispositivo.
 * Si en el futuro se añade modo sin conexión, solo se cachea el shell
 * estático, nunca /api/*.
 */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
