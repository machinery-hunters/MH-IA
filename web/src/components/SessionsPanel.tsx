"use client";

import { useEffect, useState } from "react";

import { api, ApiError, type SessionSummary } from "@/lib/api";
import { formatAgo, sessionsErrorMessage, sortSessions } from "@/lib/auth-helpers";

function failureOf(error: unknown) {
  return error instanceof ApiError
    ? { status: error.status, detail: error.detail, retryAfter: error.retryAfter }
    : null;
}

/**
 * Mis sesiones abiertas: en qué equipos está iniciada la cuenta y desde cuándo, para
 * cerrar las que la persona no reconozca. La API solo devuelve las propias y valida que
 * el id que se cierra sea de quien lo pide. `refreshKey` vuelve a cargar la lista (p. ej.
 * tras cambiar la contraseña, que cierra las demás).
 */
export function SessionsPanel({ refreshKey = 0 }: { refreshKey?: number }) {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now() / 1000);

  const [reloads, setReloads] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api
      .sessions()
      .then((list) => {
        if (cancelled) return;
        setSessions(sortSessions(list));
        setNow(Date.now() / 1000);
        setError(null);
      })
      .catch((failure: unknown) => {
        if (!cancelled) setError(sessionsErrorMessage(failureOf(failure)));
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey, reloads]);

  async function run(action: () => Promise<string>) {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      setNotice(await action());
    } catch (failure) {
      setError(sessionsErrorMessage(failureOf(failure)));
    } finally {
      setReloads((count) => count + 1);
      setBusy(false);
    }
  }

  const closeOne = (session: SessionSummary) =>
    run(async () => {
      await api.closeSession(session.id);
      return `Sesión cerrada en ${session.device}.`;
    });

  const closeOthers = () =>
    run(async () => {
      const { closed } = await api.closeOtherSessions();
      return closed === 1 ? "Se cerró 1 sesión." : `Se cerraron ${closed} sesiones.`;
    });

  const others = sessions?.filter((session) => !session.current).length ?? 0;

  return (
    <section aria-labelledby="sessions-title" className="flex max-w-xl flex-col gap-3">
      <h2 id="sessions-title" className="text-lg font-medium">
        Sesiones abiertas
      </h2>
      <p className="text-sm text-muted">
        Equipos donde tu cuenta está iniciada. Si ves uno que no reconoces, ciérralo y cambia tu
        contraseña.
      </p>

      {sessions === null && !error && <p className="text-sm text-muted">Cargando…</p>}

      {sessions && (
        <ul className="flex flex-col divide-y divide-line border border-line bg-panel">
          {sessions.map((session) => (
            <li key={session.id} className="flex items-center justify-between gap-4 p-3">
              <div className="flex flex-col">
                <span className="font-medium">
                  {session.device}
                  {session.current && (
                    <span className="ml-2 text-xs font-normal text-ok">· este equipo</span>
                  )}
                </span>
                <span className="text-xs text-muted">
                  Último uso {formatAgo(session.last_seen, now)} · iniciada{" "}
                  {formatAgo(session.created_at, now)}
                  {session.mfa ? " · con segundo factor" : ""}
                </span>
              </div>
              {!session.current && (
                <button
                  type="button"
                  onClick={() => closeOne(session)}
                  disabled={busy}
                  className="shrink-0 text-sm underline underline-offset-4 disabled:opacity-50"
                  aria-label={`Cerrar la sesión en ${session.device}`}
                >
                  Cerrar
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      <p role="alert" className="min-h-6 text-sm text-bad">
        {error}
      </p>
      <p role="status" aria-live="polite" className="text-sm text-ok">
        {notice}
      </p>

      {others > 0 && (
        <button
          type="button"
          onClick={closeOthers}
          disabled={busy}
          className="self-start border border-line px-4 py-2 text-sm disabled:opacity-50"
        >
          {busy ? "Cerrando…" : "Cerrar las demás sesiones"}
        </button>
      )}
    </section>
  );
}
