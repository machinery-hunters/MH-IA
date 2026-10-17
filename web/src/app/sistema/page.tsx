import type { Metadata } from "next";

import { Shell } from "@/components/Shell";

export const metadata: Metadata = { title: "Sistema" };

export default function SistemaPage() {
  return (
    <Shell title="Sistema">
      <p className="max-w-prose text-muted">
        Reservado al rol de control total: credenciales de conectores e infraestructura. Exigirá
        segundo factor. La pantalla se oculta según los permisos que devuelve la API, y la API
        vuelve a validar cada acción.
      </p>
    </Shell>
  );
}
