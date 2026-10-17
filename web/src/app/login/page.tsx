import type { Metadata } from "next";

import { Shell } from "@/components/Shell";

export const metadata: Metadata = { title: "Entrar" };

export default function LoginPage() {
  return (
    <Shell title="Entrar">
      <p className="max-w-prose text-muted">
        La autenticación (sesión por cookie, y segundo factor para el rol de control total) se
        construye junto con la API. Hasta entonces, la API rechaza toda petición sin sesión.
      </p>
    </Shell>
  );
}
