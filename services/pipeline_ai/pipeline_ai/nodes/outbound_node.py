"""
outbound_gateway_node

Último nodo antes de Claude. Es el adaptador entre el estado del grafo y
`privacy.outbound_gateway.prepare_outbound`:

  - Deriva la sensibilidad efectiva (máximo de las señales; ver privacy/sensitivity.py).
  - Si el gateway aprueba, deja el `OutboundPayload` en `state["outbound"]`.
  - Si lo bloquea, termina el flujo con una respuesta fija: NO se invoca a Claude
    y NO se devuelve contenido (solo los tipos de hallazgo, en `abort_reason`).
"""

import logging

from pipeline_ai.privacy.outbound_gateway import OutboundBlocked, prepare_outbound
from pipeline_ai.privacy.sensitivity import (
    Sensitivity,
    effective_sensitivity,
    sensitivity_for_domain,
)
from pipeline_ai.state import MHIAState

log = logging.getLogger("mhia.outbound")

# Plantilla fija, sin cifras ni datos de la consulta.
_BLOCKED_ANSWER = (
    "No se envió nada al servicio de razonamiento externo porque no se pudo "
    "garantizar que la consulta y el contexto estén libres de datos sensibles. "
    "Reformula la consulta sin datos personales ni cifras exactas, o inténtalo "
    "de nuevo."
)


def sensitivity_for_state(state: MHIAState) -> Sensitivity:
    """Máximo entre el dominio de negocio y la marca de confidencialidad del
    router/retriever. Sin dominio -> CONFIDENTIAL (falla cerrada)."""
    flagged = (
        Sensitivity.CONFIDENTIAL if state.get("is_confidential", False) else Sensitivity.PUBLIC
    )
    return effective_sensitivity(sensitivity_for_domain(state.get("data_domain")), flagged)


async def outbound_gateway_node(state: MHIAState) -> dict:
    try:
        payload = prepare_outbound(
            query=state.get("anonymized_query", ""),
            context=state.get("anonymized_context", ""),
            citations=state.get("citations", []),
            sensitivity=sensitivity_for_state(state),
            anonymized=state.get("anonymized", False),
            source_aliases=state.get("source_aliases"),
            session_id=state.get("session_id", ""),
        )
    except OutboundBlocked as blocked:
        log.warning("Envío a Claude bloqueado: %s", ", ".join(blocked.reasons))
        return {
            "outbound": None,
            "aborted": True,
            "abort_reason": f"envio_bloqueado: {', '.join(blocked.reasons)}",
            "final_answer": _BLOCKED_ANSWER,
        }
    return {"outbound": payload}
