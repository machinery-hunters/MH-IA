# SECURITY.md — MH IA

Documento vivo. Cada cambio de arquitectura que toque datos, red, credenciales
o permisos actualiza este archivo en el mismo commit. Tres secciones:
**cubierto** (con la prueba que lo respalda), **aceptado como riesgo**
(decisión consciente, con su motivo) y **explícitamente no resuelto**
(sabemos que falta; no se afirma lo contrario).

Última revisión: 19 de octubre de 2026 (outbound gateway y scrubber determinista, preparación del entorno y protección
local de ramas; primer paso de la Fase 1 del plan; login, sesiones y segundo factor; consulta con filtro por rol).

---

## 1. Cubierto

| Control | Dónde vive | Cómo se verifica |
|---|---|---|
| Secretos fuera de git (`.env`, `credentials/*.json`, `*.pem`, `*.key`) | `.gitignore` | `tests/test_infra_hardening.py`; job `secrets-scan` (gitleaks) en CI; `scripts/dev.ps1 doctor` |
| `.env.example` solo con valores vacíos o placeholders | `.env.example` | `test_env_example_has_no_real_looking_secrets` |
| Fallo explícito si falta un secreto crítico en producción | `Settings.require_production_secrets()`, llamado al arrancar la API | `tests/test_config.py` |
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
| **Outbound gateway: única puerta hacia Claude.** Claude solo recibe el `OutboundPayload` aprobado (consulta y contexto ya anonimizados y verificados); nunca la consulta cruda, el contexto crudo ni los nombres de archivo | `privacy/outbound_gateway.py`, `nodes/outbound_node.py`, `nodes/claude_node.py`, `graph.py` | `tests/test_graph_privacy_e2e.py` (corre el grafo real con modelos falsos y comprueba lo que ve Claude); `tests/test_claude_isolation.py` (solo `claude_node` importa el SDK de Anthropic y la única arista hacia Claude sale del gateway) |
| Nombres de fuente sustituidos por alias (`Fuente_1`…): el mapa vive solo en local y la respuesta se re-etiqueta al volver. Si el nombre real reaparece en el texto se sustituye; si reaparece con otra ortografía, se bloquea | `nodes/retriever_node.py`, `privacy/outbound_gateway.py` | `tests/test_outbound_gateway.py`, `test_confidential_query_reaches_claude_only_anonymized_and_aliased` |
| Scrubber determinista tras el anonimizador: RFC, CURP, correos, teléfonos, CLABE, tarjetas (Luhn), montos, credenciales y nombres conocidos, con normalización contra evasiones simples. **Cualquier hallazgo bloquea el envío** (falla cerrada); el reporte lleva solo tipo y conteo, nunca el valor | `privacy/scrubber.py` | `tests/test_scrubber.py`, `test_a_leak_in_the_anonymizer_output_blocks_the_send` |
| **Anonimizador determinista: el código reemplaza, el modelo solo detecta.** El código redacta RFC, CURP, correos, teléfonos, CLABE, tarjetas, montos y credenciales con los mismos detectores que luego verifica el scrubber; el modelo local (que ya no ve esos datos) solo devuelve una lista JSON de nombres de personas y empresas, validada contra el texto (se descartan alucinaciones); el código los reemplaza por `[PERSONA_n]`/`[EMPRESA_n]` con variantes (sin "S.A. de C.V.", sin apellido). Consulta y contexto comparten marcadores; el mapa marcador→dato real vive solo en local y restaura la respuesta de Claude. Si el modelo falla, excede `ANONYMIZER_TIMEOUT_S`, devuelve JSON inválido o la entrada trae las marcas reservadas, el envío se bloquea y el error no filtra contenido | `nodes/ollama_node.py`, `privacy/redactor.py` | `tests/test_anonymizer_node.py`, `tests/test_redactor.py`, `test_a_malformed_anonymizer_output_blocks_the_send`, `test_a_failing_local_model_blocks_the_send_without_leaking_its_message`, `test_reserved_markers_in_the_query_never_reach_the_local_model` |
| Auditoría de cada envío y de cada bloqueo: hash SHA-256, tamaños, nivel y motivos; nunca contenido | `privacy/outbound_gateway.py` (logger `mhia.audit`) | `test_audit_log_has_hash_and_sizes_but_never_content`, `test_blocked_attempts_are_audited_without_content` |
| La sensibilidad RESTRICTED nunca sale hacia Claude | `privacy/outbound_gateway.py` | `test_restricted_never_leaves_even_if_anonymized` |
| RBAC en servidor con **denegación por defecto**; rol desconocido = sin permisos | `security/roles.py`, `api/deps.py` | `tests/test_security_roles.py`, `tests/test_api.py` |
| **Login con sesiones en servidor.** Token opaco de 256 bits; en la base solo se guarda su HMAC-SHA256 (con `API_SESSION_SECRET`), así que una copia de `auth.db` no sirve para suplantar sesiones. Cookie `HttpOnly`, `SameSite=Strict`, `Path=/` (en producción `__Host-` + `Secure`). Caducan a los 30 min de inactividad y a las 12 h absolutas. El rol se lee de la base en **cada** petición: desactivar a un usuario o cambiarle el rol surte efecto de inmediato. Sin sesión válida, 401 | `security/auth.py`, `security/auth_store.py`, `api/auth_routes.py`, `api/deps.py` | `tests/test_auth_service.py`, `tests/test_auth_store.py`, `tests/test_auth_api.py` |
| Contraseñas con **argon2id**, mínimo 12 y máximo 256 caracteres, lista de contraseñas obvias vetadas, rehash automático al subir los parámetros | `security/passwords.py` | `tests/test_auth_passwords.py` |
| **Sin enumeración de usuarios:** usuario inexistente, contraseña mala, cuenta desactivada y bloqueo responden el mismo mensaje genérico; para usuarios inexistentes se hace una verificación ficticia para igualar el tiempo | `security/auth.py` | `tests/test_auth_service.py`, `tests/test_auth_api.py` |
| **Freno a la fuerza bruta:** 5 fallos bloquean la cuenta 15 min (con retroceso exponencial hasta ×8, los fallos antiguos se olvidan); también para usuarios que no existen (la clave es el hash del nombre normalizado); mientras está bloqueada no se procesa ninguna contraseña; responde 429 con `Retry-After` | `security/auth.py`, `security/auth_store.py` | `tests/test_auth_service.py`, `tests/test_auth_store.py`; verificado con el servicio real |
| **Segundo factor TOTP** (RFC 6238, ±1 ventana) con protección contra reutilización del código (consumo atómico); el secreto se guarda cifrado con Fernet (`API_TOTP_KEY`). `control_total` **siempre** exige código y no puede iniciar sesión sin haberlo enrolado (403 `mfa_not_enrolled`); cualquier usuario con TOTP activo debe usarlo | `security/totp.py`, `security/secretbox.py`, `security/auth.py` | `tests/test_auth_totp.py` (vectores del RFC), `tests/test_auth_service.py`, `tests/test_auth_cli.py`; verificado con el servicio real |
| **Sin registro público.** Los usuarios se crean y administran solo con la CLI `python -m pipeline_ai.security.manage` (contraseñas únicamente por `getpass`, nunca por argumento ni en el historial del shell); el enrolamiento TOTP guarda el secreto solo después de confirmar un código válido | `security/manage.py` | `tests/test_auth_cli.py` |
| **CSRF en dos capas:** cookie `SameSite=Strict` y verificación de `Origin`/`Sec-Fetch-Site` en todo método no seguro; respuestas de `/api/` con `Cache-Control: no-store` | `api/origin.py`, `api/main.py` | `tests/test_auth_api.py` |
| La base de usuarios **falla cerrada**: si `auth.db` no está disponible la API responde 503, nunca deja pasar. Producción exige además `API_TOTP_KEY` (Fernet válida) y cookies `Secure`; `auth.db` se crea con permisos 0600 y no se versiona | `api/deps.py`, `config.py`, `.gitignore` | `tests/test_auth_api.py`, `tests/test_config.py` |
| **GUI con login y guardia de sesión.** Toda pantalla (salvo `/login`) exige sesión: sin ella no se monta el contenido ni se llama a la API y se redirige a `/login`. El token nunca es visible para JavaScript (cookie `HttpOnly`); la contraseña se borra de memoria al terminar. El destino tras entrar (`?next=`) solo admite rutas internas (sin open redirect, sin `/login` ni `/api`). Mensajes de error genéricos (no distinguen usuario de contraseña), paso de código TOTP guiado por la API y aviso con el tiempo de espera en los bloqueos 429. Un 401 posterior (sesión caducada o revocada) devuelve a `/login`. La navegación se oculta según permisos (UX; la API valida siempre) | `web/src/components/` (`SessionProvider`, `RequireSession`, `LoginForm`, `AccountBar`), `web/src/lib/auth-helpers.ts` | `web/src/lib/auth-helpers.test.ts` (`npm test`, en CI); verificado en un navegador real contra la API real: guardia, error genérico, `next`, TOTP, logout y sesión revocada |
| **RBAC sobre el contenido (filtro por rol en el retriever).** La sensibilidad de cada fragmento se deriva del `domain` de su carpeta; cada rol solo recupera los dominios que puede ver (`consulta`: general; `lider`, `configuracion` y `control_total`: todos). Se aplica como condición de Qdrant y se verifica de nuevo sobre lo recuperado (si el filtro fallara, se descarta y se registra). Rol ausente o inválido: no se consulta la base. Un fragmento sin `domain` o con un dominio desconocido no lo ve nadie. Lo que el rol no puede ver es indistinguible de lo que no existe (misma respuesta de "sin información", sin fuentes ni nombres) | `security/access.py`, `nodes/retriever_node.py` | `tests/test_access.py`, `tests/test_retriever_node.py`, `tests/test_graph_privacy_e2e.py` (el grafo real con un Qdrant que ignora el filtro); verificado con Qdrant, Ollama y Claude reales: `consulta` nunca recibió documentos de Ventas ni de Administración y `lider` sí |
| **`POST /api/v1/query` con la identidad de la sesión.** El rol y el usuario salen de la sesión, nunca del cuerpo (un `role` enviado por el cliente se ignora); consulta validada y recortada en servidor; tope de duración (`QUERY_TIMEOUT_S`, 504) y de consultas simultáneas (`QUERY_MAX_CONCURRENCY`); errores internos con mensaje genérico (502) sin contenido de la consulta; un bloqueo del gateway o la falta de evidencia no revelan fuentes ni el motivo detallado. Auditoría `mhia.query`: usuario, rol, sesión, huella SHA-256 y longitud de la consulta, resultado y número de fuentes; nunca el texto de la consulta ni de la respuesta. La GUI muestra la respuesta como texto plano (nunca HTML) | `api/main.py`, `api/query_service.py`, `web/src/app/consultar/page.tsx` | `tests/test_query_api.py`, `tests/test_api.py`, `web/src/lib/query-helpers.test.ts`; verificado en un navegador real |
| Auditoría de autenticación (logger `mhia.auth`): login correcto, fallos, bloqueos y logout; nunca contraseñas, códigos ni tokens, y el nombre de usuario de un intento fallido va hasheado | `security/auth.py` | `tests/test_auth_service.py` |
| Respuesta de conectores sin valores de credenciales | `api/main.py` | `test_connectors_endpoint_lists_status_without_secrets` |
| Validación de entrada en servidor (longitud de la consulta) | `api/main.py` (`QueryRequest`) | `test_query_validates_input_server_side` |
| Lista blanca de cabecera `Host` | `TrustedHostMiddleware` | `test_untrusted_host_header_is_rejected` |
| GUI: CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, sin `X-Powered-By`; misma-origen hacia la API (sin CORS) | `web/next.config.ts` | Verificado levantando la GUI real y leyendo las cabeceras |
| Hook local `pre-push` bloquea el push directo a `main` y `develop` (mitigación contra accidentes; ver sección 2: no es un control) | `.githooks/pre-push`, `scripts/dev.ps1 hooks` | `tests/test_infra_hardening.py` (casos del hook) |
| PWA sin caché de datos: el service worker no intercepta `fetch` | `web/public/sw.js` | Revisión de código |

