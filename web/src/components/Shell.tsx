import Link from "next/link";
import type { ReactNode } from "react";

const NAV = [
  { href: "/consultar", label: "Consultar" },
  { href: "/fuentes", label: "Fuentes" },
  { href: "/sistema", label: "Sistema" },
];

export function Shell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-5xl flex-col gap-8 px-4 py-6">
      <header className="flex flex-wrap items-baseline justify-between gap-4 border-b border-line pb-4">
        <Link href="/" className="text-lg font-semibold tracking-tight">
          MH IA
        </Link>
        <nav aria-label="Principal" className="flex gap-5 text-sm">
          {NAV.map((item) => (
            <Link key={item.href} href={item.href} className="py-1 underline-offset-4 hover:underline">
              {item.label}
            </Link>
          ))}
        </nav>
      </header>
      <main className="flex flex-1 flex-col gap-6">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {children}
      </main>
    </div>
  );
}
