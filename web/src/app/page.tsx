import Link from "next/link";

import { Shell } from "@/components/Shell";

export default function HomePage() {
  return (
    <Shell title="Inicio">
      <p className="max-w-prose text-muted">
        Esqueleto de la GUI. Las pantallas se conectan a la API cuando se construya el flujo
        multi-agente (ver docs/ROADMAP.md).
      </p>
      <ul className="grid gap-3 sm:grid-cols-3">
        <li>
          <Link className="block border border-line bg-panel p-4 hover:border-ink" href="/consultar">
            Consultar
          </Link>
        </li>
        <li>
          <Link className="block border border-line bg-panel p-4 hover:border-ink" href="/fuentes">
            Fuentes
          </Link>
        </li>
        <li>
          <Link className="block border border-line bg-panel p-4 hover:border-ink" href="/sistema">
            Sistema
          </Link>
        </li>
      </ul>
    </Shell>
  );
}
