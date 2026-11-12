"""Bloque 5: segundo factor obligatorio por nivel de área, activación desde la GUI dentro de
una ventana que abre el administrador, y la lista de sesiones propias."""

import base64
import sqlite3

import psycopg
import pytest
from fastapi.testclient import TestClient

from pipeline_ai.api.main import create_app
from pipeline_ai.config import settings
from pipeline_ai.privacy.sensitivity import Sensitivity
from pipeline_ai.security import auth, secretbox, totp
from pipeline_ai.security.areas import Area
from pipeline_ai.security.auth_store import SCHEMA_VERSION, PostgresAuthStore, SqliteAuthStore
from tests.conftest import BACKENDS, PASSWORD, PG_TEST, db_count, dump_db
from tests.test_auth_cli import IO

LOGIN = "/api/v1/auth/login"
ENROLL = "/api/v1/auth/totp/enroll"
SESSIONS = "/api/v1/auth/sessions"
CLOSE_OTHERS = "/api/v1/auth/sessions/close-others"
ME = "/api/v1/me"
INT, C, R = Sensitivity.INTERNAL, Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED
CHROME_WIN = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/130.0.0.0 Safari/537.36"
)


@pytest.fixture
def app(auth_env):
    return create_app(allowed_hosts=["testserver"])


@pytest.fixture
def client(app):
    return TestClient(app)


def _login(client, username="ana", password=PASSWORD, **extra):
    return client.post(LOGIN, json={"username": username, "password": password, **extra})


def _code(secret, auth_env):
    return totp.totp(secret, auth_env.clock.t)


def _open_window(auth_env, user, hours=72):
    auth_env.store.set_totp_enroll_window(user.id, auth_env.clock.t + hours * 3600)


def _access(store):
    return [e.action for e in reversed(store.list_events(category="acceso"))]


# -- Política: quién necesita segundo factor ------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "memberships", "expected"),
    [
        ("CONFIDENTIAL", {}, False),
        ("CONFIDENTIAL", {Area.GENERAL: INT}, False),
        ("CONFIDENTIAL", {Area.GENERAL: INT, Area.TALLER: C}, True),
        ("CONFIDENTIAL", {Area.RH: R}, True),
        ("RESTRICTED", {Area.TALLER: C}, False),
        ("RESTRICTED", {Area.RH: R}, True),
        ("INTERNAL", {Area.GENERAL: INT}, True),
    ],
)
def test_the_required_level_decides_who_needs_a_second_factor(
    auth_env, make_user, monkeypatch, setting, memberships, expected
):
    monkeypatch.setattr(settings, "mfa_required_level", setting)
    user = make_user()
    auth_env.store.set_memberships(user.id, memberships)

    assert auth.requires_mfa(auth_env.store.get_user(user.id)) is expected


def test_control_total_and_anyone_already_enrolled_always_need_it(auth_env, make_user):
    root = make_user("root", role="control_total")
    ana = make_user("ana", totp_secret=totp.generate_secret())

    assert auth.requires_mfa(root, {})
    assert auth.requires_mfa(auth_env.store.get_user(ana.id), {})


def test_an_invalid_level_is_a_configuration_error_not_a_guess(
    client, auth_env, make_user, monkeypatch
):
    make_user()
    monkeypatch.setattr(settings, "mfa_required_level", "MEDIO")

    with pytest.raises(auth.AuthConfigError):
        auth.mfa_level()
    with pytest.raises(RuntimeError, match="MFA_REQUIRED_LEVEL"):
        settings.require_mfa_level()
    assert _login(client).status_code == 503


def test_the_api_refuses_to_start_with_an_invalid_level(auth_env, monkeypatch):
    monkeypatch.setattr(settings, "mfa_required_level", "MEDIO")

    with pytest.raises(RuntimeError, match="MFA_REQUIRED_LEVEL"):
        with TestClient(create_app(allowed_hosts=["testserver"])):
            pass


def test_without_a_window_a_confidential_user_is_told_to_ask_the_admin(client, auth_env, make_user):
    ana = make_user()
    auth_env.store.set_memberships(ana.id, {Area.TALLER: C})

    reply = _login(client)

    assert reply.status_code == 403
    assert "administrador" in reply.json()["detail"]
    assert _access(auth_env.store) == ["login_mfa_not_enrolled"]


