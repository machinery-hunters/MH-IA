"""
Ensamblado del grafo MH IA.

Flujo:

    START
      │
      ▼
  router_node ───────────────────────────────► (Ollama: clasifica dominio/confidencialidad)
      │
      ▼
  retriever_node ────────────────────────────► (Qdrant: recupera contexto)
      │
      ├─ retrieval_ok == False ──────────────► abort_node ──► END
      │
      ▼ retrieval_ok == True
  ollama_anonymizer_node ─────────────────────► (Ollama: anonimiza si es confidencial)
      │
      ▼
  claude_reasoner_node ───────────────────────► (Claude: razona + cita) ──► END
"""

from langgraph.graph import END, START, StateGraph

from pipeline_ai.nodes.claude_node import claude_reasoner_node
from pipeline_ai.nodes.guardrail_node import abort_node
from pipeline_ai.nodes.ollama_node import ollama_anonymizer_node
from pipeline_ai.nodes.retriever_node import retriever_node
from pipeline_ai.nodes.router import router_node
from pipeline_ai.state import MHIAState


def _route_after_retrieval(state: MHIAState) -> str:
    """Edge condicional: aplica la regla de 'cero alucinaciones'."""
    if not state.get("retrieval_ok", False):
        return "abort"
    return "continue"


def build_graph():
    graph = StateGraph(MHIAState)

    graph.add_node("router", router_node)
    graph.add_node("retriever", retriever_node)
    graph.add_node("anonymizer", ollama_anonymizer_node)
    graph.add_node("claude_reasoner", claude_reasoner_node)
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

    graph.add_edge("anonymizer", "claude_reasoner")
    graph.add_edge("claude_reasoner", END)
    graph.add_edge("abort", END)

    return graph.compile()


mhia_graph = build_graph()
