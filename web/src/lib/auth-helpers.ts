/**
 * Funciones puras del flujo de login (sin dependencias, probadas con `npm test`).
 */

const FALLBACK_PATH = "/";
const MAX_NEXT_LENGTH = 200;
const PLACEHOLDER_ORIGIN = "http://localhost.invalid";

/**
 * Destino al que volver tras iniciar sesión. Solo rutas internas de la GUI:
 * rechaza URLs absolutas, `//host`, barras invertidas, caracteres de control,
 * /login (bucle) y /api. Ante cualquier duda vuelve a "/" (evita open redirect).
 */
export function safeNext(raw: string | null | undefined): string {
  if (!raw || raw.length > MAX_NEXT_LENGTH) return FALLBACK_PATH;
  if (!raw.startsWith("/") || raw.startsWith("//")) return FALLBACK_PATH;
  if ([...raw].some((char) => char === "\\" || char.charCodeAt(0) < 32)) return FALLBACK_PATH;

  let url: URL;
  try {
    url = new URL(raw, PLACEHOLDER_ORIGIN);
  } catch {
    return FALLBACK_PATH;
  }
  if (url.origin !== PLACEHOLDER_ORIGIN) return FALLBACK_PATH;
  if (url.pathname === "/login" || url.pathname.startsWith("/login/")) return FALLBACK_PATH;
  if (url.pathname === "/api" || url.pathname.startsWith("/api/")) return FALLBACK_PATH;
  return url.pathname + url.search;
}

/** Deja solo los dígitos (quita espacios y guiones que algunas apps agregan al código). */
export function normalizeTotpCode(raw: string): string {
  return raw.replace(/\D/g, "");
}

export function isValidTotpCode(code: string): boolean {
  return /^\d{6}$/.test(code);
}

export function formatWait(seconds: number | undefined): string {
  if (seconds === undefined || !Number.isFinite(seconds) || seconds <= 0) {
    return "unos minutos";
  }
  if (seconds < 60) return "menos de un minuto";
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return minutes === 1 ? "1 minuto" : `${minutes} minutos`;
  const hours = Math.ceil(minutes / 60);
  return hours === 1 ? "1 hora" : `${hours} horas`;
}

export interface LoginFailure {
  status: number;
  detail?: string;
  retryAfter?: number;
}

/**
 * Mensaje para el usuario ante un fallo de login. El de credenciales es siempre
 * genérico (no revela si falló el usuario o la contraseña, igual que la API).
 */
export function loginErrorMessage(
  failure: LoginFailure | null,
  step: "credentials" | "code",
): string {
  if (failure === null) {
    return "No se pudo conectar con el servidor. Revisa tu conexión e intenta de nuevo.";
  }
  switch (failure.status) {
    case 401:
      return step === "code"
        ? "El código no es válido o ya se usó. Espera al siguiente código de tu app autenticadora."
        : "Usuario o contraseña incorrectos.";
    case 403:
      // La API explica que falta enrolar el segundo factor; es texto fijo del servidor.
      return failure.detail ?? "No tienes permiso para iniciar sesión. Contacta al administrador.";
    case 429:
      return `Demasiados intentos. Vuelve a intentarlo en ${formatWait(failure.retryAfter)}.`;
    case 422:
      return "Revisa los datos: el usuario, la contraseña o el código no tienen un formato válido.";
    case 503:
      return "El servicio de autenticación no está disponible. Intenta de nuevo en un momento.";
    default:
      return "No se pudo iniciar sesión. Intenta de nuevo.";
  }
}

const ROLE_LABELS: Record<string, string> = {
  consulta: "Consulta",
  lider: "Líder",
  configuracion: "Configuración",
  control_total: "Control total",
};

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

/** Mismos límites que la política del servidor (la API valida siempre). */
export const MIN_PASSWORD_LENGTH = 12;
export const MAX_PASSWORD_LENGTH = 256;

export interface PasswordChangeInput {
  current: string;
  next: string;
  confirm: string;
}

/**
 * Revisión previa al envío del cambio de contraseña. `null` = se puede enviar.
 * Solo evita viajes inútiles: la política completa (repetitiva, común, contiene el
 * usuario) la aplica el servidor y su mensaje se muestra tal cual.
 */
export function passwordChangeProblem({ current, next, confirm }: PasswordChangeInput): string | null {
  if (current === "") return "Escribe tu contraseña actual.";
  if (next.length < MIN_PASSWORD_LENGTH) {
    return `La contraseña nueva debe tener al menos ${MIN_PASSWORD_LENGTH} caracteres.`;
  }
  if (next.length > MAX_PASSWORD_LENGTH) {
    return `La contraseña nueva no puede pasar de ${MAX_PASSWORD_LENGTH} caracteres.`;
  }
  if (next !== confirm) return "La confirmación no coincide con la contraseña nueva.";
  if (next === current) return "La contraseña nueva debe ser distinta.";
  return null;
}