def test_a_session_without_mfa_stops_working_when_the_person_gains_a_confidential_area(
    client, auth_env, make_user
):
    ana = make_user()
    assert _login(client).status_code == 200
    assert client.get(ME).status_code == 200

    auth_env.store.set_memberships(ana.id, {Area.COMERCIAL: C})

    assert client.get(ME).status_code == 401
    assert client.get(SESSIONS).status_code == 401


def test_a_session_with_mfa_keeps_working_after_gaining_a_confidential_area(
    client, auth_env, make_user
):
    secret = totp.generate_secret()
    ana = make_user(totp_secret=secret)
    assert _login(client, totp_code=_code(secret, auth_env)).status_code == 200

    auth_env.store.set_memberships(ana.id, {Area.COMERCIAL: C})

    assert client.get(ME).status_code == 200


# -- Activación desde la GUI ---------------------------------------------------------------


def _start(client, auth_env, make_user, *, areas=None):
    ana = make_user()
    auth_env.store.set_memberships(ana.id, areas or {Area.TALLER: C})
    _open_window(auth_env, ana)
    reply = _login(client)
    assert reply.status_code == 401, reply.text
    return ana, reply


def test_inside_the_window_the_login_offers_the_qr_and_sets_a_separate_cookie(
    client, auth_env, make_user
):
    ana, reply = _start(client, auth_env, make_user)

    body = reply.json()["mfa_enrollment"]
    assert body["otpauth_uri"].startswith("otpauth://totp/MH%20IA%3Aana?secret=" + body["secret"])
    assert body["qr"].startswith("data:image/svg+xml;base64,")
    svg = base64.b64decode(body["qr"].split(",", 1)[1]).decode()
    # Como <img>, el SVG solo se pinta si declara su espacio de nombres.
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg"') and body["secret"] not in svg
    assert body["expires_in"] == settings.totp_enroll_ttl_s
    cookie = reply.headers["set-cookie"]
    assert cookie.startswith("mhia_enroll=") and "HttpOnly" in cookie
    assert "samesite=strict" in cookie.lower()
    assert "mhia_session" not in cookie
    assert client.get(ME).status_code == 401  # todavía sin sesión
    # El secreto pendiente se guarda cifrado; ni él ni el token aparecen en claro.
    dump = dump_db(auth_env.store)
    assert body["secret"] not in dump
    assert client.cookies.get("mhia_enroll") not in dump
    assert secretbox.decrypt(auth_env.store.get_user(ana.id).totp_secret_enc) == body["secret"]
    assert _access(auth_env.store) == ["totp_activacion_iniciada"]


def test_the_right_code_activates_the_factor_and_opens_a_session_with_mfa(
    client, auth_env, make_user
):
    ana, reply = _start(client, auth_env, make_user)
    secret = reply.json()["mfa_enrollment"]["secret"]

    done = client.post(ENROLL, json={"totp_code": _code(secret, auth_env)})

    assert done.status_code == 200, done.text
    assert done.json()["username"] == "ana"
    assert client.get(ME).status_code == 200
    assert 'mhia_enroll=""' in done.headers["set-cookie"]  # se borra la cookie de activación
    user = auth_env.store.get_user(ana.id)
    assert user.totp_enabled and user.totp_enroll_until is None
    assert db_count(auth_env.store, "totp_enrollments") == 0
    [session] = client.get(SESSIONS).json()
    assert session["mfa"] is True
    assert _access(auth_env.store) == ["totp_activacion_iniciada", "totp_activado", "login_ok"]

    # La próxima vez la contraseña sola no basta, y el mismo código no se puede repetir.
    other = TestClient(client.app)
    assert _login(other).json() == {
        "detail": "Se requiere el código de verificación.",
        "mfa_required": True,
    }
    assert _login(other, totp_code=_code(secret, auth_env)).status_code == 401
    auth_env.clock.advance(30)
    assert _login(other, totp_code=_code(secret, auth_env)).status_code == 200


