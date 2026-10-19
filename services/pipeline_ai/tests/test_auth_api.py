"""Login/logout por la API real (TestClient), cookies, CSRF, MFA y errores cerrados."""

import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient

from pipeline_ai.api.main import create_app
from pipeline_ai.api.origin import host_allowed, request_origin_ok
from pipeline_ai.config import settings
from pipeline_ai.security import auth, totp
from tests.conftest import PASSWORD

LOGIN = "/api/v1/auth/login"


@pytest.fixture
def client(auth_env):
    return TestClient(create_app(allowed_hosts=["testserver"]))


def _login(client, username="ana", password=PASSWORD, **extra):
    return client.post(LOGIN, json={"username": username, "password": password, **extra})


# -- Login / cookie ---------------------------------------------------------------------


def test_login_sets_a_hardened_session_cookie(client, make_user):
    make_user()

    r = _login(client)

    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith("mhia_session=")
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert "domain" not in cookie
    body = r.json()
    assert body["username"] == "ana" and body["role"] == "lider"
    assert "query_confidential" in body["permissions"]
    # El token viaja solo en la cookie, jamás en el cuerpo.
    assert client.cookies.get("mhia_session") not in r.text


def test_production_cookie_uses_host_prefix_and_secure_flag(client, make_user, monkeypatch):
    make_user()
    monkeypatch.setattr(settings, "cookie_secure", True)

    cookie = _login(client).headers["set-cookie"].lower()

    assert cookie.startswith("__host-mhia_session=")
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie


def test_session_cookie_authenticates_later_requests(client, make_user):
    make_user("ana", role="configuracion")
    _login(client)

    me = client.get("/api/v1/me")
    connectors = client.get("/api/v1/connectors")

    assert me.status_code == 200 and me.json()["username"] == "ana"
    assert connectors.status_code == 200
    assert me.headers["cache-control"] == "no-store"


def test_rbac_is_enforced_with_real_sessions(client, make_user):
    make_user("ana", role="consulta")
    _login(client)

    assert client.get("/api/v1/me").status_code == 200
    assert client.get("/api/v1/connectors").status_code == 403


def test_requests_without_a_session_are_denied(client):
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/connectors").status_code == 401
    assert client.post("/api/v1/query", json={"query": "hola"}).status_code == 401


def test_wrong_credentials_get_one_generic_answer(client, make_user):
    make_user()

    wrong_password = _login(client, password="contraseña-equivocada")
    unknown_user = _login(client, username="nadie")

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json() == {"detail": "Credenciales inválidas."}
    assert "set-cookie" not in wrong_password.headers


def test_brute_force_gets_429_with_retry_after(client, make_user):
    make_user()
    for _ in range(5):
        assert _login(client, password="mala-contraseña-1").status_code == 401

    r = _login(client)

    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert "set-cookie" not in r.headers


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"username": "ana"},
        {"password": "x"},
        {"username": "", "password": "x"},
        {"username": "a" * 65, "password": "x"},
        {"username": "ana", "password": "x" * 257},
        {"username": "ana", "password": "x", "totp_code": "12"},
        {"username": "ana", "password": "x", "totp_code": "abcdef"},
        {"username": "ana", "password": "x", "totp_code": "1234567"},
    ],
)
def test_login_validates_input_server_side(client, payload):
    assert client.post(LOGIN, json=payload).status_code == 422


# -- Segundo factor ---------------------------------------------------------------------


def test_control_total_flow_asks_for_the_code_then_logs_in(client, auth_env, make_user):
    secret = totp.generate_secret()
    make_user("root", role="control_total", totp_secret=secret)

    first = _login(client, "root")
    assert first.status_code == 401 and first.json()["mfa_required"] is True
    assert "set-cookie" not in first.headers

    code = totp.totp(secret, auth_env.clock.t)
    second = _login(client, "root", totp_code=code)

    assert second.status_code == 200 and second.json()["role"] == "control_total"
    assert client.get("/api/v1/me").status_code == 200


def test_totp_code_replay_is_rejected_by_the_api(client, auth_env, make_user):
    secret = totp.generate_secret()
    make_user("root", role="control_total", totp_secret=secret)
    code = totp.totp(secret, auth_env.clock.t)

    assert _login(client, "root", totp_code=code).status_code == 200
    assert _login(client, "root", totp_code=code).status_code == 401


def test_control_total_without_enrolled_factor_is_refused(client, make_user):
    make_user("root", role="control_total")

    r = _login(client, "root")

    assert r.status_code == 403 and "set-cookie" not in r.headers


# -- Logout y vida de la sesión ----------------------------------------------------------


def test_logout_revokes_the_session_server_side(client, make_user):
    make_user()
    _login(client)
    stolen = client.cookies.get("mhia_session")

    r = client.post("/api/v1/auth/logout")

    assert r.status_code == 204
    assert "max-age=0" in r.headers["set-cookie"].lower()
    assert client.get("/api/v1/me").status_code == 401
    # Aunque alguien conserve la cookie robada, ya no sirve.
    other = TestClient(create_app(allowed_hosts=["testserver"]), cookies={"mhia_session": stolen})
    assert other.get("/api/v1/me").status_code == 401


