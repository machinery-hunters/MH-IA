import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV !== "production";

// OJO: los rewrites se resuelven en BUILD, no en runtime. En Docker este valor
// entra como build arg (ver web/Dockerfile y docker-compose.yml).
const apiInternalUrl = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

// CSP: Next inyecta scripts inline de arranque, por eso 'unsafe-inline' en
// script-src. Pendiente (SECURITY.md, "aceptado como riesgo"): CSP con nonce.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'self'",
  "worker-src 'self'",
  "manifest-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,

  // La GUI habla con la API por el mismo origen: sin CORS y las cookies de
  // sesión nunca viajan a otro dominio.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiInternalUrl}/api/:path*` }];
  },

  async headers() {
    return [
      { source: "/(.*)", headers: securityHeaders },
      // El service worker nunca debe quedar cacheado por el navegador.
      {
        source: "/sw.js",
        headers: [{ key: "Cache-Control", value: "no-cache, no-store, must-revalidate" }],
      },
    ];
  },
};

export default nextConfig;
