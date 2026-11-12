"use client";

import { useState, type FormEvent } from "react";

import { SessionsPanel } from "@/components/SessionsPanel";
import { Shell } from "@/components/Shell";
import { useSession } from "@/components/SessionProvider";
import { api, ApiError } from "@/lib/api";
import {
  MAX_PASSWORD_LENGTH,
  MIN_PASSWORD_LENGTH,
  passwordChangeErrorMessage,
  passwordChangeProblem,
  roleLabel,
} from "@/lib/auth-helpers";

const INPUT_CLASS = "border border-line bg-panel p-3";

/**
 * Mi cuenta: datos de la sesión, cambio de contraseña y sesiones abiertas. El cambio de
 * contraseña cierra todas las demás sesiones de la persona (otros equipos); esta sigue
 * abierta con una cookie nueva.
 */
export default function CuentaPage() {
  const { session } = useSession();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [sessionsKey, setSessionsKey] = useState(0);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const problem = passwordChangeProblem({ current, next, confirm });
    if (problem) {
      setError(problem);
      return;
    }

    setBusy(true);
    setError(null);
    setDone(false);
    try {
      await api.changePassword({ currentPassword: current, newPassword: next });
      setDone(true);
      setSessionsKey((key) => key + 1);
    } catch (failure) {
      setError(
        passwordChangeErrorMessage(
          failure instanceof ApiError
            ? { status: failure.status, detail: failure.detail, retryAfter: failure.retryAfter }
            : null,
        ),
      );
    } finally {
      // Las contraseñas no se quedan en memoria más de lo necesario.
      setCurrent("");
      setNext("");
      setConfirm("");
      setBusy(false);
    }
  }

  const me = session.status === "authenticated" ? session.me : null;

  return (
    <Shell title="Mi cuenta">
      {me && (
        <p className="text-muted">
          {me.username} · {roleLabel(me.role)}
        </p>
      )}

      <form onSubmit={onSubmit} className="flex max-w-sm flex-col gap-4" noValidate>
        <h2 className="text-lg font-medium">Cambiar contraseña</h2>
        <div className="flex flex-col gap-1">
          <label htmlFor="current-password" className="text-sm font-medium">
            Contraseña actual
          </label>
          <input
            id="current-password"
            type="password"
            autoComplete="current-password"
            required
            maxLength={MAX_PASSWORD_LENGTH}
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
            className={INPUT_CLASS}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="new-password" className="text-sm font-medium">
            Contraseña nueva
          </label>
          <input
            id="new-password"
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD_LENGTH}
            maxLength={MAX_PASSWORD_LENGTH}
            aria-describedby="new-password-hint"
            value={next}
            onChange={(event) => setNext(event.target.value)}
            className={INPUT_CLASS}
          />
          <p id="new-password-hint" className="text-xs text-muted">
            Al menos {MIN_PASSWORD_LENGTH} caracteres. Una frase de varias palabras es más fácil de
            recordar y más segura.
          </p>
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="confirm-password" className="text-sm font-medium">
            Confirma la contraseña nueva
          </label>
          <input
            id="confirm-password"
            type="password"
            autoComplete="new-password"
            required
            maxLength={MAX_PASSWORD_LENGTH}
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            className={INPUT_CLASS}
          />
        </div>

        <p role="alert" className="min-h-6 text-sm text-bad">
          {error}
        </p>
        <p role="status" aria-live="polite" className="text-sm text-ok">
          {done
            ? "Contraseña actualizada. Se cerraron tus sesiones en otros equipos; esta sigue abierta."
            : null}
        </p>

        <button
          type="submit"
          disabled={busy}
          className="self-start bg-ink px-4 py-2 text-sm text-panel disabled:opacity-50"
        >
          {busy ? "Guardando…" : "Cambiar contraseña"}
        </button>
      </form>

      <SessionsPanel refreshKey={sessionsKey} />
    </Shell>
  );
}
