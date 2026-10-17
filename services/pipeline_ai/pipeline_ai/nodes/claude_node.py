"""
claude_reasoner_node

Único nodo con acceso a la API de Anthropic. Solo recibe:
  - state["anonymized_context"]  (nunca raw_context)
  - state["citations"]           (metadatos, no contenido sensible per se)
  - state["query"]

REGLA CRÍTICA #1: el prompt de sistema EXIGE que cada afirmación cite una
fuente de `citations`. Si el modelo no puede fundamentar algo, debe decirlo
explícitamente en vez de inferir. Este nodo solo se alcanza si
retrieval_ok=True (garantizado por el edge condicional del grafo).
"""

from functools import lru_cache

from langchain_anthropic import ChatAnthropic

from pipeline_ai.config import settings
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
   ej: "El equipo fue enviado a subasta [Fuente: Inventario_Subasta_012]".
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


async def claude_reasoner_node(state: MHIAState) -> dict:
    context = state.get("anonymized_context", "")
    sources = "\n".join(
        f"- {c['source']} (score={c['score']:.2f})" for c in state.get("citations", [])
    )

    messages = [
        ("system", _SYSTEM_PROMPT.format(context=context, sources=sources)),
        ("human", state["query"]),
    ]

    response = await _get_claude().ainvoke(messages)

    return {"final_answer": response.content, "aborted": False}