/** Mensaje ante un fallo del cambio de contraseña. `null` = no hubo respuesta (red). */
export function passwordChangeErrorMessage(failure: LoginFailure | null): string {
  if (failure === null) {
    return "No se pudo conectar con el servidor. Revisa tu conexión e intenta de nuevo.";
  }
  switch (failure.status) {
    case 400:
      return "La contraseña actual no es correcta.";
    case 422:
      // Texto fijo de la política del servidor (p. ej. "demasiado común").
      return failure.detail ?? "La contraseña nueva no cumple la política.";
    case 429:
      return `Demasiados intentos. Vuelve a intentarlo en ${formatWait(failure.retryAfter)}.`;
    case 503:
      return "El servicio de autenticación no está disponible. Intenta de nuevo en un momento.";
    default:
      return "No se pudo cambiar la contraseña. Intenta de nuevo.";
  }
}

// -- Activación del segundo factor --------------------------------------------------------

/** Lo que devuelve la API al iniciar sesión dentro de la ventana de activación. */
export interface MfaEnrollment {
  secret: string;
  otpauthUri: string;
  /** QR como data URI SVG generado por el servidor. */
  qr: string;
  /** Segundos que dura la activación antes de caducar. */
  expiresIn: number;
}

const QR_PREFIX = "data:image/svg+xml;base64,";

/**
 * Valida la forma de `mfa_enrollment` antes de pintarla. El QR solo se acepta como
 * SVG en base64 (nunca una URL externa) y el URI solo como `otpauth://totp/`.
 */
export function parseMfaEnrollment(raw: unknown): MfaEnrollment | null {
  if (typeof raw !== "object" || raw === null) return null;
  const body = raw as Record<string, unknown>;
  const { secret, otpauth_uri: uri, qr, expires_in: expiresIn } = body;
  if (typeof secret !== "string" || !/^[A-Z2-7]{16,64}$/.test(secret)) return null;
  if (typeof uri !== "string" || !uri.startsWith("otpauth://totp/")) return null;
  if (typeof qr !== "string" || !qr.startsWith(QR_PREFIX)) return null;
  if (!/^[A-Za-z0-9+/=]+$/.test(qr.slice(QR_PREFIX.length))) return null;
  if (typeof expiresIn !== "number" || !Number.isFinite(expiresIn) || expiresIn <= 0) {
    return null;
  }
  return { secret, otpauthUri: uri, qr, expiresIn };
}

/** "JBSWY3DPEHPK3PXP" -> "JBSW Y3DP EHPK 3PXP", más fácil de copiar a mano. */
export function groupSecret(secret: string): string {
  return secret.match(/.{1,4}/g)?.join(" ") ?? secret;
}

/** Mensaje ante un fallo al confirmar la activación. `null` = no hubo respuesta (red). */
export function enrollErrorMessage(failure: LoginFailure | null): string {
  if (failure === null) {
    return "No se pudo conectar con el servidor. Revisa tu conexión e intenta de nuevo.";
  }
  switch (failure.status) {
    case 400:
      return "El código no coincide. Revisa que la hora de tu teléfono sea automática y usa el código que se ve ahora.";
    case 410:
      return "La activación caducó. Vuelve a iniciar sesión para empezar de nuevo.";
    case 422:
      return "Escribe los 6 dígitos que muestra tu app autenticadora.";
    case 429:
      return `Demasiados intentos. Vuelve a intentarlo en ${formatWait(failure.retryAfter)}.`;
    case 503:
      return "El servicio de autenticación no está disponible. Intenta de nuevo en un momento.";
    default:
      return "No se pudo activar el segundo factor. Intenta de nuevo.";
  }
}

// -- Mis sesiones ---------------------------------------------------------------------------

/** "hace 5 minutos", a partir de segundos epoch (como los devuelve la API). */
export function formatAgo(epochSeconds: number, nowSeconds: number): string {
  const elapsed = Math.max(0, nowSeconds - epochSeconds);
  if (elapsed < 60) return "hace un momento";
  const minutes = Math.floor(elapsed / 60);
  if (minutes < 60) return minutes === 1 ? "hace 1 minuto" : `hace ${minutes} minutos`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours === 1 ? "hace 1 hora" : `hace ${hours} horas`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "hace 1 día" : `hace ${days} días`;
}

export interface SessionLike {
  current: boolean;
  last_seen: number;
}

/** Esta sesión primero; las demás de la más a la menos reciente. */
export function sortSessions<T extends SessionLike>(sessions: readonly T[]): T[] {
  return [...sessions].sort(
    (a, b) => Number(b.current) - Number(a.current) || b.last_seen - a.last_seen,
  );
}

/** Mensaje ante un fallo al listar o cerrar sesiones. */
export function sessionsErrorMessage(failure: LoginFailure | null): string {
  if (failure === null) {
    return "No se pudo conectar con el servidor. Revisa tu conexión e intenta de nuevo.";
  }
  if (failure.status === 404) return "Esa sesión ya no existe. La lista se actualizó.";
  if (failure.status === 503) {
    return "El servicio de autenticación no está disponible. Intenta de nuevo en un momento.";
  }
  return "No se pudo completar la operación. Intenta de nuevo.";
}
