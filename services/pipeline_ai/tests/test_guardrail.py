import pytest

from pipeline_ai.nodes.guardrail_node import abort_node
from pipeline_ai.state import MHIAState


@pytest.mark.asyncio
async def test_abort_node_declares_unknown_and_does_not_fabricate():
    state: MHIAState = {"query": "¿cuántas máquinas quedan?", "data_domain": "inventario"}
    result = await abort_node(state)

    assert result["aborted"] is True
    assert "no tengo información" in result["final_answer"].lower()
    # No debe contener cifras "inventadas": una heurística simple de red de
    # seguridad es que la respuesta de aborto no debería llevar dígitos.
    assert not any(ch.isdigit() for ch in result["final_answer"])
