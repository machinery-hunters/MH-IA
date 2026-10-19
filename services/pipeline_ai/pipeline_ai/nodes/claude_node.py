"""
claude_reasoner_node

Unico nodo con acceso a la API de Anthropic. Solo lee `state["outbound"]`, el
OutboundPayload que produce el outbound gateway (privacy/outbound_gateway.py):
consulta y contexto ya anonimizados y verificados, y las fuentes con alias
(Fuente_1...). NUNCA lee `state["query"]`, `raw_context`, `anonymized_context`
ni `citations`: esos pueden contener datos reales o nombres de archivo.

Si no hay un OutboundPayload válido, no se llama a Claude. Estructuralmente el
grafo no permite ese caso (el único camino a este nodo pasa por el gateway); la
comprobación es defensa en profundidad.

REGLA CRITICA #1: el prompt de sistema EXIGE que cada afirmación cite una
fuente. Si el modelo no puede fundamentar algo, debe decirlo explícitamente en
vez de inferir. Este nodo solo se alcanza si retrieval_ok=True (garantizado por
el edge condicional del grafo).

La respuesta se re-etiqueta en LOCAL: los alias Fuente_N vuelven a ser el
nombre real del documento antes de mostrarse al usuario.
"""

from functools import lru_cache

from langchain_anthropic import ChatAnthropic

from pipeline_ai.config import settings
from pipeline_ai.privacy.outbound_gateway import OutboundPayload
from pipeline_ai.state import MHIAState


@lru_cache
def _get_claude() -> ChatAnthropic:
    settings.require("anthropic_api_key")
    return ChatAnthropic(
        model=settings.anthropic_model,
        api_key=settings.anthropic_api_key,
        temperature=0.2,
    )


_SYSTEM_PROMPT = """Eres el analista de razonamiento de MH IA para Machinery Hunters.

REGLAS INQUEBRANTABLES:
1. Solo puedes afirmar algo si está respaldado por el CONTEXTO proporcionado.
2. Cada afirmación factual debe ir acompañada de su fuente entre corchetes,
   ej: "El equipo fue enviado a subasta [Fuente: Fuente_1]".
3. Si el contexto no cubre completamente la pregunta, dilo explícitamente:
   indica qué sí puedes responder y qué queda sin fundamento.
4. Nunca inventes cifras, nombres, fechas o folios que no aparezcan en el
   contexto (el contexto puede estar anonimizado con placeholders como
   Cliente_A, Monto_X: consérvalos tal cual, no los "completes").
5. Responde en español, tono profesional, directo.

CONTEXTO RECUPERADO:
{context}

FUENTES DISPONIBLES PARA CITAR:
{sources}
"""

# Plantilla fija, sin datos de la consulta.
_NO_PAYLOAD_ANSWER = (
    "No se envió nada al servicio de razonamiento externo porque la consulta "
    "no pasó la verificación de privacidad."
)


def _as_text(content: object) -> str:
    """El contenido de la respuesta puede ser texto o una lista de bloques."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part if isinstance(part, str) else str(part.get("text", ""))
            for part in content
            if isinstance(part, str | dict)
        )
    return str(content)


async def claude_reasoner_node(state: MHIAState) -> dict:
    payload = state.get("outbound")
    if not isinstance(payload, OutboundPayload):
        return {
            "aborted": True,
            "abort_reason": "sin_payload_del_gateway",
            "final_answer": _NO_PAYLOAD_ANSWER,
        }

    messages = [
        (
            "system",
            _SYSTEM_PROMPT.format(context=payload.context, sources="\n".join(payload.source_lines)),
        ),
        ("human", payload.query),
    ]

    response = await _get_claude().ainvoke(messages)

    return {"final_answer": payload.restore(_as_text(response.content)), "aborted": False}
