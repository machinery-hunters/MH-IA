"""
Ensamblado del grafo MH IA.

Flujo:

    START
      |
      v
  router_node ------------------ (Ollama: clasifica dominio/confidencialidad)
      |
      v
  retriever_node --------------- (Qdrant: recupera contexto; alias de fuentes)
      |
      +-- retrieval_ok == False --> abort_node --> END
      |
      v retrieval_ok == True
  ollama_anonymizer_node ------- (Ollama: anonimiza consulta + contexto)
      |
      v
  outbound_gateway_node -------- (verifica, aliasa fuentes, scrubber, auditoría)
      |
      +-- bloqueado -------------> END   (no se invoca ningún modelo de razonamiento)
      |
      v enviado
  reasoner_node ---------------- (razona + cita) --> END
                                 REASONING_MODE=local  -> modelo local (por defecto, sin costo)
                                 REASONING_MODE=claude -> Claude; si falla, modelo local

El outbound gateway es la ÚNICA puerta hacia el razonamiento (y hacia Claude):
tests/test_claude_isolation.py comprueba que ninguna otra arista llega a `reasoner`, que los
dos proveedores solo reciben el payload de la pasarela y que ningún otro módulo importa el SDK
de Anthropic.
"""

from langgraph.graph import END, START, StateGraph

from pipeline_ai.nodes.guardrail_node import abort_node
from pipeline_ai.nodes.ollama_node import ollama_anonymizer_node
from pipeline_ai.nodes.outbound_node import outbound_gateway_node
from pipeline_ai.nodes.reasoner_node import reasoner_node
from pipeline_ai.nodes.retriever_node import retriever_node
from pipeline_ai.nodes.router import router_node
from pipeline_ai.state import MHIAState


def _route_after_retrieval(state: MHIAState) -> str:
    """Edge condicional: aplica la regla de 'cero alucinaciones'."""
    if not state.get("retrieval_ok", False):
        return "abort"
    return "continue"


def _route_after_gateway(state: MHIAState) -> str:
    """Edge condicional: solo se razona si el gateway aprobó el envío."""
    if state.get("outbound") is None:
        return "blocked"
    return "send"


def build_graph():
    graph = StateGraph(MHIAState)

    graph.add_node("router", router_node)
    graph.add_node("retriever", retriever_node)
    graph.add_node("anonymizer", ollama_anonymizer_node)
    graph.add_node("outbound_gateway", outbound_gateway_node)
    graph.add_node("reasoner", reasoner_node)
    graph.add_node("abort", abort_node)

    graph.add_edge(START, "router")
    graph.add_edge("router", "retriever")

    graph.add_conditional_edges(
        "retriever",
        _route_after_retrieval,
        {
            "continue": "anonymizer",
            "abort": "abort",
        },
    )

    graph.add_edge("anonymizer", "outbound_gateway")

    graph.add_conditional_edges(
        "outbound_gateway",
        _route_after_gateway,
        {
            "send": "reasoner",
            "blocked": END,
        },
    )

    graph.add_edge("reasoner", END)
    graph.add_edge("abort", END)

    return graph.compile()


mhia_graph = build_graph()