def test_logout_without_a_session_is_harmless(client):
    assert client.post("/api/v1/auth/logout").status_code == 204


def test_idle_session_is_rejected(client, auth_env, make_user):
    make_user()
    _login(client)

    auth_env.clock.advance(1801)

    assert client.get("/api/v1/me").status_code == 401


def test_tampered_or_oversized_cookies_are_rejected(client, make_user):
    make_user()
    _login(client)
    real = client.cookies.get("mhia_session")

    for bad in (real[:-1] + ("A" if real[-1] != "A" else "B"), "x" * 300, "", "../../etc/passwd"):
        other = TestClient(create_app(allowed_hosts=["testserver"]), cookies={"mhia_session": bad})
        assert other.get("/api/v1/me").status_code == 401


def test_disabling_the_user_ends_the_session_immediately(client, auth_env, make_user):
    user = make_user()
    _login(client)

    auth_env.store.set_active(user.id, False)

    assert client.get("/api/v1/me").status_code == 401


def test_each_login_rotates_the_session_token(client, make_user):
    make_user()
    _login(client)
    first = client.cookies.get("mhia_session")
    _login(client)

    assert client.cookies.get("mhia_session") != first


# -- CSRF -------------------------------------------------------------------------------


def test_cross_origin_post_is_blocked(client, make_user):
    make_user()

    r = client.post(
        LOGIN,
        json={"username": "ana", "password": PASSWORD},
        headers={"Origin": "https://evil.example"},
    )

    assert r.status_code == 403 and "set-cookie" not in r.headers


def test_cross_site_fetch_metadata_without_origin_is_blocked(client, make_user):
    make_user()

    r = client.post(
        LOGIN,
        json={"username": "ana", "password": PASSWORD},
        headers={"Sec-Fetch-Site": "cross-site"},
    )

    assert r.status_code == 403


def test_same_origin_post_is_allowed(client, make_user):
    make_user()

    r = client.post(
        LOGIN,
        json={"username": "ana", "password": PASSWORD},
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    )

    assert r.status_code == 200


def test_logout_cannot_be_forced_cross_origin(client, make_user):
    make_user()
    _login(client)

    r = client.post("/api/v1/auth/logout", headers={"Origin": "https://evil.example"})

    assert r.status_code == 403
    assert client.get("/api/v1/me").status_code == 200


@pytest.mark.parametrize(
    ("method", "origin", "fetch_site", "expected"),
    [
        ("GET", "https://evil.example", None, True),  # los GET no cambian estado
        ("POST", None, None, True),  # cliente que no es navegador
        ("POST", "https://mhia.ts.net", None, True),
        ("POST", "https://mhia.ts.net:8443", None, True),
        ("POST", "https://evil.example", None, False),
        ("POST", "null", None, False),
        ("POST", "https://mhia.ts.net.evil.example", None, False),
        ("POST", "not a url", None, False),
        ("DELETE", None, "cross-site", False),
        ("PUT", None, "same-site", False),
        ("POST", None, "same-origin", True),
        ("POST", None, "none", True),
    ],
)
def test_origin_rules(method, origin, fetch_site, expected):
    assert request_origin_ok(method, origin, fetch_site, ["mhia.ts.net", "localhost"]) is expected


def test_wildcard_hosts_match_one_suffix_only():
    assert host_allowed("a.example.com", ["*.example.com"])
    assert not host_allowed("example.com.evil.net", ["*.example.com"])


# -- Errores cerrados y registros ---------------------------------------------------------


def test_a_database_failure_denies_access_instead_of_opening_it(client, make_user, monkeypatch):
    make_user()
    _login(client)

    def boom(_token):
        raise sqlite3.OperationalError("disco lleno")

    monkeypatch.setattr(auth, "resolve_session", boom)

    r = client.get("/api/v1/me")
    assert r.status_code == 503 and "disco" not in r.text


def test_login_database_failure_returns_503(client, make_user, monkeypatch):
    make_user()

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("disco lleno")

    monkeypatch.setattr(auth, "authenticate", boom)

    assert _login(client).status_code == 503


def test_missing_session_secret_fails_closed(client, make_user, monkeypatch):
    make_user()
    monkeypatch.setattr(settings, "api_session_secret", "")

    assert _login(client).status_code == 503


def test_logs_never_contain_passwords_codes_or_tokens(client, auth_env, make_user, caplog):
    secret = totp.generate_secret()
    make_user("root", role="control_total", totp_secret=secret)
    code = totp.totp(secret, auth_env.clock.t)
    secret_password = "esta-contraseña-es-secreta"

    with caplog.at_level(logging.DEBUG):
        _login(client, "root", password=secret_password)
        _login(client, "root", totp_code=code)
        token = client.cookies.get("mhia_session")
        client.post("/api/v1/auth/logout")

    assert secret_password not in caplog.text
    assert code not in caplog.text and PASSWORD not in caplog.text
    assert token and token not in caplog.text
    assert "login_ok" in caplog.text and "login_failed" in caplog.text
