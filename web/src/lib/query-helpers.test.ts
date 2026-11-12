import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  MAX_QUERY_LENGTH,
  canSubmitQuery,
  queryErrorMessage,
  reasonerNote,
  statusLabel,
} from "./query-helpers.ts";

describe("statusLabel", () => {
  it("cada estado tiene una etiqueta de texto distinta", () => {
    const labels = [
      statusLabel("answered"),
      statusLabel("no_evidence"),
      statusLabel("blocked"),
    ];
    assert.equal(new Set(labels).size, 3);
    assert.equal(statusLabel("no_evidence"), "Sin información suficiente");
  });
});

describe("reasonerNote", () => {
  it("solo avisa cuando respondió el modelo local", () => {
    assert.match(reasonerNote("local") ?? "", /modelo local/);
    assert.equal(reasonerNote("claude"), null);
    assert.equal(reasonerNote(null), null);
    assert.equal(reasonerNote(undefined), null);
  });
});

describe("canSubmitQuery", () => {
  it("rechaza vacío o solo espacios", () => {
    assert.equal(canSubmitQuery(""), false);
    assert.equal(canSubmitQuery("   \n\t"), false);
  });

  it("acepta texto normal y el límite exacto", () => {
    assert.equal(canSubmitQuery("¿Qué equipos hay en consignación?"), true);
    assert.equal(canSubmitQuery("x".repeat(MAX_QUERY_LENGTH)), true);
  });

  it("rechaza lo que excede el límite de la API", () => {
    assert.equal(canSubmitQuery("x".repeat(MAX_QUERY_LENGTH + 1)), false);
  });
});

describe("queryErrorMessage", () => {
  it("sin respuesta del servidor habla de la conexión", () => {
    assert.match(queryErrorMessage(null), /conectar/);
  });

  it("429 avisa del límite y dice cuánto esperar", () => {
    assert.match(queryErrorMessage({ status: 429, retryAfter: 30 }), /muchas consultas/);
    assert.match(queryErrorMessage({ status: 429, retryAfter: 30 }), /menos de un minuto/);
    assert.match(queryErrorMessage({ status: 429 }), /unos minutos/);
  });

  it("403 y 422 tienen mensajes propios", () => {
    assert.match(queryErrorMessage({ status: 403 }), /rol/);
    assert.match(queryErrorMessage({ status: 422 }), /2000/);
  });

  it("502 y 504 usan el texto de la API y tienen respaldo", () => {
    assert.equal(
      queryErrorMessage({ status: 504, detail: "La consulta tardó demasiado. Intenta de nuevo." }),
      "La consulta tardó demasiado. Intenta de nuevo.",
    );
    assert.match(queryErrorMessage({ status: 502 }), /No se pudo completar/);
  });

  it("un detalle inesperado en otros códigos nunca se muestra tal cual", () => {
    const message = queryErrorMessage({ status: 500, detail: "Traceback: secreto Acme" });
    assert.ok(!message.includes("Acme") && !message.includes("Traceback"));
  });

  it("503 indica servicio no disponible", () => {
    assert.match(queryErrorMessage({ status: 503 }), /no está disponible/);
  });
});
