"use client";

import { useEffect, useState } from "react";

import { Shell } from "@/components/Shell";
import { api, ApiError, type ConnectorStatus } from "@/lib/api";

type State =
  | { kind: "loading" }
  | { kind: "denied" }
  | { kind: "error" }
  | { kind: "ready"; connectors: ConnectorStatus[] };

export default function FuentesPage() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    api
      .connectors()
      .then((connectors) => !cancelled && setState({ kind: "ready", connectors }))
      .catch((error: unknown) => {
        if (cancelled) return;
        const denied = error instanceof ApiError && (error.status === 401 || error.status === 403);
        setState({ kind: denied ? "denied" : "error" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Shell title="Fuentes">
      {state.kind === "loading" && <p className="text-muted">Cargando…</p>}
      {state.kind === "denied" && (
        <p className="max-w-prose text-muted">
          No tienes permiso para ver la configuración de fuentes, o tu sesión expiró.
        </p>
      )}
      {state.kind === "error" && (
        <p className="max-w-prose text-bad">No se pudo consultar el estado de las fuentes.</p>
      )}
      {state.kind === "ready" && (
        <ul className="flex flex-col divide-y divide-line border border-line bg-panel">
          {state.connectors.map((connector) => (
            <li key={connector.name} className="flex items-center justify-between gap-4 p-4">
              <span>{connector.display_name}</span>
              {/* Estado en texto, no solo en color. */}
              <span className={connector.configured ? "text-ok" : "text-warn"}>
                {connector.configured ? "Configurada" : "Sin configurar"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Shell>
  );
}
