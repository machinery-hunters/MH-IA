/**
 * Cliente tipado de la API de MH IA.
 *
 * La API (FastAPI) es la fuente de verdad de roles y permisos. La GUI pide
 * sus permisos a /api/v1/me y SOLO los usa para decidir qué mostrar (UX):
 * ocultar un botón aquí no protege nada, cada endpoint valida en servidor.
 *
 * La sesión vive en una cookie HttpOnly que el navegador envía solo (misma
 * origen vía el proxy de Next); este código nunca ve ni guarda el token.
 */

export type Permission =
  | "query"
  | "query_confidential"
  | "configure_connectors"
  | "admin_system";

export interface Me {
  user_id: string;
  username: string;
  role: string;
  permissions: Permission[];
}

export interface ConnectorStatus {
  name: string;
  display_name: string;
  sensitivity: "public" | "internal" | "confidential" | "restricted";
  configured: boolean;
}

export interface LoginInput {
  username: string;
  password: string;
  /** Seis dígitos; solo se envía cuando la API ya pidió el segundo factor. */
  totpCode?: string;
}

/** Evento global: cualquier 401 fuera del login indica que la sesión terminó. */
export const SESSION_EXPIRED_EVENT = "mhia:session-expired";

const LOGIN_PATH = "/api/v1/auth/login";
const MAX_RETRY_AFTER_S = 24 * 60 * 60;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /** Mensaje en español que devuelve la API (genérico, sin datos sensibles). */
    readonly detail?: string,
    /** Solo en 429: segundos de espera según la cabecera Retry-After. */
    readonly retryAfter?: number,
    /** Solo en el login: la contraseña fue correcta y falta el código TOTP. */
    readonly mfaRequired: boolean = false,
  ) {
    super(message);
  }
}

interface ErrorBody {
  detail?: unknown;
  mfa_required?: unknown;
}

function parseRetryAfter(value: string | null): number | undefined {
  if (value === null || !/^\d+$/.test(value.trim())) return undefined;
  return Math.min(Number.parseInt(value, 10), MAX_RETRY_AFTER_S);
}

async function readErrorBody(response: Response): Promise<ErrorBody> {
  try {
    const body: unknown = await response.json();
    return typeof body === "object" && body !== null ? (body as ErrorBody) : {};
  } catch {
    return {};
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });

  if (!response.ok) {
    const body = await readErrorBody(response);
    // 422 de FastAPI trae una lista en `detail`: solo se usa si es texto.
    const detail = typeof body.detail === "string" ? body.detail : undefined;
    if (response.status === 401 && path !== LOGIN_PATH && typeof window !== "undefined") {
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    }
    throw new ApiError(
      response.status,
      `HTTP ${response.status}`,
      detail,
      parseRetryAfter(response.headers.get("Retry-After")),
      body.mfa_required === true,
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  me: () => request<Me>("/api/v1/me"),
  login: ({ username, password, totpCode }: LoginInput) =>
    request<Me>(LOGIN_PATH, {
      method: "POST",
      body: JSON.stringify({
        username,
        password,
        ...(totpCode ? { totp_code: totpCode } : {}),
      }),
    }),
  logout: () => request<void>("/api/v1/auth/logout", { method: "POST" }),
  connectors: () => request<ConnectorStatus[]>("/api/v1/connectors"),
  query: (query: string) =>
    request<unknown>("/api/v1/query", { method: "POST", body: JSON.stringify({ query }) }),
};

export function can(me: Me | null, permission: Permission): boolean {
  return me?.permissions.includes(permission) ?? false;
}
