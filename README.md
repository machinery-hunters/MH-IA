# MH IA

Sistema híbrido de inteligencia artificial de **Machinery Hunters**: conecta
los sistemas de información de la empresa, recupera y estructura información
localmente vía RAG (Ollama + Qdrant), y delega el razonamiento y la
redacción final a un modelo de razonamiento avanzado (Claude, API de
Anthropic), siempre con citas obligatorias y sin salir de la red de MH con
datos crudos confidenciales.

> Documento de referencia completo: `SRS_MH_IA.docx` (Especificación de
> Requisitos de Software — arquitectura, requisitos funcionales/no
> funcionales, casos de uso, roadmap de conectividad).

## Reglas críticas de diseño

1. **Cero alucinaciones** — si no hay evidencia recuperada suficiente, el
   flujo se aborta y el sistema declara desconocimiento en vez de inventar
   una respuesta.
2. **Privacidad de datos** — las consultas de dominio ventas/inventario/
   proveedores se anonimizan localmente (Ollama) antes de que cualquier
   payload cruce hacia la API de Claude.
3. **Modularidad** — cada sistema de MH se integra como un "conector" con
   una interfaz estándar, sin rediseñar el orquestador.

## Arquitectura (resumen)

```
Conectores (Drive, ERPNext, ...)
        │
        ▼
 Estructuración e indexación local (Ollama embeddings + Qdrant)
        │
        ▼
 Orquestador LangGraph (router → retriever → guardrail → anonymizer)
        │
        ▼
 Razonamiento y redacción (Claude, vía API) — solo contexto ya
 estructurado y anonimizado, citas obligatorias
```

## Empezar

| Quiero... | Ir a... |
|---|---|
| Instalar todo lo necesario en mi máquina | [`INSTALL.md`](./INSTALL.md) |
| Levantar el stack con Docker Compose, acceso remoto y migración | [`DEPLOYMENT.md`](./DEPLOYMENT.md) |
| Entender la arquitectura y sus decisiones | [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) |
| Ver el plan de construcción | [`docs/ROADMAP.md`](./docs/ROADMAP.md) |
| Ver el modelo de seguridad y sus huecos conocidos | [`SECURITY.md`](./SECURITY.md) |
| Sincronizar documentos de Google Drive | [`INGESTION.md`](./INGESTION.md) |
| Entender el diseño completo / requisitos | `SRS_MH_IA.docx` |
| Contribuir código (branches, commits, PRs) | [`CONTRIBUTING.md`](./CONTRIBUTING.md) |

## Comandos rápidos

Windows (PowerShell):

```powershell
.\scripts\dev.ps1 doctor        # valida compose, puertos y secretos sin levantar nada
.\scripts\dev.ps1 up            # levantar en la laptop (Ollama con GPU NVIDIA)
.\scripts\dev.ps1 pull-models   # descargar llama3.1:8b y nomic-embed-text
.\scripts\dev.ps1 ingest        # sincronizar Google Drive -> Qdrant
.\scripts\dev.ps1 test          # pytest
.\scripts\dev.ps1 lint          # ruff + black --check
```

macOS/Linux: `make up-gpu`, `make up-mac`, `make ingest`, `make test`, `make lint`.

## Estructura del repositorio

```
services/pipeline_ai/
├── pipeline_ai/
│   ├── api/             # FastAPI: /health, /api/v1/me, /connectors, /query
│   ├── agents/          # sub-agente (aislamiento) y supervisor (consolidación)
│   ├── connectors/      # Drive, ERP, CRM, Firestore (stubs) + registro
│   ├── privacy/         # sensibilidad, scrubber determinista y outbound gateway
│   ├── security/        # roles y permisos (RBAC)
│   ├── nodes/           # nodos del grafo actual (router, retriever, anonymizer, outbound gateway, claude, guardrail)
│   ├── ingestion/       # Google Drive -> Qdrant (funciona hoy)
│   ├── config.py        # Settings centralizados
│   ├── state.py         # Estado del grafo
│   └── graph.py         # Flujo LangGraph actual
├── tests/               # pytest, sin servicios reales
└── eval/                # calibración del router (requiere Ollama)
web/                      # GUI Next.js + TypeScript + Tailwind (PWA)
docs/                     # ARCHITECTURE, ROADMAP, GUI_DIRECCION_VISUAL
scripts/dev.ps1           # tareas de desarrollo en Windows
docker-compose*.yml       # base + overrides (gpu / mac / dev)
SECURITY.md               # cubierto / aceptado como riesgo / no resuelto
.github/workflows/ci.yml  # lint + tests + build GUI + secretos + Docker amd64/arm64
```

## Estado del proyecto

Ver la tabla de funciones y el roadmap de fases en `SRS_MH_IA.docx`
(secciones 2.3 y 9). En resumen: infraestructura y RAG sobre Google Drive
implementados (Fase 0-1); conector a ERPNext en diseño (Fase 2);
conectividad ampliada y generación de reportes planeada (Fase 3).

## Licencia

Software propietario de Machinery Hunters — uso interno. Ver
[`LICENSE`](./LICENSE).