def test_a_wrong_code_is_rejected_and_repeated_failures_lock_the_activation(
    client, auth_env, make_user
):
    _start(client, auth_env, make_user)

    for _ in range(settings.login_max_failures):
        assert client.post(ENROLL, json={"totp_code": "000000"}).status_code == 400
    locked = client.post(ENROLL, json={"totp_code": "000000"})

    assert locked.status_code == 429 and int(locked.headers["Retry-After"]) > 0
    assert _access(auth_env.store).count("totp_activacion_fallida") == settings.login_max_failures


def test_the_activation_expires_after_its_ttl(client, auth_env, make_user):
    _, reply = _start(client, auth_env, make_user)
    secret = reply.json()["mfa_enrollment"]["secret"]

    auth_env.clock.advance(settings.totp_enroll_ttl_s + 1)

    assert client.post(ENROLL, json={"totp_code": _code(secret, auth_env)}).status_code == 410


def test_closing_the_window_cancels_an_activation_in_progress(client, auth_env, make_user):
    ana, reply = _start(client, auth_env, make_user)
    secret = reply.json()["mfa_enrollment"]["secret"]

    auth_env.store.set_totp_enroll_window(ana.id, None)

    assert client.post(ENROLL, json={"totp_code": _code(secret, auth_env)}).status_code == 410
    assert not auth_env.store.get_user(ana.id).totp_enabled


def test_a_new_login_replaces_the_previous_activation(client, auth_env, make_user):
    _, first = _start(client, auth_env, make_user)
    old_cookie = client.cookies.get("mhia_enroll")
    second = _login(client).json()["mfa_enrollment"]["secret"]

    stale = TestClient(client.app, cookies={"mhia_enroll": old_cookie})
    code = _code(second, auth_env)
    assert stale.post(ENROLL, json={"totp_code": code}).status_code == 410
    assert client.post(ENROLL, json={"totp_code": code}).status_code == 200
    assert first.json()["mfa_enrollment"]["secret"] != second


def test_enroll_without_cookie_or_with_a_malformed_code(client, auth_env):
    assert client.post(ENROLL, json={"totp_code": "123456"}).status_code == 410
    assert client.post(ENROLL, json={"totp_code": "12ab56"}).status_code == 422


def test_a_wrong_password_never_starts_an_activation(client, auth_env, make_user):
    ana = make_user()
    auth_env.store.set_memberships(ana.id, {Area.TALLER: C})
    _open_window(auth_env, ana)

    reply = _login(client, password="mala")

    assert reply.status_code == 401 and "mfa_enrollment" not in reply.json()
    assert db_count(auth_env.store, "totp_enrollments") == 0


def test_the_window_closes_on_its_own_after_its_hours(client, auth_env, make_user):
    ana = make_user()
    auth_env.store.set_memberships(ana.id, {Area.TALLER: C})
    _open_window(auth_env, ana, hours=1)

    auth_env.clock.advance(3601)

    assert _login(client).status_code == 403


def test_the_enroll_endpoint_checks_the_origin(client, auth_env):
    reply = client.post(
        ENROLL, json={"totp_code": "123456"}, headers={"Origin": "https://otro.example"}
    )
    assert reply.status_code == 403


# -- Mis sesiones --------------------------------------------------------------------------


def _two_sessions(app, auth_env, make_user):
    make_user()
    laptop = TestClient(app, headers={"User-Agent": CHROME_WIN})
    phone = TestClient(app, headers={"User-Agent": "Mozilla/5.0 (iPhone) Safari/604.1"})
    assert _login(laptop).status_code == 200
    auth_env.clock.advance(60)
    assert _login(phone).status_code == 200
    return laptop, phone


def test_the_list_shows_my_sessions_with_device_and_which_one_is_this(app, auth_env, make_user):
    laptop, phone = _two_sessions(app, auth_env, make_user)
    make_user("beto")
    assert _login(TestClient(app), "beto").status_code == 200

    sessions = laptop.get(SESSIONS).json()

    assert {s["device"]: s["current"] for s in sessions} == {
        "Safari en iPhone": False,
        "Chrome en Windows": True,
    }
    assert all(len(s["id"]) == 16 and s["mfa"] is False for s in sessions)
    assert set(sessions[0]) == {
        "id",
        "device",
        "created_at",
        "last_seen",
        "expires_at",
        "mfa",
        "current",
    }
    # Ni el token ni su hash salen por la API.
    assert laptop.cookies.get("mhia_session") not in repr(sessions)


