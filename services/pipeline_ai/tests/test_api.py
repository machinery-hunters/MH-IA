import pytest
from fastapi.testclient import TestClient

from pipeline_ai.api.deps import get_current_principal
from pipeline_ai.api.main import create_app
from pipeline_ai.config import settings
from pipeline_ai.security.roles import Principal, Role


def _client(role: Role | None = None) -> TestClient:
    app = create_app(allowed_hosts=["testserver"])
    if role is not None:

        async def fake_principal() -> Principal:
            return Principal(user_id="u1", role=role)

        app.dependency_overrides[get_current_principal] = fake_principal
    return TestClient(app)


def test_health_is_public_and_minimal():
    r = _client().get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_endpoints_deny_by_default_without_authentication():
    c = _client()
    assert c.get("/api/v1/me").status_code == 401
    assert c.get("/api/v1/connectors").status_code == 401
    assert c.post("/api/v1/query", json={"query": "hola"}).status_code == 401


def test_docs_are_disabled_unless_debug():
    assert not settings.debug
    c = _client()
    assert c.get("/docs").status_code == 404
    assert c.get("/openapi.json").status_code == 404


def test_untrusted_host_header_is_rejected():
    c = TestClient(create_app(allowed_hosts=["mhia.example.ts.net"]))
    assert c.get("/health").status_code == 400


def test_me_returns_permissions_from_the_server():
    r = _client(Role.LIDER).get("/api/v1/me")
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "lider"
    assert body["permissions"] == ["query"]
    assert "areas" in body
    assert "configure_connectors" not in body["permissions"]


@pytest.mark.parametrize("role", [Role.CONSULTA, Role.LIDER])
def test_connectors_endpoint_forbidden_below_configuracion(role):
    assert _client(role).get("/api/v1/connectors").status_code == 403


@pytest.mark.parametrize("role", [Role.CONFIGURACION, Role.CONTROL_TOTAL])
def test_connectors_endpoint_lists_status_without_secrets(role, monkeypatch):
    monkeypatch.setattr(settings, "erpnext_api_key", "SECRETO-ERP-123")
    r = _client(role).get("/api/v1/connectors")
    assert r.status_code == 200
    assert "SECRETO-ERP-123" not in r.text
    names = {c["name"] for c in r.json()}
    assert names == {"drive", "erp", "crm", "firestore"}
    assert all(set(c) == {"name", "display_name", "sensitivity", "configured"} for c in r.json())


def test_query_validates_input_server_side():
    c = _client(Role.CONSULTA)
    assert c.post("/api/v1/query", json={"query": ""}).status_code == 422
    assert c.post("/api/v1/query", json={"query": "x" * 2001}).status_code == 422
    assert c.post("/api/v1/query", json={"query": "   \n\t "}).status_code == 422
    assert c.post("/api/v1/query", json={}).status_code == 422
    assert c.post("/api/v1/query", json={"query": 123}).status_code == 422
