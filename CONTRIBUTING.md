# Contribuir a MH IA

## Flujo de ramas

- `main` — siempre desplegable. Solo se llega por PR.
- `develop` — integración de trabajo en curso.
- Ramas de trabajo: `feature/<descripción-corta>`, `fix/<descripción-corta>`,
  `docs/<descripción-corta>`.

```bash
git checkout develop
git pull
git checkout -b feature/conector-erpnext
# ... trabajo ...
git push -u origin feature/conector-erpnext
# abrir PR hacia develop
```

## Commits

Formato recomendado (Conventional Commits, en español el resumen):

```
tipo(alcance): resumen breve en presente

feat(ingestion): agrega soporte para exportar Google Sheets a CSV
fix(router): corrige clasificación por defecto ante error de parseo JSON
docs(srs): actualiza roadmap de la Fase 3
test(guardrail): agrega prueba de no-fabricación en abort_node
chore(ci): agrega workflow de lint y tests
```

Tipos usados: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`.

## Antes de abrir un PR

```bash
make lint     # ruff + black --check
make test     # pytest
```

El workflow de CI (`.github/workflows/ci.yml`) corre lo mismo automáticamente
en cada push/PR — un PR con CI en rojo no se debe mergear.

## Reglas de diseño que todo cambio debe respetar

Antes de tocar código en `services/pipeline_ai/pipeline_ai/nodes/` o `services/pipeline_ai/pipeline_ai/graph.py`, revisar la sección 10
del SRS (`SRS_MH_IA.docx`) — matriz de trazabilidad de las 3 reglas críticas:

1. Ningún dato crudo de dominios confidenciales (ventas/inventario/
   proveedores) debe poder llegar al nodo de Claude sin pasar por
   anonimización local.
2. El guardrail anti-alucinación (`retrieval_ok=False → abort`) no debe
   poder saltarse.
3. Todo conector nuevo debe implementar la interfaz estándar (list/sync,
   fetch/query, to_document) descrita en la sección 3.1.1 del SRS, en vez de
   integrarse ad-hoc dentro del orquestador.

## Agregar un conector nuevo

1. Crear `services/pipeline_ai/pipeline_ai/connectors/<sistema>.py` con una
   clase que cumpla el contrato de `connectors/base.py`: un `ConnectorSpec`
   (nombre, `default_sensitivity`, `required_settings`, `timeout_setting`) y
   `async def extract(query) -> RawExtraction`. El conector **solo extrae
   crudo**: no anonimiza ni decide permisos (eso lo hace `agents/subagent.py`).
2. Si necesita un cliente HTTP o SDK, instanciarlo de forma perezosa
   (`@lru_cache` + `settings.require(...)` dentro de la función, nunca al
   importar).
3. Declarar sus variables en `config.py` y en `.env.example` (valores vacíos).
4. Registrarlo en `connectors/registry.py`. **No se toca el prompt del
   router**: la sensibilidad la declara el propio conector.
5. Normalizar las citas al formato `Citation` (`source`, `chunk_id`, `score`).
6. Agregar pruebas que no dependan de credenciales ni servicios reales
   (respuestas simuladas); `tests/test_connectors_registry.py` ya valida que
   el spec apunte a variables de configuración reales.
7. Documentar el setup de credenciales en `INSTALL.md` y, si cambia la
   superficie de ataque, actualizar `SECURITY.md` en el mismo commit.
