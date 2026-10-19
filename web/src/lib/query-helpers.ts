/**
 * Funciones puras de la pantalla de consulta (sin dependencias, probadas con `npm test`).
 */

export const MAX_QUERY_LENGTH = 2000; // mismo límite que valida la API (la API valida siempre)

export type QueryStatus = "answered" | "no_evidence" | "blocked";

/** Etiqueta del resultado: siempre en texto, el estado no depende solo del color. */
export function statusLabel(status: QueryStatus): string {
  switch (status) {
    case "answered":
      return "Respuesta";
    case "no_evidence":
      return "Sin información suficiente";
    case "blocked":
      return "Envío bloqueado por privacidad";
  }
}

export function canSubmitQuery(raw: string): boolean {
  const trimmed = raw.trim();
  return trimmed.length > 0 && raw.length <= MAX_QUERY_LENGTH;
}

export interface QueryFailure {
  status: number;
  detail?: string;
}

/**
 * Mensaje ante un fallo de la consulta. `null` = no hubo respuesta del servidor (red).
 * Los textos 502/504 vienen de la API (genéricos, sin contenido de la consulta).
 */
export function queryErrorMessage(failure: QueryFailure | null): string {
  if (failure === null) {
    return "No se pudo conectar con el servidor. Revisa tu conexión e intenta de nuevo.";
  }
  switch (failure.status) {
    case 403:
      return "Tu rol no tiene permiso para consultar.";
    case 422:
      return "La consulta no es válida. Escribe una pregunta de hasta 2000 caracteres.";
    case 502:
    case 504:
      return failure.detail ?? "No se pudo completar la consulta. Intenta de nuevo.";
    case 503:
      return "El servicio no está disponible. Intenta de nuevo en un momento.";
    default:
      return "No se pudo completar la consulta. Intenta de nuevo.";
  }
}