def test_closing_another_session_logs_that_device_out(app, auth_env, make_user):
    laptop, phone = _two_sessions(app, auth_env, make_user)
    phone_id = next(s["id"] for s in laptop.get(SESSIONS).json() if not s["current"])

    assert laptop.delete(f"{SESSIONS}/{phone_id}").status_code == 204

    assert phone.get(ME).status_code == 401
    assert laptop.get(ME).status_code == 200
    assert "cerrar_sesion" in _access(auth_env.store)


def test_closing_the_current_session_clears_the_cookie(app, auth_env, make_user):
    laptop, _ = _two_sessions(app, auth_env, make_user)
    mine = next(s["id"] for s in laptop.get(SESSIONS).json() if s["current"])

    reply = laptop.delete(f"{SESSIONS}/{mine}")

    assert reply.status_code == 204 and 'mhia_session=""' in reply.headers["set-cookie"]
    assert laptop.get(ME).status_code == 401


def test_someone_elses_session_looks_like_it_does_not_exist(app, auth_env, make_user):
    laptop, _ = _two_sessions(app, auth_env, make_user)
    make_user("beto")
    beto = TestClient(app)
    assert _login(beto, "beto").status_code == 200
    beto_id = beto.get(SESSIONS).json()[0]["id"]

    assert laptop.delete(f"{SESSIONS}/{beto_id}").status_code == 404
    assert laptop.delete(f"{SESSIONS}/{'0' * 16}").status_code == 404
    assert laptop.delete(f"{SESSIONS}/no-es-un-id").status_code == 422
    assert beto.get(ME).status_code == 200


def test_close_others_keeps_only_this_session(app, auth_env, make_user):
    laptop, phone = _two_sessions(app, auth_env, make_user)
    third = TestClient(app)
    assert _login(third).status_code == 200

    reply = laptop.post(CLOSE_OTHERS)

    assert reply.status_code == 200 and reply.json() == {"closed": 2}
    assert phone.get(ME).status_code == 401 and third.get(ME).status_code == 401
    assert [s["current"] for s in laptop.get(SESSIONS).json()] == [True]


def test_session_endpoints_need_a_session_and_a_same_origin_request(app, auth_env, make_user):
    laptop, _ = _two_sessions(app, auth_env, make_user)
    anonymous = TestClient(app)

    assert anonymous.get(SESSIONS).status_code == 401
    assert anonymous.post(CLOSE_OTHERS).status_code == 401
    assert anonymous.delete(f"{SESSIONS}/{'a' * 16}").status_code == 401
    evil = {"Origin": "https://otro.example"}
    assert laptop.post(CLOSE_OTHERS, headers=evil).status_code == 403
    assert laptop.delete(f"{SESSIONS}/{'a' * 16}", headers=evil).status_code == 403
    assert len(laptop.get(SESSIONS).json()) == 2


def test_expired_sessions_are_not_listed(app, auth_env, make_user):
    laptop, phone = _two_sessions(app, auth_env, make_user)
    auth_env.clock.advance(100)
    assert laptop.get(ME).status_code == 200  # la laptop sigue en uso, el teléfono no
    auth_env.clock.advance(settings.session_idle_s - 50)

    assert [s["device"] for s in laptop.get(SESSIONS).json()] == ["Chrome en Windows"]


@pytest.mark.parametrize(
    ("user_agent", "label"),
    [
        (CHROME_WIN, "Chrome en Windows"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) Safari/605.1.15", "Safari en Mac"),
        ("Mozilla/5.0 (Windows NT 10.0) Chrome/130.0 Safari/537.36 Edg/130.0", "Edge en Windows"),
        (
            "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
            "Firefox en Linux",
        ),
        ("Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Mobile Safari/537.36", "Chrome en Android"),
        ("curl/8.5.0", "Equipo desconocido"),
        ("", "Equipo desconocido"),
        (None, "Equipo desconocido"),
    ],
)
def test_device_label_is_coarse(user_agent, label):
    assert auth.device_label(user_agent) == label


