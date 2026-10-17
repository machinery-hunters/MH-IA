# SECURITY.md — MH IA

Documento vivo. Cada cambio de arquitectura que toque datos, red, credenciales
o permisos actualiza este archivo en el mismo commit. Tres secciones:
**cubierto** (con la prueba que lo respalda), **aceptado como riesgo**
(decisión consciente, con su motivo) y **explícitamente no resuelto**
(sabemos que falta; no se afirma lo contrario).

Última revisión: 17 de octubre de 2026 (preparación del entorno, antes del
desarrollo pesado posterior al 27 de noviembre de 2026).

---

## 1. Cubierto

| Control | Dónde vive | Cómo se verifica |
|---|---|---|
| Secretos fuera de git (`.env`, `credentials/*.json`, `*.pem`, `*.key`) | `.gitignore` | `tests/test_infra_hardening.py`; job `secrets-scan` (gitleaks) en CI; `scripts/dev.ps1 doctor` |
| `.env.example` solo con valores vacíos o placeholders | `.env.example` | `test_env_example_has_no_real_looking_secrets` |
| Fallo explícito si falta un secreto crítico en producción | `Settings.require_production_secrets()`, llamado al arrancar la API | `tests/test_config.py` (6 casos) |
| `DEBUG` apagado por defecto; `/docs` y `/openapi.json` desactivados salvo debug | `config.py`, `api/main.py` | `test_debug_is_off_by_default`, `test_docs_are_disabled_unless_debug` |
| Credenciales con carga perezosa (nada se valida ni conecta al importar) | patrón `@lru_cache` + `settings.require()` | `test_lazy_clients_validate_at_use_not_at_import` |
| Qdrant y Ollama **no publican puertos** al host ni a la LAN | `docker-compose*.yml` | `test_qdrant_and_ollama_never_publish_ports` |
| `mhia-api` y `web` publican **solo en `127.0.0.1`** | `docker-compose.yml` | `test_published_ports_only_on_loopback` |
| `web` no recibe `.env` y no está en la red `backend` (no alcanza Qdrant/Ollama) | `docker-compose.yml` | `test_web_gets_no_backend_secrets_and_no_backend_network` |
| Contenedores propios sin root; `cap_drop: ALL`, `no-new-privileges`, filesystem de solo lectura (api, web) | `Dockerfile`s, compose | `test_api_dockerfile_does_not_run_as_root` (el resto: revisión de compose) |
| El router de confidencialidad **falla cerrado**: JSON inválido, dominio inventado o clave ausente se tratan como confidenciales | `nodes/router.py` | `tests/test_router_failclosed.py` |
| Sensibilidad efectiva = **máximo** de las señales; sin señales = confidencial | `privacy/sensitivity.py` | `tests/test_sensitivity.py` |
| Cero alucinaciones: sin evidencia, el flujo aborta sin llamar a Claude | `nodes/guardrail_node.py`, `agents/supervisor.should_abort` | `test_guardrail.py`, `test_graph_structure.py`, `test_subagent_isolation.py` |
| Sub-agentes aislados: timeout, excepción o falta de credenciales no tumban la petición | `agents/subagent.py` | `tests/test_subagent_isolation.py` |
| Anonimización **falla cerrada**: si no se pudo anonimizar, nunca se devuelve el dato crudo; los errores no filtran contenido de registros | `agents/subagent.py` | `test_anonymizer_failure_fails_closed_never_returns_raw`, `test_exception_does_not_escape_and_does_not_leak_message` |
| RBAC en servidor con **denegación por defecto**; rol desconocido = sin permisos | `security/roles.py`, `api/deps.py` | `tests/test_security_roles.py`, `tests/test_api.py` |
| Respuesta de conectores sin valores de credenciales | `api/main.py` | `test_connectors_endpoint_lists_status_without_secrets` |
| Validación de entrada en servidor (longitud de la consulta) | `api/main.py` (`QueryRequest`) | `test_query_validates_input_server_side` |
| Lista blanca de cabecera `Host` | `TrustedHostMiddleware` | `test_untrusted_host_header_is_rejected` |
| GUI: CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, sin `X-Powered-By`; misma-origen hacia la API (sin CORS) | `web/next.config.ts` | Verificado levantando la GUI real y leyendo las cabeceras |
| PWA sin caché de datos: el service worker no intercepta `fetch` | `web/public/sw.js` | Revisión de código |

## 2. Aceptado como riesgo

| Riesgo | Por qué se acepta | Revisar cuando |
|---|---|---|
| Qdrant sin API key | No publica puerto; solo `mhia-api` está en su red | Si algún otro servicio entra a la red `backend` o se publica un puerto |
| Qdrant `v1.11.0` y `ollama:latest` sin versión probada/pineada | Actualizar sin pruebas de reindexación puede romper el almacenamiento; se fijará al migrar a la Mac mini | Antes de producción |
| Qdrant y Ollama pueden salir a internet | Ollama lo necesita para `ollama pull`. Qdrant comparte la red | Si se descarga modelos por otra vía y se aísla `backend` con `internal: true` |
| CSP con `'unsafe-inline'` en `script-src` | Next.js inyecta scripts de arranque en línea | Al implementar CSP con nonce |
| Volumen de Qdrant y `.env` sin cifrado propio | Dependen del cifrado de disco del equipo (BitLocker en la laptop, FileVault en la Mac mini) | Activarlo y confirmarlo como parte de `deploy-checklist` |
| Tailscale (servicio de terceros) coordina la red privada | Los datos viajan cifrados extremo a extremo (WireGuard); el servicio coordina claves y dispositivos, no ve el tráfico | Si Dirección exige infraestructura 100 % propia (alternativa: Headscale) |
| Nombres y reparto de roles son provisionales | Hay que validarlos con Dirección | Antes de implementar el login |
| Ollama `llama3.1:8b` como anonimizador y clasificador | Es el modelo calibrado hoy; se compensa con piso por conector y scrubber (ver sección 3) | Con cada cambio de modelo, correr el eval |

