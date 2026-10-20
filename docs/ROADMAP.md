# Plan: qué se construye y en qué orden

**Hasta el 27 de noviembre de 2026:** solo mantenimiento del entorno y de la
arquitectura (esta preparación). **El desarrollo pesado arranca después.**
Cada fase cierra con sus criterios de salida; no se empieza la siguiente sin
cumplirlos. Los huecos de seguridad conocidos están en `SECURITY.md`,
sección 3, y mandan el orden.

## Antes del 27 de noviembre (sin tocar lógica de negocio)

- Crear la cuenta de Tailscale e instalarlo en la laptop y en el celular (ver `DEPLOYMENT.md`).
- Cuando llegue la Mac mini: instalar Docker, Ollama nativo, Tailscale y correr el checklist de migración (`DEPLOYMENT.md`) con el stack actual.
- Cualquier cambio de seguridad nuevo se refleja en `SECURITY.md`.

## Fase 1 — Cerrar la frontera de privacidad y la autenticación (primero)

1. **[Hecho el 19 oct 2026] Outbound gateway** (`privacy/outbound_gateway.py`): la consulta y el contexto se anonimizan juntos, las fuentes viajan con alias, hay auditoría con hash y `claude_node` solo lee el payload del gateway. Prueba de extremo a extremo en `tests/test_graph_privacy_e2e.py`.
2. **[Hecho el 19 oct 2026] Scrubber determinista** (`privacy/scrubber.py`) y bloqueo si quedan hallazgos. Pendiente de este punto: medir falsos positivos con el Ollama real (SECURITY.md, sección 3).
   - **[Hecho el 19 oct 2026] Anonimizador determinista** (`privacy/redactor.py`, `nodes/ollama_node.py`): el código redacta datos estructurados y reemplaza nombres; el modelo local solo los detecta. Laboratorio sintético: 14/14 respondidas, 0 fugas. Pendiente: extraer entidades al ingestar.
   - **[Hecho el 19 oct 2026] Calibración de recuperación y Qdrant v1.19.1.** Un umbral absoluto no separa relevante de irrelevante con nomic-embed-text (lab/calibrar_umbral.py); se añadió corte relativo (RETRIEVAL_SCORE_MARGIN). Resuelto con búsqueda híbrida y juez local (entrada siguiente).
   - **[Hecho el 20 oct 2026] Recuperación híbrida y juez de relevancia local** (`retrieval/`, `nodes/retriever_node.py`). Candidatos por significado + palabras clave (BM25 en Qdrant, fusión RRF, ambas con el filtro de rol); un juez en Ollama decide cuáles responden la pregunta y el umbral queda de respaldo. Medido con `lab/evaluar_recuperacion.py` (31 preguntas): recall 0.81 → 1.00, precisión 0.16 → 0.68, ruido 92 → 10 fuentes, sin evidencia correcta 0/12 → 11/12, ~+1.9 s por consulta. Requiere reindexar (esquema nuevo). Pendiente: endurecer el juez (aprueba fragmentos que solo rozan el tema) y medir con datos reales de Drive.
   - **[Hecho el 20 oct 2026] Primera ingesta real de Drive** (2 hojas de cálculo, 22 fragmentos): una carpeta desconocida ya no se indexa (antes caía en `general`, visible para `consulta`), dominio nuevo `proyectos` (confidencial) y las tablas se parten por filas con el encabezado repetido en cada fragmento. Verificado con datos reales: `consulta` no recibe nada y `lider` solo lo relevante. Pendiente: compartir más carpetas con la cuenta de servicio, escribir `lab/real_golden.json` y medir el juez con preguntas reales; las hojas con varias pestañas solo exportan la primera.
3. **[Hecho el 19 oct 2026, backend] Login y sesiones** (`security/auth.py`, `api/auth_routes.py`): argon2id, sesiones en servidor, freno a la fuerza bruta, TOTP obligatorio para `control_total`, usuarios por CLI (`python -m pipeline_ai.security.manage`). **[Hecho el 19 oct 2026] Pantalla de login en la GUI** (`web/src/app/login`, guardia de sesión en todas las pantallas, TOTP, logout). Pendiente: cambio de contraseña, cablear `POST /api/v1/query` con el usuario de la sesión.
4. **[Hecho el 19 oct 2026] Filtrado por rol en Qdrant** por dominio del fragmento (`security/access.py`, `nodes/retriever_node.py`), con segunda capa en el retriever y pruebas de que cada rol solo ve lo suyo (también con Qdrant, Ollama y Claude reales). Pendiente: `allowed_roles` por documento.
5. Resolver las 3 fallas del eval del router y añadir casos adversariales.

*Salida:* ningún dato crudo ni nombre sensible llega a Claude (prueba automática); todos los endpoints exigen sesión y permiso; `SECURITY.md` sección 3 sin los puntos 1–5.

## Fase 2 — Multi-agente con Drive

- Adaptar `retriever_node` a `DriveConnector` sin reescribir la recuperación.
- Cablear el supervisor en `graph.py` con `Send`; consolidar con `agents/supervisor.py`.
- Que Claude reciba la lista de fuentes no disponibles.
- **[Hecho el 19 oct 2026]** `POST /api/v1/query` conectado al grafo actual (un solo flujo sobre Drive) con el usuario y el rol de la sesión; `/consultar` muestra respuesta y fuentes. Pendiente: reemplazar ese flujo por el supervisor multi-agente.

*Salida:* una consulta sobre Drive pasa por supervisor → sub-agente → gateway → Claude, con citas, y aborta sin evidencia.

## Fase 3 — Firestore/Supabase y GoHighLevel

- Firestore/Supabase primero: ya existen los datos (portafolio aprobado, SourcingBot).
- GoHighLevel con token de solo lectura y **lista blanca de campos**.
- Pruebas de contrato con respuestas simuladas; sin credenciales reales en CI.

*Salida:* consulta que combina Drive + Firestore + CRM; si una fuente cae, las demás responden y la caída se declara.

## Fase 4 — ERP (cuando el proveedor lo entregue)

- Usuario de solo lectura, DocTypes y campos en lista blanca.
- No bloquea ninguna otra fase.

## Fase 5 — GUI real

- Elegir la dirección visual (`docs/GUI_DIRECCION_VISUAL.md`) y reemplazar los tokens provisionales.
- Pantallas: consultar (respuesta con citas y estado por fuente), fuentes, sistema.
- Passkeys (WebAuthn) para `control_total`.

## Fase 6 — Producción en la Mac mini M5 Pro

- Migración con el checklist de `DEPLOYMENT.md`, imágenes fijadas, copia de seguridad y restauración probadas.
- `tailscale serve`, ACLs y aprobación de dispositivos.
- Escaneo de dependencias/imágenes y monitoreo básico.