# -- Comandos de administración ------------------------------------------------------------


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setenv("MHIA_ADMIN", "fernando")


def _window_open(auth_env, user):
    return auth.can_self_enroll(auth_env.store.get_user(user.id), now=auth_env.clock.t)


def test_allow_totp_enroll_opens_the_window_for_one_person(auth_env, make_user, admin):
    ana = make_user()
    io = IO()

    assert io.run("allow-totp-enroll", "--username", "ana") == 0

    assert _window_open(auth_env, ana)
    assert "debe activar su segundo factor" in io.text and "72 horas" in io.text
    event = auth_env.store.list_admin_events()[0]
    assert (event.action, event.target, event.detail) == (
        "abrir_activacion_totp",
        "ana",
        {"horas": 72.0},
    )


def test_allow_totp_enroll_refuses_enrolled_or_disabled_people(auth_env, make_user, capsys):
    make_user("ana", totp_secret=totp.generate_secret())
    make_user("beto", active=False)

    assert IO().run("allow-totp-enroll", "--username", "ana") == 1
    assert "reset-totp" in capsys.readouterr().err
    assert IO().run("allow-totp-enroll", "--username", "beto") == 1
    assert "enable-user" in capsys.readouterr().err
    with pytest.raises(SystemExit):  # falta --username o --pendientes
        IO().run("allow-totp-enroll")


def test_allow_totp_enroll_pending_opens_it_only_for_who_needs_it(auth_env, make_user):
    needs = make_user("ana")
    auth_env.store.set_memberships(needs.id, {Area.TALLER: C})
    internal = make_user("beto")
    auth_env.store.set_memberships(internal.id, {Area.GENERAL: INT})
    enrolled = make_user("carla", totp_secret=totp.generate_secret())
    auth_env.store.set_memberships(enrolled.id, {Area.RH: R})
    io = IO()

    assert io.run("allow-totp-enroll", "--pendientes") == 0

    assert _window_open(auth_env, needs)
    assert not _window_open(auth_env, internal) and not _window_open(auth_env, enrolled)
    assert "Ventanas abiertas: 1." in io.text


def test_reset_totp_removes_the_factor_closes_sessions_and_reopens_the_window(
    client, auth_env, make_user, admin
):
    secret = totp.generate_secret()
    ana = make_user(totp_secret=secret)
    auth_env.store.set_memberships(ana.id, {Area.TALLER: C})
    assert _login(client, totp_code=_code(secret, auth_env)).status_code == 200

    assert IO().run("reset-totp", "--username", "ana") == 0

    user = auth_env.store.get_user(ana.id)
    assert not user.totp_enabled and user.totp_secret_enc is None
    assert client.get(ME).status_code == 401
    assert _window_open(auth_env, ana)
    actions = [e.action for e in reversed(auth_env.store.list_admin_events())]
    assert actions == ["quitar_totp", "abrir_activacion_totp"]
    assert _login(client).json()["mfa_enrollment"]["secret"] != secret


def test_reset_totp_for_someone_who_no_longer_needs_it(auth_env, make_user):
    make_user(totp_secret=totp.generate_secret())
    io = IO()

    assert io.run("reset-totp", "--username", "ana") == 0

    assert "No lo necesita" in io.text


def test_admin_changes_that_leave_someone_without_factor_open_the_window(auth_env, make_user):
    ana = make_user("ana")
    assert IO().run("set-areas", "--username", "ana", "--areas", "taller:CONFIDENTIAL") == 0
    assert _window_open(auth_env, ana)

    beto = make_user("beto")
    assert IO().run("set-areas", "--username", "beto", "--areas", "general:INTERNAL") == 0
    assert not _window_open(auth_env, beto)

    carla = make_user("carla")
    assert IO().run("set-role", "--username", "carla", "--role", "control_total") == 0
    assert _window_open(auth_env, carla)

    assert IO().run("disable-user", "--username", "ana") == 0
    assert not _window_open(auth_env, ana)
    assert IO().run("enable-user", "--username", "ana") == 0
    assert _window_open(auth_env, ana)


