"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { useSession } from "@/components/SessionProvider";
import { api, ApiError } from "@/lib/api";
import { isValidTotpCode, loginErrorMessage, normalizeTotpCode } from "@/lib/auth-helpers";

type Step = "credentials" | "code";

const INPUT_CLASS = "border border-line bg-panel p-3";

/**
 * Login en dos pasos que decide la API, no la GUI: se envían usuario y
 * contraseña; si la API responde `mfa_required` (contraseña correcta, falta el
 * segundo factor) se pide el código de 6 dígitos y se reenvía todo junto.
 * La validación real es siempre del servidor; aquí solo se mejora la UX.
 */
export function LoginForm({ next }: { next: string }) {
  const { session, signIn } = useSession();
  const router = useRouter();
  const [step, setStep] = useState<Step>("credentials");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Con sesión (recién iniciada o ya existente) no hay nada que hacer en /login.
  useEffect(() => {
    if (session.status === "authenticated") router.replace(next);
  }, [session.status, next, router]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;

    const totpCode = step === "code" ? normalizeTotpCode(code) : undefined;
    if (step === "code" && !isValidTotpCode(totpCode ?? "")) {
      setError("Escribe los 6 dígitos que muestra tu app autenticadora.");
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const me = await api.login({ username: username.trim(), password, totpCode });
      // La contraseña no debe quedarse en memoria más de lo necesario.
      setPassword("");
      setCode("");
      signIn(me);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 401 && failure.mfaRequired) {
        setStep("code");
        return;
      }
      if (failure instanceof ApiError) {
        setError(
          loginErrorMessage(
            { status: failure.status, detail: failure.detail, retryAfter: failure.retryAfter },
            step,
          ),
        );
        if (step === "credentials" && failure.status === 401) setPassword("");
      } else {
        setError(loginErrorMessage(null, step));
      }
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  function backToCredentials() {
    setStep("credentials");
    setPassword("");
    setCode("");
    setError(null);
  }

  if (session.status === "authenticated") {
    return (
      <p role="status" className="text-muted">
        Sesión iniciada. Redirigiendo…
      </p>
    );
  }

  return (
    <form onSubmit={onSubmit} className="flex max-w-sm flex-col gap-4" noValidate>
      {step === "credentials" ? (
        <>
          <div className="flex flex-col gap-1">
            <label htmlFor="username" className="text-sm font-medium">
              Usuario
            </label>
            <input
              id="username"
              name="username"
              type="text"
              autoComplete="username"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              required
              maxLength={64}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              className={INPUT_CLASS}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="password" className="text-sm font-medium">
              Contraseña
            </label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              maxLength={256}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className={INPUT_CLASS}
            />
          </div>
        </>
      ) : (
        <div className="flex flex-col gap-1">
          <p className="text-sm text-muted">
            Verificación en dos pasos para <strong className="text-ink">{username.trim()}</strong>.
          </p>
          <label htmlFor="code" className="text-sm font-medium">
            Código de tu app autenticadora
          </label>
          <input
            id="code"
            name="code"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            autoFocus
            required
            maxLength={7}
            placeholder="123456"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            className={`${INPUT_CLASS} tracking-widest`}
          />
        </div>
      )}

      <p role="alert" className="min-h-6 text-sm text-bad">
        {error}
      </p>

      <div className="flex items-center gap-4">
        <button
          type="submit"
          disabled={busy || username.trim() === "" || (step === "credentials" && password === "")}
          className="bg-ink px-4 py-2 text-sm text-panel disabled:opacity-50"
        >
          {busy ? "Verificando…" : step === "code" ? "Verificar" : "Entrar"}
        </button>
        {step === "code" && (
          <button type="button" onClick={backToCredentials} className="text-sm underline underline-offset-4">
            Usar otro usuario
          </button>
        )}
      </div>
    </form>
  );
}
