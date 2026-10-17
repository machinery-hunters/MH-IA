/**
 * Cliente tipado de la API de MH IA.
 *
 * La API (FastAPI) es la fuente de verdad de roles y permisos. La GUI pide
 * sus permisos a /api/v1/me y SOLO los usa para decidir qué mostrar (UX):
 * ocultar un botón aquí no protege nada, cada endpoint valida en servidor.
 */

export type Permission =
  | "query"
  | "query_confidential"
  | "configure_connectors"
  | "admin_system";

export interface Me {
  user_id: string;
  role: string;
  permissions: Permission[];
}

export interface ConnectorStatus {
  name: string;
  display_name: string;
  sensitivity: "public" | "internal" | "confidential" | "restricted";
  configured: boolean;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
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
    throw new ApiError(response.status, `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export const api = {
  me: () => request<Me>("/api/v1/me"),
  connectors: () => request<ConnectorStatus[]>("/api/v1/connectors"),
  query: (query: string) =>
    request<unknown>("/api/v1/query", { method: "POST", body: JSON.stringify({ query }) }),
};

export function can(me: Me | null, permission: Permission): boolean {
  return me?.permissions.includes(permission) ?? false;
}
