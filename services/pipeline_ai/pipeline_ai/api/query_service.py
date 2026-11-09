"""
Servicio de consulta: ejecuta el grafo de MH IA en nombre de un usuario autenticado.

Responsabilidades de esta capa (el grafo no sabe de HTTP ni de sesiones):
  - El rol, el usuario y sus membresías por área salen del `Principal` que resolvió la
    sesión; el cliente no puede elegirlos. Las membresías viajan en `state["memberships"]` y
    el retriever las usa para filtrar el contenido.
  - Tope de tiempo total y de consultas simultáneas (Ollama es un solo equipo).
  - Auditoría `mhia.query`: quién (user_id, rol), cuándo, huella SHA-256 y longitud de la
    consulta, resultado y número de fuentes. NUNCA el texto de la consulta ni de la respuesta.
    El `session_id` (uno por consulta) enlaza esta línea con las del gateway (`mhia.audit`).
  - Los errores internos no se propagan con su mensaje: pueden contener datos de la consulta.
    Se registra solo el tipo de excepción.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Literal

from pipeline_ai.config import settings
from pipeline_ai.graph import mhia_graph
from pipeline_ai.security.areas import to_state
from pipeline_ai.security.roles import Principal

log = logging.getLogger("mhia.query")

Status = Literal["answered", "no_evidence", "blocked"]


class QueryTimeoutError(Exception):
    """La consulta superó QUERY_TIMEOUT_S (incluida la espera por un turno)."""


class QueryFailedError(Exception):
    """Falla interna del pipeline. Sin detalle: puede contener datos de la consulta."""


@dataclass(frozen=True)
class QueryOutcome:
    status: Status
    answer: str
    sources: list[str]


def _status_of(result: dict) -> Status:
    if result.get("retrieval_ok") is False:
        return "no_evidence"
    if result.get("aborted"):
        return "blocked"
    return "answered"


def _unique_sources(result: dict) -> list[str]:
    seen: dict[str, None] = {}
    for citation in result.get("citations", []):
        seen.setdefault(str(citation["source"]), None)
    return list(seen)


def to_outcome(result: dict) -> QueryOutcome:
    """Traduce el estado final del grafo a lo que puede ver el usuario.

    Solo una respuesta efectiva lleva fuentes; un bloqueo o la falta de evidencia no revelan
    qué documentos se habrían usado."""
    status = _status_of(result)
    answer = str(result.get("final_answer", ""))
    sources = _unique_sources(result) if status == "answered" else []
    return QueryOutcome(status=status, answer=answer, sources=sources)


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


async def run_query(
    principal: Principal,
    text: str,
    *,
    slots: asyncio.Semaphore,
    graph=None,
) -> QueryOutcome:
    graph = graph if graph is not None else mhia_graph
    session_id = str(uuid.uuid4())
    started = time.perf_counter()
    base = (
        f"user_id={principal.user_id} role={principal.role.value} session={session_id} "
        f"query_sha256={_fingerprint(text)} query_len={len(text)}"
    )

    async def _invoke() -> dict:
        async with slots:
            return await graph.ainvoke(
                {
                    "messages": [],
                    "query": text,
                    "session_id": session_id,
                    "role": principal.role.value,
                    "memberships": to_state(principal.memberships),
                }
            )

    try:
        result = await asyncio.wait_for(_invoke(), timeout=settings.query_timeout_s)
    except TimeoutError:
        log.warning("query_timeout %s ms=%d", base, _elapsed_ms(started))
        raise QueryTimeoutError from None
    except Exception as exc:
        # Solo el tipo: el mensaje puede traer fragmentos de la consulta o de los documentos.
        log.error("query_error %s error=%s ms=%d", base, type(exc).__name__, _elapsed_ms(started))
        raise QueryFailedError from None

    outcome = to_outcome(result)
    log.info(
        "query_done %s status=%s sources=%d ms=%d",
        base,
        outcome.status,
        len(outcome.sources),
        _elapsed_ms(started),
    )
    return outcome


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