## 2. Aceptado como riesgo

| Riesgo | Por qué se acepta | Revisar cuando |
|---|---|---|
| Qdrant sin API key | No publica puerto; solo `mhia-api` está en su red | Si algún otro servicio entra a la red `backend` o se publica un puerto |
| Qdrant `v1.19.1` (alineado con `qdrant-client` 1.19.x) y `ollama:latest` sin versión pineada | Qdrant se subió de v1.11.0 sin datos de producción; un volumen creado con la versión anterior puede no abrir y hay que reindexar. Ollama se fijará al migrar a la Mac mini | Antes de producción; al subir Qdrant o el cliente, subir ambos |
| Qdrant y Ollama pueden salir a internet | Ollama lo necesita para `ollama pull`. Qdrant comparte la red | Si se descarga modelos por otra vía y se aísla `backend` con `internal: true` |
| CSP con `'unsafe-inline'` en `script-src` | Next.js inyecta scripts de arranque en línea | Al implementar CSP con nonce |
| Volumen de Qdrant y `.env` sin cifrado propio | Dependen del cifrado de disco del equipo (BitLocker en la laptop, FileVault en la Mac mini) | Activarlo y confirmarlo como parte de `deploy-checklist` |
| Tailscale (servicio de terceros) coordina la red privada | Los datos viajan cifrados extremo a extremo (WireGuard); el servicio coordina claves y dispositivos, no ve el tráfico | Si Dirección exige infraestructura 100 % propia (alternativa: Headscale) |
| **GitHub no aplica la protección de ramas** en este repositorio privado (plan actual de la organización); la regla de `main` existe pero figura "Not enforced" | La única mitigación es el hook local `pre-push` (se puede saltar con `--no-verify`, solo existe en clones donde se active, y no impide un push hecho desde otra herramienta) y la disciplina del flujo de `CONTRIBUTING.md`. El CI corre en cada PR, pero GitHub no bloquea el merge si falla | Si la organización pasa a GitHub Team/Enterprise: la regla ya creada se activa sola, y el hook pasa a ser respaldo. No hacer público el repositorio para obtenerla |
| El repositorio tiene un solo desarrollador | Sin revisión de pares; por eso la regla de `main` no exige aprobaciones (no podría aprobar su propio PR) | Cuando haya más colaboradores: exigir 1 aprobación y revisar `CODEOWNERS` |
| Nombres y reparto de roles son provisionales | Hay que validarlos con Dirección | Antes de crear los usuarios reales |
| Ollama `llama3.1:8b` como anonimizador y clasificador | Es el modelo que usamos hoy; ya solo detecta nombres (no reescribe texto) y se compensa con piso por conector y con el scrubber determinista (sección 1) | Con cada cambio de modelo, correr el eval |
| El scrubber es una red de seguridad, no una prueba de limpieza | No detecta cantidades escritas en palabras ("un millón doscientos mil"), homoglifos (letras cirílicas que imitan latinas) ni nombres de personas o empresas fuera de la lista de fuentes conocidas; para eso depende del modelo local. Es conservador: puede **bloquear de más** (p. ej. un número de 7 o más dígitos que sea un folio legítimo) y el usuario recibe un mensaje genérico | En el primer eval con consultas reales; al llegar cada conector, pasar a `known_names` los nombres de sus clientes y proveedores |
| El hash de auditoría es SHA-256 simple, no HMAC | Sirve para correlacionar y probar integridad; no oculta consultas muy cortas frente a quien ya tenga el log | Al implementar el almacén de auditoría (HMAC con clave dedicada) |
| Las respuestas de Claude no pasan por el scrubber | Solo se re-etiquetan alias en local y se muestran a un usuario autenticado; no vuelven a salir de la red | Si la respuesta se reenvía a otro sistema o canal |
| El candado del constructor de `OutboundPayload` evita errores de código, no a quien tenga acceso al proceso | Es defensa contra accidentes; el control real es que el payload solo sale de `prepare_outbound` y que las pruebas fijan que solo `claude_node` importa el SDK | Si se añade otro nodo que hable con un modelo externo |
| **El freno a la fuerza bruta es por usuario, no por IP** | La API queda detrás del proxy de Next.js, así que la IP del cliente no es confiable. Consecuencia: alguien con acceso al tailnet puede bloquear temporalmente (15 min) la cuenta de otro; se acepta frente a permitir adivinar contraseñas | Si se expone fuera del tailnet o se configura un proxy con cabeceras `X-Forwarded-For` confiables (añadir límite por IP) |
| **TOTP es phishable** y su secreto vive en el servidor | Está cifrado con `API_TOTP_KEY`, pero quien tenga a la vez `.env` y `auth.db` podría generar códigos. SHA-1 es lo que soportan todas las apps autenticadoras | Fase 5: passkeys (WebAuthn) para `control_total` |
| `auth.db` no tiene cifrado propio (salvo los secretos TOTP) | Contiene hashes argon2id y HMACs de sesión, no datos de negocio; depende del cifrado de disco del equipo | Junto con el cifrado de disco de `deploy-checklist` |
| Administración de usuarios solo por CLI; **sin cambio de contraseña ni recuperación de TOTP desde la API** | Con pocos usuarios y un solo administrador evita una superficie de ataque más; un reinicio se hace con `reset-password` / `enroll-totp` | Cuando haya pantalla de administración o más de ~10 usuarios |
| **La sensibilidad de un documento se deduce de la carpeta de Drive**, no de una etiqueta propia | Un archivo confidencial guardado por error en una carpeta General sería visible para `consulta` (el anonimizador, el scrubber y el gateway siguen aplicando); la ingesta registra la carpeta y el dominio de cada archivo | Al implementar `allowed_roles` por documento; revisar `ingestion/domain_mapping.py` con cada carpeta nueva |

