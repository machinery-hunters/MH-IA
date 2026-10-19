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
