"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { Shell } from "@/components/Shell";
import { api, ApiError, type QueryResponse } from "@/lib/api";
import {
  MAX_QUERY_LENGTH,
  canSubmitQuery,
  queryErrorMessage,
  reasonerNote,
  statusLabel,
} from "@/lib/query-helpers";

export default function ConsultarPage() {
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);

  // Si la persona sale de la pantalla se descarta la respuesta pendiente.
  useEffect(() => () => controller.current?.abort(), []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !canSubmitQuery(query)) return;

    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;

    setBusy(true);
    setError(null);
    setResult(null); // nunca se deja a la vista la respuesta de la pregunta anterior
    try {
      const response = await api.query(query.trim(), current.signal);
      if (!current.signal.aborted) setResult(response);
    } catch (failure) {
      if (current.signal.aborted) return;
      setError(
        queryErrorMessage(
          failure instanceof ApiError
            ? { status: failure.status, detail: failure.detail, retryAfter: failure.retryAfter }
            : null,
        ),
      );
    } finally {
      if (!current.signal.aborted) setBusy(false);
    }
  }

  return (
    <Shell title="Consultar">
      <form onSubmit={onSubmit} className="flex max-w-prose flex-col gap-3" aria-busy={busy}>
        <label htmlFor="query" className="text-sm font-medium">
          Pregunta
        </label>
        <textarea
          id="query"
          required
          maxLength={MAX_QUERY_LENGTH}
          rows={4}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="border border-line bg-panel p-3"
        />
        <div className="flex items-center gap-4">
          <button
            type="submit"
            disabled={busy || !canSubmitQuery(query)}
            className="bg-ink px-4 py-2 text-sm text-panel disabled:opacity-50"
          >
            {busy ? "Consultando…" : "Consultar"}
          </button>
          <span className="text-xs text-muted">
            {query.length}/{MAX_QUERY_LENGTH}
          </span>
        </div>
        <p role="status" aria-live="polite" className="min-h-6 text-sm text-muted">
          {busy ? "Consultando las fuentes. Puede tardar hasta un par de minutos." : null}
        </p>
      </form>

      {error && (
        <p role="alert" className="max-w-prose text-sm text-bad">
          {error}
        </p>
      )}

      {result && (
        <section aria-labelledby="result-title" className="flex max-w-prose flex-col gap-3">
          <h2 id="result-title" className="text-sm font-medium text-muted">
            {statusLabel(result.status)}
          </h2>
          {/* Texto plano: la respuesta nunca se interpreta como HTML. */}
          <div className="whitespace-pre-wrap border border-line bg-panel p-4">
            {result.answer}
          </div>
          {result.status === "answered" && reasonerNote(result.reasoner) && (
            <p className="text-xs text-muted">{reasonerNote(result.reasoner)}</p>
          )}
          {result.status === "answered" && result.sources.length > 0 && (
            <div className="flex flex-col gap-1">
              <h3 className="text-sm font-medium">Fuentes consultadas</h3>
              <ul className="list-disc pl-5 text-sm text-muted">
                {result.sources.map((source) => (
                  <li key={source}>{source}</li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
    </Shell>
  );
}