def test_create_user_with_a_confidential_area_opens_the_window(auth_env):
    io = IO(passwords=[PASSWORD, PASSWORD])

    assert (
        io.run("create-user", "--username", "dora", "--role", "lider", "--areas", "rh:RESTRICTED")
        == 0
    )

    assert "debe activar su segundo factor" in io.text
    assert auth.authenticate("dora", PASSWORD).status == "mfa_enrollment"


def test_list_users_shows_the_state_of_each_second_factor(auth_env, make_user):
    make_user("ana", totp_secret=totp.generate_secret())
    pending = make_user("beto")
    auth_env.store.set_memberships(pending.id, {Area.TALLER: C})
    _open_window(auth_env, pending)
    missing = make_user("carla")
    auth_env.store.set_memberships(missing.id, {Area.TALLER: C})
    make_user("dora")
    io = IO()

    assert io.run("list-users") == 0

    states = {line.split()[0]: line.split()[3] for line in io.lines[1:]}
    assert states == {"ana": "sí", "beto": "pendiente", "carla": "FALTA", "dora": "no"}


# -- Migración al esquema 4 ----------------------------------------------------------------


def _downgrade_sqlite(path):
    """Deja una base creada hoy como estaba en el esquema 3 (sin ventana ni id de sesión)."""
    with sqlite3.connect(path) as conn:
        conn.execute("DROP INDEX sessions_public_id")
        conn.execute("ALTER TABLE sessions DROP COLUMN public_id")
        conn.execute("ALTER TABLE sessions DROP COLUMN device")
        conn.execute("ALTER TABLE users DROP COLUMN totp_enroll_until")
        conn.execute("PRAGMA user_version = 3")


def test_a_schema_3_sqlite_database_gains_the_new_columns_and_closes_sessions(
    tmp_path, monkeypatch
):
    from pipeline_ai.security import passwords

    path = tmp_path / "auth.db"
    store = SqliteAuthStore(path)
    user = store.create_user("ana", passwords.hash_password(PASSWORD), "lider", now=1.0)
    store.create_session("h" * 64, user.id, mfa=False, now=1.0, absolute_s=100)
    _downgrade_sqlite(path)

    store = SqliteAuthStore(path)

    assert db_count(store, "sessions") == 0
    assert store.get_user(user.id).totp_enroll_until is None
    assert store.create_session("i" * 64, user.id, mfa=False, now=2.0, absolute_s=100)
    with store.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


@pytest.mark.skipif("postgres" not in BACKENDS, reason="sin Postgres de pruebas")
def test_a_schema_3_postgres_database_gains_the_new_columns_and_closes_sessions(pg_schema):
    from pipeline_ai.security import passwords

    store = PostgresAuthStore(**PG_TEST, schema=pg_schema)
    user = store.create_user("ana", passwords.hash_password(PASSWORD), "lider", now=1.0)
    store.create_session("h" * 64, user.id, mfa=False, now=1.0, absolute_s=100)
    store.close()
    with psycopg.connect(**PG_TEST, autocommit=True) as conn:
        conn.execute(f"SET search_path TO {pg_schema}")
        conn.execute("DROP INDEX sessions_public_id")
        conn.execute("ALTER TABLE sessions DROP COLUMN public_id, DROP COLUMN device")
        conn.execute("ALTER TABLE users DROP COLUMN totp_enroll_until")
        conn.execute("UPDATE schema_meta SET version = 3")

    store = PostgresAuthStore(**PG_TEST, schema=pg_schema)
    try:
        assert db_count(store, "sessions") == 0
        assert store.get_user(user.id).totp_enroll_until is None
        assert store.create_session("i" * 64, user.id, mfa=False, now=2.0, absolute_s=100)
        with store.connection() as conn:
            version = conn.execute("SELECT version FROM schema_meta").fetchone()["version"]
        assert version == SCHEMA_VERSION
    finally:
        store.close()
