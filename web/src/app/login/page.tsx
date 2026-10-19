import type { Metadata } from "next";

import { LoginForm } from "@/components/LoginForm";
import { Shell } from "@/components/Shell";
import { safeNext } from "@/lib/auth-helpers";

export const metadata: Metadata = { title: "Entrar" };

interface LoginPageProps {
  searchParams: Promise<{ next?: string | string[] }>;
}

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const { next } = await searchParams;
  // `next` viene de la URL: solo se acepta una ruta interna (ver safeNext).
  const destination = safeNext(Array.isArray(next) ? next[0] : next);

  return (
    <Shell title="Entrar" requireSession={false}>
      <LoginForm next={destination} />
    </Shell>
  );
}
