import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  formatWait,
  isValidTotpCode,
  loginErrorMessage,
  normalizeTotpCode,
  passwordChangeErrorMessage,
  passwordChangeProblem,
  roleLabel,
  safeNext,
} from "./auth-helpers.ts";

describe("safeNext", () => {
  it("acepta rutas internas con query", () => {
    assert.equal(safeNext("/consultar"), "/consultar");
    assert.equal(safeNext("/fuentes?x=1"), "/fuentes?x=1");
  });

  it("vuelve a / si falta o viene vacío", () => {
    assert.equal(safeNext(null), "/");
    assert.equal(safeNext(undefined), "/");
    assert.equal(safeNext(""), "/");
  });

  it("rechaza URLs absolutas, protocolo relativo y esquemas peligrosos", () => {
    for (const raw of [
      "https://evil.example/",
      "//evil.example",
      "///evil.example",
      "javascript:alert(1)",
      "evil.example",
      "/\\evil.example",
    ]) {
      assert.equal(safeNext(raw), "/", raw);
    }
    // Lo que sí pasa es siempre una ruta interna: nunca empieza con "//".
    assert.ok(!safeNext("/%5Cevil.example").startsWith("//"));
  });

  it("rechaza caracteres de control y valores enormes", () => {
    assert.equal(safeNext("/a\nb"), "/");
    assert.equal(safeNext("/a\tb"), "/");
    assert.equal(safeNext("/" + "a".repeat(300)), "/");
  });

  it("evita el bucle hacia /login y no redirige a /api", () => {
    assert.equal(safeNext("/login"), "/");
    assert.equal(safeNext("/login?next=/x"), "/");
    assert.equal(safeNext("/login/otra"), "/");
    assert.equal(safeNext("/api/v1/me"), "/");
    assert.equal(safeNext("/api"), "/");
    // Prefijos parecidos sí son rutas legítimas.
    assert.equal(safeNext("/loginx"), "/loginx");
    assert.equal(safeNext("/apiary"), "/apiary");
  });
});

describe("código TOTP", () => {
  it("normaliza espacios y guiones", () => {
    assert.equal(normalizeTotpCode("123 456"), "123456");
    assert.equal(normalizeTotpCode("123-456"), "123456");
    assert.equal(normalizeTotpCode(" 12a3456 "), "123456");
  });

  it("valida exactamente seis dígitos", () => {
    assert.equal(isValidTotpCode("123456"), true);
    assert.equal(isValidTotpCode("12345"), false);
    assert.equal(isValidTotpCode("1234567"), false);
    assert.equal(isValidTotpCode("12345a"), false);
    assert.equal(isValidTotpCode(""), false);
  });
});

describe("formatWait", () => {
  it("redondea hacia arriba y usa el plural correcto", () => {
    assert.equal(formatWait(900), "15 minutos");
    assert.equal(formatWait(61), "2 minutos");
    assert.equal(formatWait(60), "1 minuto");
    assert.equal(formatWait(30), "menos de un minuto");
    assert.equal(formatWait(3600), "1 hora");
    assert.equal(formatWait(7201), "3 horas");
  });

  it("ante un valor ausente o inválido no inventa un número", () => {
    assert.equal(formatWait(undefined), "unos minutos");
    assert.equal(formatWait(0), "unos minutos");
    assert.equal(formatWait(-5), "unos minutos");
    assert.equal(formatWait(Number.NaN), "unos minutos");
  });
});

describe("loginErrorMessage", () => {
  it("el 401 de credenciales es genérico y no distingue usuario de contraseña", () => {
    const message = loginErrorMessage({ status: 401 }, "credentials");
    assert.equal(message, "Usuario o contraseña incorrectos.");
  });

  it("el 401 en el paso del código habla del código", () => {
    assert.match(loginErrorMessage({ status: 401 }, "code"), /código/);
  });

  it("el 403 muestra el texto de la API y tiene respaldo", () => {
    assert.equal(
      loginErrorMessage({ status: 403, detail: "Falta segundo factor." }, "credentials"),
      "Falta segundo factor.",
    );
    assert.match(loginErrorMessage({ status: 403 }, "credentials"), /administrador/);
  });

  it("el 429 indica cuánto esperar", () => {
    assert.match(
      loginErrorMessage({ status: 429, retryAfter: 900 }, "credentials"),
      /15 minutos/,
    );
  });

  it("cubre 422, 503, sin red y errores inesperados", () => {
    assert.match(loginErrorMessage({ status: 422 }, "credentials"), /formato/);
    assert.match(loginErrorMessage({ status: 503 }, "credentials"), /no está disponible/);
    assert.match(loginErrorMessage(null, "credentials"), /conectar/);
    assert.match(loginErrorMessage({ status: 500 }, "credentials"), /No se pudo iniciar sesión/);
  });
});

describe("roleLabel", () => {
  it("traduce los roles conocidos y deja pasar los desconocidos", () => {
    assert.equal(roleLabel("control_total"), "Control total");
    assert.equal(roleLabel("lider"), "Líder");
    assert.equal(roleLabel("otro"), "otro");
  });
});

describe("passwordChangeProblem", () => {
  const ok = { current: "vieja-clave-1", next: "una frase bastante larga", confirm: "una frase bastante larga" };

  it("deja pasar un cambio válido", () => {
    assert.equal(passwordChangeProblem(ok), null);
  });

  it("pide la actual, el largo mínimo, la confirmación y que sea distinta", () => {
    assert.match(passwordChangeProblem({ ...ok, current: "" }) ?? "", /actual/);
    assert.match(passwordChangeProblem({ ...ok, next: "corta", confirm: "corta" }) ?? "", /12/);
    assert.match(passwordChangeProblem({ ...ok, confirm: "otra cosa distinta" }) ?? "", /confirmación/);
    assert.match(
      passwordChangeProblem({ current: ok.next, next: ok.next, confirm: ok.next }) ?? "",
      /distinta/,
    );
    const huge = "a".repeat(257);
    assert.match(passwordChangeProblem({ ...ok, next: huge, confirm: huge }) ?? "", /256/);
  });
});

describe("passwordChangeErrorMessage", () => {
  it("distingue la contraseña actual incorrecta de la política", () => {
    assert.match(passwordChangeErrorMessage({ status: 400 }), /actual no es correcta/);
    assert.equal(
      passwordChangeErrorMessage({ status: 422, detail: "La contraseña es demasiado común." }),
      "La contraseña es demasiado común.",
    );
    assert.match(passwordChangeErrorMessage({ status: 422 }), /política/);
  });

  it("429 dice cuánto esperar y sin red habla de la conexión", () => {
    assert.match(passwordChangeErrorMessage({ status: 429, retryAfter: 600 }), /10 minutos/);
    assert.match(passwordChangeErrorMessage(null), /conectar/);
    assert.match(passwordChangeErrorMessage({ status: 503 }), /no está disponible/);
  });
});