## 3. Explícitamente no resuelto

Cosas que sabemos que faltan. Hasta que cada una se cierre, **la API exige
sesión válida y permiso** (sin sesión, `get_current_principal` devuelve 401) y el pipeline **solo se expone
por `POST /api/v1/query`**, que exige sesión y permiso y aplica el filtro por rol (sección 1).

1. **Autenticación: pendientes tras el login.** El login con sesiones, argon2id, freno a la fuerza bruta y TOTP ya están implementados (sección 1). Faltan: cambio de contraseña desde la API, passkeys (WebAuthn) y un almacén de auditoría de autenticación (hoy solo el logger `mhia.auth`).
2. **Logs locales con nombres de fuentes.** La consulta ya no se escribe en los logs (solo su longitud y una huella; `retriever_node`, `api/query_service.py`). Sigue apareciendo en el log INFO el nombre de los documentos recuperados (solo los que el rol podía ver), útil para calibrar. No sale de la red, pero quien lea los logs sabría qué documentos se consultaron. Pendiente: registrar identificadores de fragmento en vez de nombres.
3. **Nombres no detectados por el modelo local.** Un nombre de persona o empresa no tiene forma reconocible, así que si llama3.1:8b no lo detecta, el scrubber no lo atrapa y viaja a Claude. Un documento malicioso también podría intentar que el extractor devuelva listas vacías (el prompt lo prohíbe y trata el texto como datos, pero no es una garantía). Medido en el laboratorio sintético (19 oct 2026, 14 preguntas con datos inventados): 0 fugas de canarios, 14/14 respondidas, sin cuelgues. Mitigación pendiente: extraer entidades al ingestar y guardarlas como metadatos / lista de contrapartes conocidas, que el scrubber verifique siempre. Además, el redactor puede sobre-redactar (p. ej. "6,800 horas" como monto); es seguro, solo resta precisión.
4. **Filtrado por rol solo por dominio.** Cada fragmento se filtra por el dominio de su carpeta (sección 1). Falta un `allowed_roles` por documento para excepciones (p. ej. un archivo de Ventas visible para `consulta`, o uno de General restringido), y los fragmentos indexados sin `domain` quedan invisibles para todos hasta reindexar. Cuando lleguen ERP/CRM/Firestore, cada conector aplicará el mismo filtro con su sensibilidad por defecto. Una carpeta de Drive cuyo nombre no coincide con ningún dominio (`ingestion/domain_mapping.py`) ya NO cae en `general` (visible para `consulta`): sus archivos no se indexan y la ingesta lo avisa en el log hasta que se la mapee. `Proyectos` es un dominio confidencial propio (líder y superiores), por decisión del negocio del 20 oct 2026.
5. **Auditoría incompleta.** Cada consulta deja una línea `mhia.query` (usuario, rol, sesión, huella y longitud de la consulta, resultado, número de fuentes) y cada envío a Claude otra `mhia.audit` (hash, nivel, motivos), enlazadas por `session`. Falta un almacén propio, protección contra alteración del registro y auditar qué fuente concreta vio cada usuario.
6. **Sin rate limiting general** en la API. El login frena la fuerza bruta (por usuario) y la consulta limita las simultáneas (`QUERY_MAX_CONCURRENCY`) y su duración (`QUERY_TIMEOUT_S`), pero un usuario autenticado puede ocupar todos los turnos. Ver sección 2.
7. **Inyección de instrucciones vía documentos.** Un documento de Drive o un registro del CRM puede contener texto que intente instruir al modelo. Mitigación prevista: el nodo Claude no tiene herramientas ni acciones (solo produce texto), el contexto va delimitado y la respuesta exige citas; falta una prueba adversarial.
8. **Eval del router al 89 %** con 3 fallas conocidas sin resolver; ahora, además, el piso por conector cubre ERP/CRM/Firestore, pero Drive sigue dependiendo del router y del mapeo de carpetas.
9. **Listas blancas de campos** de ERP, CRM y Firestore por construir (llegan con cada conector).
10. **Relevancia: el juez local puede equivocarse.** La recuperación es híbrida (significado + palabras clave, fusión RRF, ambas consultas con el filtro de rol) y un juez local en Ollama decide qué fragmentos responden la pregunta; el umbral de coseno solo queda como respaldo si el juez falla. Medido con `lab/evaluar_recuperacion.py` y 31 preguntas (`lab/retrieval_golden.json`) contra el criterio anterior: recall 0.81 → 1.00, precisión 0.16 → 0.68, fuentes irrelevantes que viajan a Claude 92 → 10 y preguntas sin documento que terminan correctamente sin evidencia 0/12 → 11/12, a cambio de ~1.9 s más por consulta. Persiste: el juez es tolerante (a veces aprueba un fragmento que solo roza el tema) y un fragmento con texto inyectado podría torcer su veredicto; el peor caso es aprobar o descartar contenido que el rol YA puede ver, que de todos modos pasa por el anonimizador y la pasarela de salida. Si Ollama falla se vuelve al umbral (más ruido, nunca menos evidencia ni más acceso). Las colecciones creadas antes de este cambio siguen respondiendo solo por significado hasta reindexar. Las cifras salen de un laboratorio de 8 documentos sintéticos: hay que volver a medir con datos reales. **Backups y restauración** de Qdrant y del manifest, **rotación de secretos**, **escaneo de dependencias e imágenes** (Dependabot/Trivy): sin implementar.
11. **Sin CSP con nonce.** La protección CSRF ya existe (cookie `SameSite=Strict` más verificación de `Origin`/`Sec-Fetch-Site`; sección 1).

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

**Plan:** TOTP primero (cierra el hueco rápido), passkeys como segundo paso; no se construye capa nativa. **Estado:** TOTP implementado. `control_total` exige código en cada login y no puede iniciar sesión sin haberlo enrolado (`mfa_not_enrolled`). Passkeys siguen pendientes (Fase 5).
