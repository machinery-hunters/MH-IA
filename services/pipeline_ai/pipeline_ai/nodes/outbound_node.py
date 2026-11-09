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
# Contenido RESTRICTED (RH, documentos financieros marcados): nunca sale de la red local.
_RESTRICTED_ANSWER = (
    "La información que responde esta consulta es de acceso restringido y no puede enviarse "
    "al servicio de razonamiento externo. Las respuestas sobre información restringida se "
    "darán solo con el modelo local."
)


def sensitivity_for_state(state: MHIAState) -> Sensitivity:
    """Máximo entre el dominio de la consulta (router), la marca de confidencialidad y la
    sensibilidad real del contenido recuperado (retriever). Sin dominio -> CONFIDENTIAL
    (falla cerrada). Contenido RESTRICTED -> la pasarela bloquea el envío a Claude."""
    flagged = (
        Sensitivity.CONFIDENTIAL if state.get("is_confidential", False) else Sensitivity.PUBLIC
    )
    levels = [sensitivity_for_domain(state.get("data_domain")), flagged]
    raw = state.get("content_sensitivity")
    if raw is not None:
        try:
            levels.append(Sensitivity(int(raw)))
        except (TypeError, ValueError):
            levels.append(Sensitivity.RESTRICTED)  # valor corrupto: lo más estricto
    return effective_sensitivity(*levels)


async def outbound_gateway_node(state: MHIAState) -> dict:
    try:
        payload = prepare_outbound(
            query=state.get("anonymized_query", ""),
            context=state.get("anonymized_context", ""),
            citations=state.get("citations", []),
            sensitivity=sensitivity_for_state(state),
            anonymized=state.get("anonymized", False),
            source_aliases=state.get("source_aliases"),
            known_names=state.get("known_names", ()),
            placeholder_map=state.get("placeholder_map"),
            session_id=state.get("session_id", ""),
        )
    except OutboundBlocked as blocked:
        log.warning("Envío a Claude bloqueado: %s", ", ".join(blocked.reasons))
        return {
            "outbound": None,
            "aborted": True,
            "abort_reason": f"envio_bloqueado: {', '.join(blocked.reasons)}",
            "final_answer": (
                _RESTRICTED_ANSWER
                if "sensibilidad_restringida" in blocked.reasons
                else _BLOCKED_ANSWER
            ),
        }
    return {"outbound": payload}
