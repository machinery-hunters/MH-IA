"""
Servicio de consulta: ejecuta el grafo de MH IA en nombre de un usuario autenticado.

Responsabilidades de esta capa (el grafo no sabe de HTTP ni de sesiones):
  - El rol, el usuario y sus membresías por área salen del `Principal` que resolvió la
    sesión; el cliente no puede elegirlos. Las membresías viajan en `state["memberships"]` y
    el retriever las usa para filtrar el contenido.
  - Tope de tiempo total y de consultas simultáneas (Ollama es un solo equipo).
  - Indica qué proveedor redactó la respuesta (`reasoner`: local o claude).
  - Auditoría, en dos lugares y NUNCA con el texto de la consulta ni de la respuesta:
      * registro encadenado (categoría `consulta`): quién, rol y áreas, huella y longitud de
        la consulta, resultado, proveedor, identificadores de los fragmentos usados (nunca el
        nombre del documento), huella del payload que salió del gateway y duración. Si no se
        puede escribir, la consulta falla (502) y la respuesta no se entrega.
      * logger `mhia.query`, con la misma huella, para correlacionar con el resto de logs.
    El `session_id` (uno por consulta) enlaza ambos con las líneas del gateway (`mhia.audit`).
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
from pipeline_ai.security.auth_store import get_store
from pipeline_ai.security.roles import Principal

log = logging.getLogger("mhia.query")

Status = Literal["answered", "no_evidence", "blocked"]
Reasoner = Literal["local", "claude"]


class QueryTimeoutError(Exception):
    """La consulta superó QUERY_TIMEOUT_S (incluida la espera por un turno)."""


class QueryFailedError(Exception):
    """Falla interna del pipeline. Sin detalle: puede contener datos de la consulta."""


@dataclass(frozen=True)
class QueryOutcome:
    status: Status
    answer: str
    sources: list[str]
    reasoner: Reasoner | None = None


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
    if status != "answered":
        return QueryOutcome(status=status, answer=answer, sources=[])
    reasoner = result.get("reasoner")
    return QueryOutcome(
        status=status,
        answer=answer,
        sources=_unique_sources(result),
        reasoner=reasoner if reasoner in ("local", "claude") else None,
    )


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _chunk_ids(result: dict | None) -> list[str]:
    seen: dict[str, None] = {}
    for citation in (result or {}).get("citations", []):
        chunk_id = str(citation.get("chunk_id") or "")
        if chunk_id:
            seen.setdefault(chunk_id, None)
    return list(seen)


def _record(
    principal: Principal,
    *,
    session_id: str,
    text: str,
    status: str,
    result: dict | None,
    reasoner: str | None,
    ms: int,
) -> None:
    chunks = _chunk_ids(result)
    outbound = (result or {}).get("outbound")
    get_store().record_event(
        "consulta",
        f"user:{principal.user_id}",
        "consulta",
        session_id,
        {
            "role": principal.role.value,
            "areas": to_state(principal.memberships),
            "query_sha256": _fingerprint(text),
            "query_len": len(text),
            "status": status,
            "reasoner": reasoner,
            "chunks": chunks[: max(0, settings.audit_max_chunks)],
            "chunks_total": len(chunks),
            "outbound_hash": getattr(outbound, "audit_hash", None),
            "ms": ms,
        },
    )


async def _audit(principal: Principal, **fields: object) -> None:
    try:
        await asyncio.to_thread(_record, principal, **fields)  # type: ignore[arg-type]
    except Exception as exc:
        # Sin registro no hay respuesta: la consulta se da por fallida.
        log.error(
            "query_audit_failed session=%s error=%s", fields["session_id"], type(exc).__name__
        )
        raise QueryFailedError from None


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

    audit = {"session_id": session_id, "text": text}
    try:
        result = await asyncio.wait_for(_invoke(), timeout=settings.query_timeout_s)
    except TimeoutError:
        ms = _elapsed_ms(started)
        log.warning("query_timeout %s ms=%d", base, ms)
        await _audit(principal, **audit, status="timeout", result=None, reasoner=None, ms=ms)
        raise QueryTimeoutError from None
    except Exception as exc:
        # Solo el tipo: el mensaje puede traer fragmentos de la consulta o de los documentos.
        ms = _elapsed_ms(started)
        log.error("query_error %s error=%s ms=%d", base, type(exc).__name__, ms)
        await _audit(principal, **audit, status="error", result=None, reasoner=None, ms=ms)
        raise QueryFailedError from None

    outcome = to_outcome(result)
    ms = _elapsed_ms(started)
    # Los fragmentos se registran aunque no se entregue respuesta: es lo que el sistema leyó
    # en nombre de esta persona.
    await _audit(
        principal,
        **audit,
        status=outcome.status,
        result=result,
        reasoner=result.get("reasoner"),
        ms=ms,
    )
    log.info(
        "query_done %s status=%s sources=%d reasoner=%s ms=%d",
        base,
        outcome.status,
        len(outcome.sources),
        outcome.reasoner or "-",
        ms,
    )
    return outcome


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
