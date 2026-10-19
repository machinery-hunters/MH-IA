"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useSession } from "@/components/SessionProvider";

/**
 * Guardia de pantalla: no monta a sus hijos (y por tanto no dispara sus
 * llamadas a la API) hasta tener una sesión válida; sin sesión manda a /login
 * recordando a dónde volver. Es UX: la API vuelve a validar todo.
 */
export function RequireSession({ children }: { children: ReactNode }) {
  const { session, retry } = useSession();
  const router = useRouter();
  const pathname = usePathname();

  const signedOut = session.status === "anonymous" && session.reason === "signed-out";

  useEffect(() => {
    if (session.status !== "anonymous") return;
    // Si la persona salió a propósito no se recuerda la pantalla; si expiró, sí.
    router.replace(signedOut ? "/login" : `/login?next=${encodeURIComponent(pathname)}`);
  }, [session.status, signedOut, router, pathname]);

  if (session.status === "authenticated") return <>{children}</>;

  if (session.status === "unavailable") {
    return (
      <div role="alert" className="flex max-w-prose flex-col items-start gap-3">
        <p className="text-bad">No se pudo verificar tu sesión. Intenta de nuevo en un momento.</p>
        <button type="button" onClick={retry} className="border border-line bg-panel px-4 py-2 text-sm">
          Reintentar
        </button>
      </div>
    );
  }

  return (
    <p role="status" className="text-muted">
      Verificando sesión…
    </p>
  );
}
