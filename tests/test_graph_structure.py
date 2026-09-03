"""
Smoke tests de la estructura del grafo.

No requieren Ollama, Qdrant ni credenciales reales corriendo: solo validan
que el grafo se construye correctamente (contrato estructural), gracias a
la instanciación perezosa de clientes en cada nodo (app/config.py Settings.require).
Sirven como red de seguridad rápida en CI antes de correr nada en Docker.
"""

from app.graph import mhia_graph


def test_graph_compiles():
    assert mhia_graph is not None


def test_graph_has_expected_nodes():
    nodes = set(mhia_graph.get_graph().nodes.keys())
    expected = {
        "__start__",
        "__end__",
        "router",
        "retriever",
        "anonymizer",
        "claude_reasoner",
        "abort",
    }
    assert expected.issubset(nodes)


def test_abort_is_reachable_from_retriever():
    """Garantiza estructuralmente la regla de 'cero alucinaciones':
    debe existir un camino retriever -> abort en el grafo compilado."""
    graph = mhia_graph.get_graph()
    edges_from_retriever = {e.target for e in graph.edges if e.source == "retriever"}
    assert "abort" in edges_from_retriever
