"""
Proveedor local del nodo de razonamiento (REASONING_MODE=local, el modo por defecto).

Usa el modelo de Ollama `OLLAMA_REASONER_MODEL` (en la Mac, el "modelo local grande" del plan,
sección 3.3; si no se define, el mismo modelo de trabajo `OLLAMA_LLM_MODEL`). No sale nada de
la red local y no tiene costo.

Recibe lo mismo que Claude: solo el `OutboundPayload` de la pasarela de salida, ya anonimizado
y verificado. Aunque el destino sea local, se conserva esa frontera a propósito: así el
comportamiento de privacidad se prueba antes de gastar en la API (plan, sección 8.1), y cambiar
de proveedor no cambia lo que el modelo ve.

Falla cerrada: si Ollama no responde o excede REASONER_TIMEOUT_S se lanza
`LocalReasonerError` (solo con el tipo de la excepción original, nunca su mensaje, que podría
traer fragmentos del texto). La API responde entonces con un error genérico.
"""

import asyncio
from functools import lru_cache

from langchain_ollama import ChatOllama

from pipeline_ai.config import settings
from pipeline_ai.nodes.reasoning_prompt import as_text, build_messages
from pipeline_ai.privacy.outbound_gateway import OutboundPayload


class LocalReasonerError(Exception):
    """El modelo local de razonamiento no produjo respuesta."""


@lru_cache
def _get_local_reasoner() -> ChatOllama:
    return ChatOllama(
        model=settings.ollama_reasoner_model,
        base_url=settings.ollama_base_url,
        temperature=0.2,
        num_ctx=settings.reasoner_num_ctx,
        num_predict=settings.reasoner_max_tokens,
        client_kwargs={"timeout": settings.reasoner_timeout_s},
    )


async def reason_locally(payload: OutboundPayload) -> str:
    """Respuesta del modelo local, todavía con alias y marcadores (la restaura `reasoner_node`)."""
    try:
        response = await asyncio.wait_for(
            _get_local_reasoner().ainvoke(build_messages(payload)),
            timeout=settings.reasoner_timeout_s,
        )
    except TimeoutError:
        raise LocalReasonerError("tiempo agotado") from None
    except Exception as exc:  # noqa: BLE001 - Ollama caído, modelo ausente, red...
        raise LocalReasonerError(f"modelo local no disponible ({type(exc).__name__})") from None
    text = as_text(response.content)
    if not text:
        raise LocalReasonerError("respuesta vacía")
    return text
