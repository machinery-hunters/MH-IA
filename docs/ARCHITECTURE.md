# Arquitectura de MH IA

Registro de decisiones (ADR) de la evolución de `mh-ia`: de "RAG sobre Drive"
a la plataforma de IA híbrida de Machinery Hunters. Estado: **esqueleto
listo; la lógica de negocio arranca después del 27 de noviembre de 2026.**

## Vista general

```
 Personas (roles) ──► GUI Next.js (PWA) ──► API FastAPI ──► Supervisor (LangGraph)
   dentro del tailnet     web/                 RBAC          │  fan-out en paralelo
                                                             ▼
        ┌────────────┬────────────┬────────────┬────────────┐
        │ sub-agente │ sub-agente │ sub-agente │ sub-agente │   cada uno: timeout propio,
        │   Drive    │    ERP     │    CRM     │ Firestore  │   extrae + anonimiza lo suyo
        └─────┬──────┴─────┬──────┴─────┬──────┴─────┬──────┘
              ▼            ▼            ▼            ▼
          Qdrant       Frappe/      GoHighLevel   Firestore /
        (+Ollama)      ERPNext                    Supabase
              │            │            │            │
              └──── SourceResult (contexto YA anonimizado + estado) ────┘
                                   │
                                   ▼
                      Outbound gateway (único punto de salida)
                                   │
                                   ▼
                       Claude (API) — solo razona y cita
```

Capa local (Ollama): clasifica, extrae, anonimiza. Capa de razonamiento
(Claude): solo recibe contexto estructurado y anonimizado.

## Estructura del repositorio

```
services/pipeline_ai/
  pipeline_ai/
    api/          FastAPI: /health, /api/v1/me, /connectors, /query (501)
    agents/       subagent.py (aislamiento), supervisor.py (consolidación)
    connectors/   base.py + drive, erp, crm, firestore (stubs) + registry
    privacy/      sensitivity.py, scrubber.py (stub), outbound_gateway.py (stub)
    security/     roles.py (RBAC)
    nodes/        router, retriever, anonymizer, claude, guardrail (flujo actual)
    ingestion/    Drive -> Qdrant (funciona hoy)
    graph.py      flujo lineal actual (sigue funcionando)
  tests/ eval/
web/              Next.js + TypeScript + Tailwind (PWA)
docs/             ARCHITECTURE, ROADMAP, GUI_DIRECCION_VISUAL
SECURITY.md       cubierto / aceptado / no resuelto
```

## ADR-001 — Monorepo `mh-ia` con servicios separados

**Decisión:** un repo; `services/pipeline_ai` y `web` con imágenes y
dependencias separadas. **Por qué:** un compose y un CI, un contrato de API
compartido, una sola persona mantiene todo. **Costo:** commits que tocan
ambos lados. **Garantía de aislamiento:** la GUI no importa el pipeline; solo
habla con la API, y su contenedor no recibe `.env` ni alcanza Qdrant/Ollama.

## ADR-002 — Qdrant: una colección para documentos; los registros no se embeben

**Decisión:** `mhia_drive_docs` se queda como está (sin reindexar). Los
registros de ERP, CRM y Firestore **se consultan en vivo** desde su sub-agente
y no se copian a Qdrant. Si más adelante hace falta búsqueda semántica sobre
registros, se crea una segunda colección `mhia_records` con payload
`source_system`. **Por qué:** los registros estructurados cambian y son más
sensibles; copiarlos crea una segunda copia que sincronizar y proteger. Los
documentos sí necesitan vectores. Una colección con payload por dominio
(índices `domain`, `sensitivity`, `allowed_roles`) sigue la recomendación de
Qdrant para tenants homogéneos y poco numerosos. **Advertencia:** el filtro de
payload es aislamiento de aplicación, no un límite de seguridad por sí solo.
**Pendiente:** dimensionamiento con conteos reales (`qdrant-sizing`).

## ADR-003 — Sensibilidad declarada por conector

**Decisión:** cada conector declara su `ConnectorSpec` con
`default_sensitivity`. La sensibilidad efectiva es el **máximo** entre el
default del conector, el dominio del documento y la clasificación del router;
nada la baja. Sin señales: CONFIDENTIAL. **Por qué:** agregar un conector no
obliga a tocar el prompt del router, y un error del clasificador no puede
bajar el piso de ERP/CRM. ERP/CRM/Firestore nacen CONFIDENTIAL; Drive INTERNAL
y sube por documento.

## ADR-004 — Multi-agente: supervisor + un sub-agente por conector

- Fan-out en paralelo (LangGraph `Send`); cada sub-agente tiene su timeout.
- El supervisor solo ve `SourceResult` (contexto **ya anonimizado** + estado).
- **Política de falla:** si una fuente falla, el supervisor **continúa con las
  demás** y la marca como "fuente no disponible". Claude recibe la lista de
  fuentes caídas para no afirmar cobertura completa. Solo se aborta (sin
  llamar a Claude) si **ninguna** fuente produjo evidencia.

| Fuente | Timeout | Sin credenciales | Rol mínimo (por sensibilidad) |
|---|---|---|---|
| Drive | 15 s | `not_configured` | `consulta` (documentos confidenciales: `lider`) |
| ERP | 10 s | `not_configured` (hoy) | `lider` |
| CRM | 10 s | `not_configured` | `lider` |
| Firestore | 10 s | `not_configured` | `lider` |
| Global | 30 s | — | — |

Estados: `ok`, `timeout`, `unavailable`, `not_configured`, `forbidden`. Los
timeouts son configurables por variable de entorno.
**Estado:** `subagent.py` y `supervisor.py` existen y están probados;
`graph.py` conserva el flujo lineal hasta cablear el supervisor.

## ADR-005 — Autenticación y autorización viven en la API

La API es la fuente de verdad (sesión por cookie `httpOnly`, argon2, roles →
permisos, segundo factor para `control_total`). Next.js no es frontera de
seguridad. La GUI obtiene permisos de `GET /api/v1/me`.

## ADR-006 — Red: Tailscale en el host + `tailscale serve`

Nada expuesto a internet. Compose publica solo en `127.0.0.1`; `tailscale
serve` lo hace accesible dentro del tailnet. Qdrant y Ollama no publican
puertos. Detalle y trade-offs en `SECURITY.md`, sección 4.

## ADR-007 — PWA, no apps nativas

Manifest + service worker mínimo (instalable, sin caché de datos). Passkeys
(WebAuthn) cubren la biometría para `control_total` sin capa nativa.

## ADR-008 — Portabilidad laptop → Mac mini M5 Pro

Ollama containerizado con GPU NVIDIA en la laptop; nativo (Metal) en la Mac.
La app solo lee `OLLAMA_BASE_URL`; nada depende de CUDA. Las imágenes propias
se compilan para `linux/amd64` y `linux/arm64` en CI. Los embeddings deben
usar el mismo modelo (`nomic-embed-text`) en ambas máquinas para no reindexar.

## Qué se revisitaría al crecer

- Colección `mhia_records` y su dimensionamiento si se necesita búsqueda semántica sobre registros.
- Cola de trabajos si las consultas multi-fuente se vuelven largas.
- Headscale si Dirección exige infraestructura de red 100 % propia.
