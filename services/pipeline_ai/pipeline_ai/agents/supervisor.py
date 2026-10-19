"""
Supervisor multi-agente (STUB de la política de consolidación).

Diseño (ver docs/ARCHITECTURE.md, ADR-004):
  1. Decide qué conectores consultar a partir del dominio que clasificó el
     router (local). Hoy: todos los configurados.
  2. Lanza un sub-agente por conector EN PARALELO (LangGraph `Send`), cada
     uno con su timeout, bajo un tope global (settings.timeout_global_s).
  3. Consolida SOLO SourceResult ya anonimizados. Nunca ve dato crudo.
  4. Política de falla: continúa con las fuentes que respondieron y las marca
     "fuente no disponible". Aborta (nodo abort, sin Claude) únicamente si
     NINGUNA fuente produjo evidencia — regla de cero alucinaciones.

El cableado al grafo (graph.py) se hace después del 27 de noviembre. Mientras
tanto graph.py conserva el flujo lineal actual, que sigue funcionando.
"""

from __future__ import annotations

from pipeline_ai.sources import SourceResult


def consolidate(results: list[SourceResult]) -> tuple[list[SourceResult], list[SourceResult]]:
    """Separa (fuentes con evidencia, fuentes no disponibles). Función pura."""
    ok = [r for r in results if r.ok and r.context.strip()]
    unavailable = [r for r in results if not (r.ok and r.context.strip())]
    return ok, unavailable


def should_abort(results: list[SourceResult]) -> bool:
    """Cero alucinaciones: sin ninguna fuente con evidencia, no se llama a Claude."""
    ok, _ = consolidate(results)
    return not ok
