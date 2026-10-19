"""
Sub-agente: envuelve UN conector con aislamiento de fallas.

Garantías (cubiertas por tests/test_subagent_isolation.py):
  - Nunca lanza excepción hacia el supervisor: siempre devuelve SourceResult.
  - Sin credenciales -> NOT_CONFIGURED (no intenta conectarse).
  - Rol sin permiso para la sensibilidad de la fuente -> FORBIDDEN (no consulta).
  - Excede su timeout -> TIMEOUT (no bloquea a los demás).
  - Falla al consultar O al anonimizar -> UNAVAILABLE. Falla CERRADA: si no
    se pudo anonimizar un dato que lo requiere, no se devuelve ni crudo.
  - `detail` solo lleva el nombre de la clase de la excepción: el mensaje
    puede contener fragmentos de registros del sistema fuente.
  - Si el nivel efectivo exige anonimizar, SOLO el texto anonimizado llega
    al SourceResult; el crudo muere dentro de esta función.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from pipeline_ai.config import settings
from pipeline_ai.connectors.base import Connector
from pipeline_ai.privacy.sensitivity import effective_sensitivity, requires_anonymization
from pipeline_ai.security.roles import can_view
from pipeline_ai.sources import SourceResult, SourceStatus

log = logging.getLogger("mhia.subagent")

# anonymize(texto_crudo) -> texto anonimizado. Se inyecta (en producción: el
# anonimizador local de Ollama + privacy.scrubber) para poder testear sin servicios.
Anonymizer = Callable[[str], Awaitable[str]]


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


async def run_subagent(
    connector: Connector,
    query: str,
    *,
    role: str | None,
    anonymize: Anonymizer,
) -> SourceResult:
    spec = connector.spec
    start = time.monotonic()
    level = spec.default_sensitivity

    missing = [f for f in spec.required_settings if not getattr(settings, f, "")]
    if missing:
        return SourceResult(
            source=spec.name,
            status=SourceStatus.NOT_CONFIGURED,
            sensitivity=int(level),
            detail="faltan credenciales",
        )

    if not can_view(role, level):
        return SourceResult(
            source=spec.name,
            status=SourceStatus.FORBIDDEN,
            sensitivity=int(level),
            detail="rol sin permiso para esta fuente",
        )

    timeout = float(getattr(settings, spec.timeout_setting, settings.timeout_global_s))

    try:
        extraction = await asyncio.wait_for(connector.extract(query), timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):  # noqa: UP041 - alias en 3.11+, distintos en 3.10
        return SourceResult(
            source=spec.name,
            status=SourceStatus.TIMEOUT,
            sensitivity=int(level),
            detail=f"excedió {timeout:g}s",
            elapsed_ms=_elapsed_ms(start),
        )
    except Exception as exc:  # noqa: BLE001 - frontera de aislamiento: nada escapa
        log.warning("Sub-agente %s falló: %s", spec.name, type(exc).__name__)
        return SourceResult(
            source=spec.name,
            status=SourceStatus.UNAVAILABLE,
            sensitivity=int(level),
            detail=type(exc).__name__,
            elapsed_ms=_elapsed_ms(start),
        )

    # La sensibilidad solo puede subir (default del conector vs. la del dato).
    if extraction.sensitivity is not None:
        level = effective_sensitivity(level, extraction.sensitivity)

    # Un rol que puede ver la fuente por defecto pero no este dato en
    # particular (p.ej. documento RESTRICTED) tampoco lo recibe.
    if not can_view(role, level):
        return SourceResult(
            source=spec.name,
            status=SourceStatus.FORBIDDEN,
            sensitivity=int(level),
            detail="rol sin permiso para este dato",
            elapsed_ms=_elapsed_ms(start),
        )

    text = extraction.text
    if requires_anonymization(level):
        try:
            text = await asyncio.wait_for(anonymize(extraction.text), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - falla cerrada: jamás devolver el crudo
            log.warning("Anonimización en %s falló: %s", spec.name, type(exc).__name__)
            return SourceResult(
                source=spec.name,
                status=SourceStatus.UNAVAILABLE,
                sensitivity=int(level),
                detail=f"anonimización falló ({type(exc).__name__})",
                elapsed_ms=_elapsed_ms(start),
            )

    return SourceResult(
        source=spec.name,
        status=SourceStatus.OK,
        context=text,
        citations=extraction.citations,
        sensitivity=int(level),
        elapsed_ms=_elapsed_ms(start),
    )
