"""POST /api/v1/query: el grafo corre en nombre del usuario de la sesión."""

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

import pipeline_ai.api.query_service as query_service
from pipeline_ai.api.deps import get_current_principal
from pipeline_ai.api.main import create_app
from pipeline_ai.security.roles import Principal, Role

QUERY = "¿Cuánto debe Acme S.A. por el contrato 034?"
ANSWER = "Acme S.A. debe $1,250,000.00 [Fuente: Google Drive: Contrato_034.pdf]"


class FakeGraph:
    def __init__(self, result=None, *, error=None, delay=0.0):
        self.result = result if result is not None else _answered()
        self.error = error
        self.delay = delay
        self.states = []

    async def ainvoke(self, state):
        self.states.append(state)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result


def _answered(**extra):
    result = {
        "final_answer": ANSWER,
        "aborted": False,
        "retrieval_ok": True,
        "reasoner": "local",
        "citations": [
            {"source": "Google Drive: Contrato_034.pdf", "chunk_id": "a::0", "score": 0.9},
            {"source": "Google Drive: Contrato_034.pdf", "chunk_id": "a::1", "score": 0.85},
            {"source": "Google Drive: Anexo.pdf", "chunk_id": "b::0", "score": 0.8},
        ],
    }
    result.update(extra)
    return result


def _client(graph, role=Role.LIDER, monkeypatch=None, *, concurrency=None):
    monkeypatch.setattr(query_service, "mhia_graph", graph)
    if concurrency is not None:
        monkeypatch.setattr(query_service.settings, "query_max_concurrency", concurrency)
    app = create_app(allowed_hosts=["testserver"])

    async def principal() -> Principal:
        return Principal(user_id="u-123", role=role, username="ana")

    app.dependency_overrides[get_current_principal] = principal
    return TestClient(app)


def _post(client, query=QUERY, **extra):
    return client.post("/api/v1/query", json={"query": query, **extra})


# -- Contrato ------------------------------------------------------------------------------


def test_an_answered_query_returns_the_answer_and_unique_sources(monkeypatch):
    response = _post(_client(FakeGraph(), monkeypatch=monkeypatch))

    assert response.status_code == 200
    assert response.json() == {
        "status": "answered",
        "answer": ANSWER,
        "sources": ["Google Drive: Contrato_034.pdf", "Google Drive: Anexo.pdf"],
        "reasoner": "local",
    }


@pytest.mark.parametrize(
    ("graph_value", "expected"),
    [("claude", "claude"), ("local", "local"), (None, None), ("otro", None)],
)
def test_the_response_says_which_model_wrote_the_answer(monkeypatch, graph_value, expected):
    graph = FakeGraph(_answered(reasoner=graph_value))

    body = _post(_client(graph, monkeypatch=monkeypatch)).json()

    assert body["reasoner"] == expected


def test_a_blocked_or_unanswered_query_reports_no_reasoner(monkeypatch):
    graph = FakeGraph(_answered(aborted=True, abort_reason="envio_bloqueado: x"))

    body = _post(_client(graph, monkeypatch=monkeypatch)).json()

    assert body["status"] == "blocked" and body["reasoner"] is None


def test_the_api_does_not_start_with_an_unknown_reasoning_mode(monkeypatch):
    monkeypatch.setattr(query_service.settings, "reasoning_mode", "nube")
    app = create_app(allowed_hosts=["testserver"])

    with pytest.raises(RuntimeError, match="REASONING_MODE"):
        with TestClient(app):
            pass


def test_the_api_does_not_start_in_claude_mode_without_an_api_key(monkeypatch):
    monkeypatch.setattr(query_service.settings, "reasoning_mode", "claude")
    monkeypatch.setattr(query_service.settings, "anthropic_api_key", "")
    app = create_app(allowed_hosts=["testserver"])

    with pytest.raises(RuntimeError, match="anthropic_api_key"):
        with TestClient(app):
            pass


def test_no_evidence_has_no_sources(monkeypatch):
    graph = FakeGraph(
        {"final_answer": "No tengo información suficiente.", "aborted": True, "retrieval_ok": False}
    )

    body = _post(_client(graph, monkeypatch=monkeypatch)).json()

    assert body["status"] == "no_evidence" and body["sources"] == []


def test_a_blocked_send_reveals_no_sources_even_if_the_graph_had_citations(monkeypatch):
    graph = FakeGraph(
        _answered(
            aborted=True,
            abort_reason="envio_bloqueado: contexto:email",
            final_answer="No se envió nada.",
        )
    )

    body = _post(_client(graph, monkeypatch=monkeypatch)).json()

    assert body["status"] == "blocked"
    assert body["sources"] == []
    assert "contexto:email" not in str(body)  # el motivo detallado no sale de la API


