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

Antes de tocar código en `app/nodes/` o `app/graph.py`, revisar la sección 10
del SRS (`SRS_MH_IA.docx`) — matriz de trazabilidad de las 3 reglas críticas:

1. Ningún dato crudo de dominios confidenciales (ventas/inventario/
   proveedores) debe poder llegar al nodo de Claude sin pasar por
   anonimización local.
2. El guardrail anti-alucinación (`retrieval_ok=False → abort`) no debe
   poder saltarse.
3. Todo conector nuevo debe implementar la interfaz estándar (list/sync,
   fetch/query, to_document) descrita en la sección 3.1.1 del SRS, en vez de
   integrarse ad-hoc dentro del orquestador.

## Agregar un conector nuevo (Fase 2/3)

1. Crear `app/ingestion/<sistema>_client.py` (o `app/connectors/<sistema>.py`
   si es consulta en tiempo real vía function calling) con las tres
   operaciones estándar.
2. Normalizar la salida a `langchain_core.documents.Document` con
   `metadata={"source": ..., "chunk_id"/"record_id": ...}` — el mismo
   contrato que ya usa `retriever_node.py` para citar fuentes.
3. Agregar tests en `tests/` que no dependan de credenciales reales (usar
   mocks/fixtures para la llamada a la API externa).
4. Documentar el setup de credenciales en `INSTALL.md`.
