"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { api, ApiError, SESSION_EXPIRED_EVENT, type Me } from "@/lib/api";

/**
 * Estado de la sesión en la GUI. Es solo UX: la sesión real es la cookie
 * HttpOnly y la API valida cada petición. Si el estado local miente, lo peor
 * que pasa es que la API responda 401 y la GUI vuelva a /login.
 */
export type SessionState =
  | { status: "loading" }
  | { status: "anonymous"; reason?: "signed-out" }
  | { status: "unavailable" }
  | { status: "authenticated"; me: Me };

interface SessionContextValue {
  session: SessionState;
  /** Lo llama el login al terminar bien. */
  signIn: (me: Me) => void;
  /** Cierra la sesión en el servidor y localmente (aunque el servidor falle). */
  signOut: () => Promise<void>;
  /** Vuelve a consultar /me (p. ej. tras un error de red). */
  retry: () => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then((me) => {
        if (!cancelled) setSession({ status: "authenticated", me });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const unauthenticated = error instanceof ApiError && error.status === 401;
        setSession({ status: unauthenticated ? "anonymous" : "unavailable" });
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  // Un 401 en cualquier llamada posterior significa que la sesión caducó o se revocó.
  useEffect(() => {
    const onExpired = () => setSession({ status: "anonymous" });
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, []);

  const signIn = useCallback((me: Me) => setSession({ status: "authenticated", me }), []);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // Sin red o con error del servidor: igual se limpia el estado local; la cookie
      // caduca sola por inactividad (30 min) y el servidor puede revocarla.
    }
    setSession({ status: "anonymous", reason: "signed-out" });
  }, []);

  const retry = useCallback(() => {
    setSession({ status: "loading" });
    setAttempt((value) => value + 1);
  }, []);

  const value = useMemo(
    () => ({ session, signIn, signOut, retry }),
    [session, signIn, signOut, retry],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (value === null) {
    throw new Error("useSession debe usarse dentro de <SessionProvider>.");
  }
  return value;
}
