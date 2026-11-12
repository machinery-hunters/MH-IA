/**
 * Funciones puras de la pantalla de consulta (sin dependencias, probadas con `npm test`).
 */

export const MAX_QUERY_LENGTH = 2000; // mismo límite que valida la API (la API valida siempre)

export type QueryStatus = "answered" | "no_evidence" | "blocked";

/** Quién redactó la respuesta: el modelo de la red local o Claude (solo con status=answered). */
export type QueryReasoner = "local" | "claude";

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

/**
 * Aviso bajo una respuesta redactada por el modelo local (modo sin costo, o Claude no
 * disponible). `null` = sin aviso. El dato que se muestra es el mismo; cambia la redacción.
 */
export function reasonerNote(reasoner: QueryReasoner | null | undefined): string | null {
  return reasoner === "local"
    ? "Respuesta redactada por el modelo local de Machinery Hunters: puede ser más breve o menos pulida."
    : null;
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
