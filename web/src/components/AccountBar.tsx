"use client";

import Link from "next/link";
import { useState } from "react";

import { useSession } from "@/components/SessionProvider";
import { can, type Permission } from "@/lib/api";
import { roleLabel } from "@/lib/auth-helpers";

/** Cada entrada pide un permiso; ocultar es UX, la API valida en servidor. */
const NAV: { href: string; label: string; permission: Permission }[] = [
  { href: "/consultar", label: "Consultar", permission: "query" },
  { href: "/fuentes", label: "Fuentes", permission: "configure_connectors" },
  { href: "/sistema", label: "Sistema", permission: "admin_system" },
];

/** Navegación según permisos + usuario actual y botón para salir. */
export function AccountBar() {
  const { session, signOut } = useSession();
  const [leaving, setLeaving] = useState(false);

  if (session.status !== "authenticated") return null;
  const { me } = session;

  async function onSignOut() {
    setLeaving(true);
    // La guardia de cada pantalla redirige a /login cuando el estado pasa a anónimo.
    await signOut();
  }

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
      <nav aria-label="Principal" className="flex gap-5">
        {NAV.filter((item) => can(me, item.permission)).map((item) => (
          <Link key={item.href} href={item.href} className="py-1 underline-offset-4 hover:underline">
            {item.label}
          </Link>
        ))}
      </nav>
      <span className="text-muted">
        {me.username} · {roleLabel(me.role)}
      </span>
      <button
        type="button"
        onClick={onSignOut}
        disabled={leaving}
        className="border border-line bg-panel px-3 py-1 disabled:opacity-50"
      >
        {leaving ? "Saliendo…" : "Salir"}
      </button>
    </div>
  );
}