## 3. Explícitamente no resuelto

Cosas que sabemos que faltan. Hasta que cada una se cierre, **la API rechaza
todo** (`get_current_principal` devuelve 401) y el pipeline **no está expuesto
por la API**: `POST /api/v1/query` responde 501.

1. **Autenticación inexistente.** Falta login, sesiones (cookie `httpOnly` firmada con `API_SESSION_SECRET`), hash argon2, protección contra fuerza bruta y el segundo factor del rol `control_total`.
2. **`claude_node` envía la consulta cruda y los nombres de archivo de las citas a Claude.** Un nombre como `Contrato_Consignacion_034.pdf` o una pregunta con el nombre de un cliente salen de la red local sin anonimizar. Se cierra con el *outbound gateway* (`privacy/outbound_gateway.py`, hoy stub). **No conectar el pipeline a la API ni a usuarios reales antes de esto.**
3. **Sin scrubber determinista.** La anonimización depende solo del LLM local (probabilístico). Falta la capa de regex (RFC, CURP, correos, teléfonos, CLABE, montos) y bloquear el envío si quedan hallazgos (`privacy/scrubber.py`, stub).
4. **Sin filtrado por rol a nivel de documento en Qdrant.** Falta indexar `sensitivity` y `allowed_roles` en el payload y filtrar en cada consulta. Un filtro de payload tampoco basta por sí solo: el control real vive también en la API.
5. **Sin auditoría.** No hay registro de quién consultó qué ni de qué salió hacia Claude (se registrará hash del payload, nunca contenido).
6. **Sin rate limiting** en la API.
7. **Inyección de instrucciones vía documentos.** Un documento de Drive o un registro del CRM puede contener texto que intente instruir al modelo. Mitigación prevista: el nodo Claude no tiene herramientas ni acciones (solo produce texto), el contexto va delimitado y la respuesta exige citas; falta una prueba adversarial.
8. **Eval del router al 89 %** con 3 fallas conocidas sin resolver; ahora, además, el piso por conector cubre ERP/CRM/Firestore, pero Drive sigue dependiendo del router y del mapeo de carpetas.
9. **Listas blancas de campos** de ERP, CRM y Firestore por construir (llegan con cada conector).
10. **Prompt injection hacia el anonimizador**, **backups y restauración** de Qdrant y del manifest, **rotación de secretos**, **escaneo de dependencias e imágenes** (Dependabot/Trivy): sin implementar.
11. **Sin CSP con nonce** ni protección CSRF explícita (la cookie de sesión será `SameSite=Strict`; se verificará al implementar el login).

---

## 4. Modelo de acceso remoto y roles

### Capa de red (mantiene los datos "en casa")

- **Nada de la Mac mini se expone a internet.** Ningún puerto abierto en el router.
- **Tailscale instalado en el host** (no en Docker). Solo los dispositivos aprobados del tailnet llegan a la Mac mini.
- La GUI se publica **solo dentro del tailnet** con `tailscale serve` (HTTPS con certificado del tailnet). **No se usa `tailscale funnel`** (expondría el servicio a internet).
- ACL por etiqueta: solo dispositivos de personas autorizadas pueden llegar a `tag:mhia-brain`. Expiración de claves activa, aprobación de dispositivos activa, MFA en el proveedor de identidad del tailnet.
- Dentro de Docker, `mhia-api` y `web` escuchan en `127.0.0.1` del host; `tailscale serve` es el único camino hacia ellos.

### Capa de aplicación (sobre la VPN)

La API es la única fuente de verdad. La GUI pide sus permisos a `GET /api/v1/me`.

| Rol | Permisos | Ve fuentes | Notas |
|---|---|---|---|
| `consulta` | `query` | PUBLIC, INTERNAL | |
| `lider` | + `query_confidential` | + CONFIDENTIAL | ERP/CRM/Firestore son CONFIDENTIAL por defecto |
| `configuracion` | + `configure_connectors` | igual que `lider` | Ve el estado de los conectores, nunca los valores |
| `control_total` | todos (+ `admin_system`) | + RESTRICTED | El más restringido; segundo factor obligatorio |

- Cada endpoint exige un permiso con `require_permission`. Ocultar algo en la GUI es UX, no seguridad.
- Rol desconocido o ausente = sin permisos.

### Segundo factor para `control_total` (decisión y trade-off)

**Decisión:** sí, obligatorio para `control_total` (configurar credenciales de conectores, tocar la infraestructura).

| Opción | A favor | En contra |
|---|---|---|
| TOTP (app autenticadora) | Simple, funciona hoy en cualquier dispositivo, sin dependencias | Phishable; el secreto TOTP vive en el servidor |
| Passkeys (WebAuthn) | Resistente a phishing; da biometría del dispositivo **sin app nativa** (el PWA corre sobre HTTPS del tailnet) | Más trabajo de implementación; recuperación si se pierde el dispositivo |
| App nativa con biometría | — | Duplica código y distribución; no aporta sobre passkeys |

**Plan:** TOTP primero (cierra el hueco rápido), passkeys como segundo paso; no se construye capa nativa. Mientras no exista 2FA, el rol `control_total` no se habilita.
