"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { useSession } from "@/components/SessionProvider";
import { api, ApiError, type MfaEnrollment } from "@/lib/api";
import {
  enrollErrorMessage,
  groupSecret,
  isValidTotpCode,
  loginErrorMessage,
  normalizeTotpCode,
} from "@/lib/auth-helpers";

type Step = "credentials" | "code" | "enroll";

const INPUT_CLASS = "border border-line bg-panel p-3";

/**
 * Login en dos pasos que decide la API, no la GUI: se envían usuario y
 * contraseña; si la API responde `mfa_required` (contraseña correcta, falta el
 * segundo factor) se pide el código de 6 dígitos y se reenvía todo junto.
 * Si responde `mfa_enrollment` (la persona debe activar su segundo factor y el
 * administrador le abrió la ventana) se muestra el QR y se confirma con el
 * primer código. La validación real es siempre del servidor; aquí solo se mejora la UX.
 */
export function LoginForm({ next }: { next: string }) {
  const { session, signIn } = useSession();
  const router = useRouter();
  const [step, setStep] = useState<Step>("credentials");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [enrollment, setEnrollment] = useState<MfaEnrollment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Con sesión (recién iniciada o ya existente) no hay nada que hacer en /login.
  useEffect(() => {
    if (session.status === "authenticated") router.replace(next);
  }, [session.status, next, router]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;

    const totpCode = step === "credentials" ? undefined : normalizeTotpCode(code);
    if (step !== "credentials" && !isValidTotpCode(totpCode ?? "")) {
      setError("Escribe los 6 dígitos que muestra tu app autenticadora.");
      return;
    }
    if (step === "enroll") {
      await confirmEnrollment(totpCode ?? "");
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
      if (failure instanceof ApiError && failure.status === 401 && failure.mfaEnrollment) {
        // La contraseña ya cumplió su papel: la activación sigue con su propia cookie.
        setPassword("");
        setEnrollment(failure.mfaEnrollment);
        setStep("enroll");
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

  async function confirmEnrollment(totpCode: string) {
    setBusy(true);
    setError(null);
    try {
      const me = await api.enrollTotp(totpCode);
      setEnrollment(null);
      setCode("");
      signIn(me);
    } catch (failure) {
      const status = failure instanceof ApiError ? failure.status : null;
      const message = enrollErrorMessage(
        failure instanceof ApiError
          ? { status: failure.status, detail: failure.detail, retryAfter: failure.retryAfter }
          : null,
      );
      setCode("");
      if (status === 410) {
        backToCredentials();
      }
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  function backToCredentials() {
    setStep("credentials");
    setPassword("");
    setCode("");
    setEnrollment(null);
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
        <>
          {step === "enroll" && enrollment && <EnrollmentInstructions enrollment={enrollment} />}
          <div className="flex flex-col gap-1">
            <p className="text-sm text-muted">
              {step === "enroll"
                ? "Activación del segundo factor para"
                : "Verificación en dos pasos para"}{" "}
              <strong className="text-ink">{username.trim()}</strong>.
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
        </>
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
          {busy
            ? "Verificando…"
            : step === "enroll"
              ? "Activar y entrar"
              : step === "code"
                ? "Verificar"
                : "Entrar"}
        </button>
        {step !== "credentials" && (
          <button type="button" onClick={backToCredentials} className="text-sm underline underline-offset-4">
            Usar otro usuario
          </button>
        )}
      </div>
    </form>
  );
}

/**
 * QR y clave para la app autenticadora. Ambos los genera el servidor; la clave se
 * muestra agrupada para escribirla a mano si la cámara no lee el QR.
 */
function EnrollmentInstructions({ enrollment }: { enrollment: MfaEnrollment }) {
  const minutes = Math.max(1, Math.floor(enrollment.expiresIn / 60));
  return (
    <section aria-labelledby="enroll-title" className="flex flex-col gap-3 border border-line bg-panel p-4">
      <h2 id="enroll-title" className="font-medium">
        Activa tu segundo factor
      </h2>
      <p className="text-sm text-muted">
        Tu cuenta ve información confidencial, así que además de la contraseña pedirá un código de
        tu teléfono. Instala una app autenticadora (Google Authenticator, Microsoft Authenticator u
        otra), escanea este código y escribe abajo los 6 dígitos que muestre. Tienes {minutes}{" "}
        {minutes === 1 ? "minuto" : "minutos"}.
      </p>
      {/* eslint-disable-next-line @next/next/no-img-element -- data URI del servidor, sin optimizar */}
      <img
        src={enrollment.qr}
        alt="Código QR para tu app autenticadora"
        width={200}
        height={200}
        className="self-center bg-white"
      />
      <p className="text-sm">
        ¿No puedes escanear? Escribe esta clave en la app:{" "}
        <code className="break-all font-mono text-ink">{groupSecret(enrollment.secret)}</code>
      </p>
      <a href={enrollment.otpauthUri} className="text-sm underline underline-offset-4">
        Abrir en la app autenticadora de este teléfono
      </a>
    </section>
  );
}
