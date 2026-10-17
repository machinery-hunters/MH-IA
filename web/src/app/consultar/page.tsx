"use client";

import { useState } from "react";

import { Shell } from "@/components/Shell";
import { api, ApiError } from "@/lib/api";

const MAX_LENGTH = 2000; // mismo límite que valida la API (la API valida siempre)

export default function ConsultarPage() {
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await api.query(query);
      setMessage("Respuesta recibida.");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setMessage("Necesitas iniciar sesión para consultar.");
      } else if (error instanceof ApiError && error.status === 501) {
        setMessage("La consulta multi-agente aún no está construida.");
      } else {
        setMessage("No se pudo completar la consulta. Intenta de nuevo.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="Consultar">
      <form onSubmit={onSubmit} className="flex max-w-prose flex-col gap-3">
        <label htmlFor="query" className="text-sm font-medium">
          Pregunta
        </label>
        <textarea
          id="query"
          required
          maxLength={MAX_LENGTH}
          rows={4}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="border border-line bg-panel p-3"
        />
        <button
          type="submit"
          disabled={busy || query.trim().length === 0}
          className="self-start bg-ink px-4 py-2 text-sm text-panel disabled:opacity-50"
        >
          {busy ? "Consultando…" : "Consultar"}
        </button>
        <p role="status" aria-live="polite" className="min-h-6 text-sm text-muted">
          {message}
        </p>
      </form>
    </Shell>
  );
}
