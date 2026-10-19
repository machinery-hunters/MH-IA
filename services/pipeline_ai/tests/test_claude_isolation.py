"""Claude solo recibe lo que aprobó el outbound gateway, y nada más puede llegar a Claude."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline_ai.nodes.claude_node as claude_mod
from pipeline_ai.graph import mhia_graph
from pipeline_ai.nodes.outbound_node import sensitivity_for_state
from pipeline_ai.privacy.outbound_gateway import prepare_outbound
from pipeline_ai.privacy.sensitivity import Sensitivity

# pipeline_ai es un paquete de espacios de nombres (sin __init__.py): la ruta sale de un módulo.
PACKAGE_DIR = Path(claude_mod.__file__).resolve().parents[1]

REAL_1 = "Google Drive: Contrato_Consignacion_034.pdf"
REAL_2 = "Google Drive: Balance_2025.xlsx"


class FakeClaude:
    def __init__(self, reply="Cliente_A debe Monto_X [Fuente: Fuente_1]"):
        self.reply = reply
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=self.reply)


def _payload():
    return prepare_outbound(
        query="¿Cuánto debe Cliente_A según Fuente_1?",
        context="[Fuente: Fuente_1]\nCliente_A debe Monto_X\n\n---\n\n[Fuente: Fuente_2]\nBalance",
        citations=[
            {"source": REAL_1, "chunk_id": "c1", "score": 0.9},
            {"source": REAL_2, "chunk_id": "c2", "score": 0.8},
        ],
        sensitivity=Sensitivity.CONFIDENTIAL,
        anonymized=True,
    )


# -- El nodo solo usa el payload -----------------------------------------------------


async def test_claude_only_receives_the_gateway_payload(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setattr(claude_mod, "_get_claude", lambda: fake)
    payload = _payload()
    state = {
        "query": "SENTINEL_RAW_QUERY Acme",
        "raw_context": "SENTINEL_RAW_CONTEXT",
        "anonymized_context": "SENTINEL_ANON_CONTEXT",
        "citations": [{"source": "SENTINEL_REAL_FILE.pdf", "chunk_id": "c", "score": 0.9}],
        "source_aliases": {"Fuente_1": "SENTINEL_REAL_FILE.pdf"},
        "outbound": payload,
    }

    result = await claude_mod.claude_reasoner_node(state)

    assert len(fake.calls) == 1
    sent = "\n".join(text for _role, text in fake.calls[0])
    assert payload.query in sent and payload.context in sent
    assert "SENTINEL" not in sent
    assert "Contrato_Consignacion" not in sent
    assert result["aborted"] is False
    # La respuesta se re-etiqueta en local con los nombres reales.
    assert REAL_1 in result["final_answer"]


async def test_claude_is_not_called_without_a_gateway_payload(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setattr(claude_mod, "_get_claude", lambda: fake)

    missing = await claude_mod.claude_reasoner_node({"query": "x"})
    forged = await claude_mod.claude_reasoner_node(
        {"query": "x", "outbound": {"query": "y", "context": "z"}}
    )

    assert fake.calls == []
    assert missing["aborted"] is True and forged["aborted"] is True


async def test_response_given_as_content_blocks_is_joined_and_relabeled(monkeypatch):
    fake = FakeClaude(
        reply=[{"type": "text", "text": "Hola "}, {"type": "text", "text": "[Fuente: Fuente_2]"}]
    )
    monkeypatch.setattr(claude_mod, "_get_claude", lambda: fake)

    result = await claude_mod.claude_reasoner_node({"outbound": _payload()})

    assert result["final_answer"] == f"Hola [Fuente: {REAL_2}]"


# -- Nada más puede llegar a Claude ------------------------------------------------------

_SDK_MODULES = {"anthropic", "langchain_anthropic"}
_SDK_TOKENS = ("ChatAnthropic", "init_chat_model", "api.anthropic.com")


def test_only_claude_node_imports_the_anthropic_sdk():
    offenders = []
    for path in PACKAGE_DIR.rglob("*.py"):
        if path.name == "claude_node.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.split(".")[0] in _SDK_MODULES for name in names):
                offenders.append(f"{path.name}: {names}")
    assert offenders == []


def test_no_other_module_builds_an_anthropic_client_dynamically():
    offenders = [
        path.name
        for path in PACKAGE_DIR.rglob("*.py")
        if path.name != "claude_node.py"
        and any(token in path.read_text(encoding="utf-8") for token in _SDK_TOKENS)
    ]
    assert offenders == []


def test_the_only_edge_into_claude_comes_from_the_outbound_gateway():
    edges = mhia_graph.get_graph().edges
    assert {e.source for e in edges if e.target == "claude_reasoner"} == {"outbound_gateway"}


def test_the_gateway_is_only_reached_from_the_anonymizer():
    edges = mhia_graph.get_graph().edges
    assert {e.source for e in edges if e.target == "outbound_gateway"} == {"anonymizer"}


def test_a_blocked_gateway_ends_the_flow_instead_of_reaching_claude():
    edges = mhia_graph.get_graph().edges
    assert {e.target for e in edges if e.source == "outbound_gateway"} == {
        "claude_reasoner",
        "__end__",
    }


# -- Sensibilidad que se le pasa al gateway --------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"data_domain": "proveedores", "is_confidential": True}, Sensitivity.CONFIDENTIAL),
        ({"data_domain": "general", "is_confidential": False}, Sensitivity.INTERNAL),
        # El retriever puede elevar la confidencialidad aunque el dominio sea general.
        ({"data_domain": "general", "is_confidential": True}, Sensitivity.CONFIDENTIAL),
        # Sin dominio no hay señal: falla cerrada.
        ({"is_confidential": False}, Sensitivity.CONFIDENTIAL),
        ({"data_domain": "inventedado", "is_confidential": False}, Sensitivity.CONFIDENTIAL),
    ],
)
def test_sensitivity_for_state_takes_the_maximum_signal(state, expected):
    assert sensitivity_for_state(state) is expected
