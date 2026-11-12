"""
Proveedor Claude del nodo de razonamiento (REASONING_MODE=claude).

Único módulo con acceso a la API de Anthropic (tests/test_claude_isolation.py lo comprueba).
Solo recibe el `OutboundPayload` que produce la pasarela de salida: consulta y contexto ya
anonimizados y verificados, y las fuentes con alias (Fuente_1...). El nodo que lo llama
(`reasoner_node`) nunca le pasa `state["query"]`, `raw_context`, `anonymized_context` ni
`citations`: esos pueden contener datos reales o nombres de archivo.

Si la API falla (sin crédito, sin red, clave inválida), `reasoner_node` cae al modelo local.
"""

from functools import lru_cache

from langchain_anthropic import ChatAnthropic

from pipeline_ai.config import settings
from pipeline_ai.nodes.reasoning_prompt import as_text, build_messages
from pipeline_ai.privacy.outbound_gateway import OutboundPayload


@lru_cache
def _get_claude() -> ChatAnthropic:
    settings.require("anthropic_api_key")
    return ChatAnthropic(
        model=settings.anthropic_model,
        api_key=settings.anthropic_api_key,
        temperature=0.2,
        # Sin reintentos largos: si la API no responde, se responde con el modelo local.
        max_retries=1,
        timeout=settings.claude_timeout_s,
    )


async def reason_with_claude(payload: OutboundPayload) -> str:
    """Respuesta de Claude, todavía con alias y marcadores (la restaura `reasoner_node`)."""
    response = await _get_claude().ainvoke(build_messages(payload))
    return as_text(response.content)
