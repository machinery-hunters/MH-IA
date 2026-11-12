"""
reasoner_node: el nodo de razonamiento del grafo. Elige el proveedor según REASONING_MODE.

  - local  (por defecto): modelo de Ollama en la Mac/laptop. Sin costo y sin salida de red.
  - claude: API de Anthropic. Si falla (crédito agotado, sin red, clave inválida) responde el
            modelo local, sin sorpresas en la factura ni consultas perdidas.

En los dos casos el nodo solo lee `state["outbound"]`, el `OutboundPayload` que produce la
pasarela de salida, y los dos proveedores reciben exactamente los mismos mensajes
(`reasoning_prompt.build_messages`). Sin payload válido no se llama a ningún modelo: el grafo no
permite ese caso (el único camino a este nodo pasa por la pasarela) y la comprobación es
defensa en profundidad.

La respuesta se re-etiqueta en LOCAL: los alias Fuente_N y los marcadores ([EMPRESA_1]...)
vuelven a ser el dato real antes de mostrarse al usuario.

Auditoría `mhia.audit`: una línea `reasoning_done` con la sesión, el hash del payload (el mismo
de `outbound_prepared`), el proveedor que respondió y, si hubo caída a local, el TIPO de error
de Claude. Nunca el texto.
"""

import logging

from pipeline_ai.config import settings
from pipeline_ai.nodes.claude_node import reason_with_claude
from pipeline_ai.nodes.local_reasoner import reason_locally
from pipeline_ai.privacy.outbound_gateway import OutboundPayload
from pipeline_ai.state import MHIAState

log = logging.getLogger("mhia.reasoner")
audit_log = logging.getLogger("mhia.audit")

# Plantilla fija, sin datos de la consulta.
_NO_PAYLOAD_ANSWER = (
    "No se generó una respuesta porque la consulta no pasó la verificación de privacidad."
)


async def reasoner_node(state: MHIAState) -> dict:
    payload = state.get("outbound")
    if not isinstance(payload, OutboundPayload):
        return {
            "aborted": True,
            "abort_reason": "sin_payload_del_gateway",
            "final_answer": _NO_PAYLOAD_ANSWER,
        }

    mode = settings.require_reasoning_mode()
    fallback_from = ""
    if mode == "claude":
        try:
            text = await reason_with_claude(payload)
            provider = "claude"
        except Exception as exc:  # noqa: BLE001 - cualquier falla de la API cae a local
            # Solo el tipo: el mensaje de error podría citar parte de la petición.
            fallback_from = type(exc).__name__
            log.warning("Claude no disponible (%s): responde el modelo local", fallback_from)
            text = await reason_locally(payload)
            provider = "local"
    else:
        text = await reason_locally(payload)
        provider = "local"

    audit_log.info(
        "reasoning_done session=%s audit_hash=%s provider=%s fallback_from=%s",
        state.get("session_id", ""),
        payload.audit_hash,
        provider,
        fallback_from or "-",
    )
    return {"final_answer": payload.restore(text), "aborted": False, "reasoner": provider}
