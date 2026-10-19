import Link from "next/link";
import type { ReactNode } from "react";

import { AccountBar } from "@/components/AccountBar";
import { RequireSession } from "@/components/RequireSession";

interface ShellProps {
  title: string;
  children: ReactNode;
  /**
   * Por defecto toda pantalla exige sesión: sin ella manda a /login y no monta
   * el contenido. Solo la pantalla de login lo desactiva.
   */
  requireSession?: boolean;
}

export function Shell({ title, children, requireSession = true }: ShellProps) {
  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-5xl flex-col gap-8 px-4 py-6">
      <header className="flex flex-wrap items-baseline justify-between gap-4 border-b border-line pb-4">
        <Link href="/" className="text-lg font-semibold tracking-tight">
          MH IA
        </Link>
        <AccountBar />
      </header>
      <main className="flex flex-1 flex-col gap-6">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {requireSession ? <RequireSession>{children}</RequireSession> : children}
      </main>
    </div>
  );
}
