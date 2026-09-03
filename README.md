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
| Levantar el stack con Docker Compose | [`DEPLOYMENT.md`](./DEPLOYMENT.md) |
| Sincronizar documentos de Google Drive | [`INGESTION.md`](./INGESTION.md) |
| Entender el diseño completo / requisitos | `SRS_MH_IA.docx` |
| Contribuir código (branches, commits, PRs) | [`CONTRIBUTING.md`](./CONTRIBUTING.md) |

## Comandos rápidos

```bash
make up-gpu     # levantar en la laptop de desarrollo (Ollama con GPU NVIDIA)
make up-mac     # levantar en el Mac Mini de producción (Ollama nativo)
make ingest     # sincronizar Google Drive -> Qdrant
make test       # correr la suite de pytest
make lint       # ruff + black --check
```

## Estructura del repositorio

```
app/
├── config.py             # Settings centralizados (variables de entorno)
├── state.py               # Estado del grafo (MHIAState)
├── graph.py                 # Ensamblado del grafo LangGraph
├── main.py                    # CLI de prueba conversacional
├── nodes/                      # Nodos del grafo (router, retriever, anonymizer, claude, guardrail)
└── ingestion/                   # Conector Google Drive -> Qdrant (Fase 1)
tests/                    # Suite de pytest (estructura del grafo, guardrails, config)
docker-compose*.yml       # Base + overrides por entorno (gpu / mac)
.github/workflows/ci.yml  # Lint + tests en cada push/PR
```

## Estado del proyecto

Ver la tabla de funciones y el roadmap de fases en `SRS_MH_IA.docx`
(secciones 2.3 y 9). En resumen: infraestructura y RAG sobre Google Drive
implementados (Fase 0-1); conector a ERPNext en diseño (Fase 2);
conectividad ampliada y generación de reportes planeada (Fase 3).

## Licencia

Software propietario de Machinery Hunters — uso interno. Ver
[`LICENSE`](./LICENSE).