def test_the_query_is_trimmed_before_reaching_the_graph(monkeypatch):
    graph = FakeGraph()
    _post(_client(graph, monkeypatch=monkeypatch), query=f"  {QUERY}\n")

    assert graph.states[0]["query"] == QUERY


# -- El rol sale de la sesión, nunca del cliente ----------------------------------------------


@pytest.mark.parametrize("role", list(Role))
def test_the_graph_receives_the_role_of_the_session(monkeypatch, role):
    graph = FakeGraph()
    _post(_client(graph, role=role, monkeypatch=monkeypatch))

    assert graph.states[0]["role"] == role.value
    assert graph.states[0]["messages"] == []


def test_a_role_sent_by_the_client_is_ignored(monkeypatch):
    graph = FakeGraph()
    client = _client(graph, role=Role.CONSULTA, monkeypatch=monkeypatch)

    response = client.post(
        "/api/v1/query",
        json={"query": QUERY, "role": "control_total", "user_id": "otro", "session_id": "x"},
    )

    assert response.status_code == 200
    state = graph.states[0]
    assert state["role"] == "consulta"
    assert state["session_id"] not in {"x", "otro"}


def test_each_query_gets_its_own_session_id(monkeypatch):
    graph = FakeGraph()
    client = _client(graph, monkeypatch=monkeypatch)
    _post(client)
    _post(client)

    assert graph.states[0]["session_id"] != graph.states[1]["session_id"]


# -- Errores: genéricos, sin filtrar contenido ------------------------------------------------


def test_internal_errors_become_a_generic_502_without_leaking_their_message(monkeypatch):
    graph = FakeGraph(error=RuntimeError("falló procesando Acme S.A. y $1,250,000"))

    response = _post(_client(graph, monkeypatch=monkeypatch))

    assert response.status_code == 502
    assert response.json() == {"detail": "No se pudo completar la consulta. Intenta de nuevo."}
    assert "Acme" not in response.text and "1,250,000" not in response.text


def test_a_slow_pipeline_returns_504(monkeypatch):
    monkeypatch.setattr(query_service.settings, "query_timeout_s", 0.05)
    graph = FakeGraph(delay=1.0)

    response = _post(_client(graph, monkeypatch=monkeypatch))

    assert response.status_code == 504
    assert "tardó demasiado" in response.json()["detail"]


# -- Auditoría ----------------------------------------------------------------------------------


def test_the_audit_line_says_who_asked_but_never_what(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="mhia.query")
    graph = FakeGraph()
    _post(_client(graph, role=Role.LIDER, monkeypatch=monkeypatch))

    line = caplog.records[-1].getMessage()
    assert "user_id=u-123" in line and "role=lider" in line
    assert f"session={graph.states[0]['session_id']}" in line
    assert "query_sha256=" in line and f"query_len={len(QUERY)}" in line
    assert "status=answered" in line and "sources=2" in line and "reasoner=local" in line
    # (sin "034" suelto: un hash o UUID hexadecimal podría contenerlo por azar)
    for secret in ("Acme", "contrato 034", "1,250,000", "Contrato", ANSWER):
        assert secret not in caplog.text


def test_failures_are_audited_with_the_exception_type_only(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="mhia.query")
    graph = FakeGraph(error=ValueError("dato sensible: Acme S.A."))
    _post(_client(graph, monkeypatch=monkeypatch))

    assert "query_error" in caplog.text and "error=ValueError" in caplog.text
    assert "Acme" not in caplog.text and "dato sensible" not in caplog.text


def test_timeouts_are_audited(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="mhia.query")
    monkeypatch.setattr(query_service.settings, "query_timeout_s", 0.05)
    _post(_client(FakeGraph(delay=1.0), monkeypatch=monkeypatch))

    assert "query_timeout" in caplog.text and "user_id=u-123" in caplog.text


# -- Concurrencia acotada -------------------------------------------------------------------------


async def test_concurrent_queries_are_limited_by_the_semaphore(monkeypatch):
    running = 0
    peak = 0

    class Counting:
        async def ainvoke(self, state):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.02)
            running -= 1
            return _answered()

    principal = Principal(user_id="u", role=Role.LIDER)
    slots = asyncio.Semaphore(2)
    await asyncio.gather(
        *(query_service.run_query(principal, "q", slots=slots, graph=Counting()) for _ in range(6))
    )

    assert peak == 2


def test_the_concurrency_setting_can_never_disable_the_api(monkeypatch):
    # Un valor inválido (0) se interpreta como 1: la API sigue atendiendo.
    response = _post(_client(FakeGraph(), monkeypatch=monkeypatch, concurrency=0))

    assert response.status_code == 200
