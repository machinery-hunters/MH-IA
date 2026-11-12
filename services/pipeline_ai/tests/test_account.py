"""Eventos de acceso en el registro encadenado y cambio de contraseña por la propia persona."""

import pytest
from fastapi.testclient import TestClient

from pipeline_ai.api.main import create_app
from pipeline_ai.security import auth, totp
from tests.conftest import PASSWORD, dump_db

LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
CHANGE = "/api/v1/auth/password"
NEW = "otra-clave-larga-9"


@pytest.fixture
def client(auth_env):
    return TestClient(create_app(allowed_hosts=["testserver"]))


def _login(client, username="ana", password=PASSWORD, **extra):
    return client.post(LOGIN, json={"username": username, "password": password, **extra})


def _change(client, current=PASSWORD, new=NEW):
    return client.post(CHANGE, json={"current_password": current, "new_password": new})


def _access(store):
    return [(e.actor, e.action, e.detail) for e in reversed(store.list_events(category="acceso"))]


# -- Eventos de acceso ----------------------------------------------------------------------


def test_login_and_logout_are_recorded_with_the_person(client, auth_env, make_user):
    ana = make_user()

    assert _login(client).status_code == 200
    assert client.post(LOGOUT).status_code == 204

    assert _access(auth_env.store) == [
        (f"user:{ana.id}", "login_ok", {"role": "lider", "mfa": False}),
        (f"user:{ana.id}", "logout", {}),
    ]
    assert auth_env.store.verify_audit_chain().ok


def test_failed_and_blocked_logins_are_recorded_without_the_typed_name(client, auth_env, make_user):
    make_user()
    typed = "Ana-Con-Su-Clave-Por-Error"

    for _ in range(5):
        assert _login(client, typed, "mala").status_code == 401
    assert _login(client, typed, "mala").status_code == 429

    events = _access(auth_env.store)
    assert [a for _, a, _ in events] == ["login_failed"] * 5 + ["login_blocked"]
    assert {actor for actor, _, _ in events} == {"anonimo"}
    assert events[0][2] == {
        "reason": "unknown_or_inactive",
        "username_hash": events[0][2]["username_hash"],
    }
    assert len(events[0][2]["username_hash"]) == 12
    assert typed.lower() not in dump_db(auth_env.store).lower()


def test_bad_password_and_missing_second_factor_are_recorded(client, auth_env, make_user):
    make_user("ana")
    root = make_user("root", role="control_total")

    assert _login(client, "ana", "mala").status_code == 401
    assert _login(client, "root").status_code == 403

    events = _access(auth_env.store)
    assert events[0][1:] == (
        "login_failed",
        {"reason": "bad_password", "username_hash": events[0][2]["username_hash"]},
    )
    assert events[1] == (f"user:{root.id}", "login_mfa_not_enrolled", {})


def test_a_forged_cookie_on_logout_leaves_no_event(client, auth_env):
    client.cookies.set("mhia_session", "inventada")

    assert client.post(LOGOUT).status_code == 204
    assert _access(auth_env.store) == []


def test_no_session_is_issued_when_the_event_cannot_be_recorded(
    client, auth_env, make_user, monkeypatch
):
    from tests.conftest import db_count

    make_user()

    def boom(*_a, **_k):
        raise auth_env.store._integrity_errors[0]("disco lleno")

    monkeypatch.setattr(type(auth_env.store), "record_event", boom)
    with pytest.raises(Exception, match="disco lleno"):
        auth.authenticate("ana", PASSWORD)
    assert db_count(auth_env.store, "sessions") == 0


def test_login_answers_503_when_the_audit_log_is_unwritable(
    client, make_user, monkeypatch, auth_env
):
    import sqlite3

    make_user()

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(type(auth_env.store), "record_event", boom)

    r = _login(client)
    assert r.status_code == 503 and "set-cookie" not in r.headers


# -- Cambio de contraseña -------------------------------------------------------------------


def test_changing_the_password_closes_other_sessions_and_keeps_this_one(
    client, auth_env, make_user
):
    ana = make_user()
    other = TestClient(create_app(allowed_hosts=["testserver"]))
    assert _login(other).status_code == 200
    assert _login(client).status_code == 200
    old_cookie = client.cookies.get("mhia_session")

    r = _change(client)

    assert r.status_code == 204
    assert client.cookies.get("mhia_session") not in (None, old_cookie)
    assert client.get("/api/v1/me").status_code == 200  # esta sesión sigue, con cookie nueva
    assert other.get("/api/v1/me").status_code == 401  # las demás se cerraron
    assert _login(TestClient(create_app(allowed_hosts=["testserver"]))).status_code == 401
    assert (
        _login(TestClient(create_app(allowed_hosts=["testserver"])), password=NEW).status_code
        == 200
    )
    actions = [(a, e) for _, a, e in _access(auth_env.store)]
    assert ("cambiar_contrasena", {}) in actions
    event = next(e for e in auth_env.store.list_events() if e.action == "cambiar_contrasena")
    assert event.actor == f"user:{ana.id}" and event.target == "ana"
    dump = dump_db(auth_env.store)
    assert NEW not in dump and PASSWORD not in dump


def test_a_wrong_current_password_is_refused_and_throttled(client, auth_env, make_user):
    make_user()
    assert _login(client).status_code == 200

    for _ in range(5):
        assert _change(client, current="mala").status_code == 400
    r = _change(client)

    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    events = [a for _, a, _ in _access(auth_env.store)]
    assert events.count("cambio_contrasena_fallido") == 5
    assert events[-1] == "cambio_contrasena_bloqueado"
    # El bloqueo del cambio no impide entrar: la llave es distinta a la del login.
    assert _login(TestClient(create_app(allowed_hosts=["testserver"]))).status_code == 200


@pytest.mark.parametrize(
    ("new", "message"),
    [
        ("corta", "al menos"),
        ("ana-clave-larga-123", "nombre de usuario"),
        (PASSWORD, "distinta"),
    ],
)
def test_the_new_password_must_follow_the_policy(client, make_user, new, message):
    make_user()
    assert _login(client).status_code == 200

    r = _change(client, new=new)

    assert r.status_code == 422 and message in r.json()["detail"]
    assert client.get("/api/v1/me").status_code == 200


def test_changing_the_password_requires_a_session(client, make_user):
    make_user()
    assert _change(client).status_code == 401


def test_control_total_keeps_its_second_factor_after_the_change(client, auth_env, make_user):
    secret = totp.generate_secret()
    make_user("root", role="control_total", totp_secret=secret)
    assert _login(client, "root", totp_code=totp.totp(secret, auth_env.clock.t)).status_code == 200

    assert _change(client).status_code == 204
    assert client.get("/api/v1/me").status_code == 200


def test_the_change_requires_a_same_origin_request(client, make_user):
    make_user()
    assert _login(client).status_code == 200

    r = client.post(
        CHANGE,
        json={"current_password": PASSWORD, "new_password": NEW},
        headers={"origin": "https://malo.example"},
    )

    assert r.status_code == 403
