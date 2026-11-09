"""Registro de cambios de permisos, exportar/importar y migración automática SQLite -> Postgres."""

import pytest

from pipeline_ai.privacy.sensitivity import Sensitivity
from pipeline_ai.security import auth
from pipeline_ai.security.areas import Area
from pipeline_ai.security.auth_store import (
    DB_ERRORS,
    ImportDataError,
    SqliteAuthStore,
    close_store,
    get_store,
)
from tests.conftest import PASSWORD, db_count
from tests.test_auth_cli import IO

C = Sensitivity.CONFIDENTIAL


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setenv("MHIA_ADMIN", "fernando")


def _actions(store):
    return [(e.action, e.target) for e in reversed(store.list_admin_events(100))]


# ── Cada cambio queda registrado ─────────────────────────────────────────


def test_every_cli_change_is_recorded_with_who_and_before_after(auth_env, make_user, admin):
    make_user("ana", role="consulta")
    run = IO(passwords=[PASSWORD, PASSWORD, "otra-clave-larga-9", "otra-clave-larga-9"]).run

    assert (
        run(
            "create-user", "--username", "beto", "--role", "lider", "--areas", "taller:CONFIDENTIAL"
        )
        == 0
    )
    assert run("reset-password", "--username", "beto") == 0
    assert IO().run("set-role", "--username", "ana", "--role", "lider") == 0
    assert IO().run("set-areas", "--username", "ana", "--areas", "comercial:CONFIDENTIAL") == 0
    assert IO().run("disable-user", "--username", "ana") == 0
    assert IO().run("enable-user", "--username", "ana") == 0
    assert IO().run("unlock", "--username", "ana") == 0
    assert IO().run("revoke-sessions", "--all") == 0

    store = auth_env.store
    assert _actions(store) == [
        ("crear_usuario", "beto"),
        ("cambiar_contrasena", "beto"),
        ("cambiar_rol", "ana"),
        ("cambiar_areas", "ana"),
        ("desactivar", "ana"),
        ("activar", "ana"),
        ("desbloquear", "ana"),
        ("cerrar_sesiones", "*"),
    ]
    events = {e.action: e for e in store.list_admin_events(100)}
    assert {e.actor for e in events.values()} == {"cli:fernando"}
    assert events["cambiar_rol"].detail == {"antes": "consulta", "despues": "lider"}
    assert events["cambiar_areas"].detail == {"antes": "", "despues": "comercial:CONFIDENTIAL"}
    assert events["crear_usuario"].detail == {"rol": "lider", "areas": "taller:CONFIDENTIAL"}
    # Nunca contraseñas ni hashes en el registro.
    dump = repr([e.detail for e in events.values()])
    assert PASSWORD not in dump and "otra-clave" not in dump and "argon2" not in dump


def test_a_failed_change_is_not_recorded(auth_env, admin):
    assert IO().run("set-areas", "--username", "nadie", "--areas", "rh:RESTRICTED") == 1
    assert IO().run("create-user", "--username", "beto", "--role", "lider", "--areas", "x:C") == 1

    assert auth_env.store.list_admin_events() == []


def test_import_records_each_created_or_updated_person(auth_env, tmp_path, admin):
    path = tmp_path / "personas.csv"
    path.write_text("Correo,Puesto\nventas@mh.com,Ventas\nceo@mh.com,CEO\n", encoding="utf-8")

    assert IO().run("import-users", "--file", str(path), "--apply") == 0
    assert IO().run("import-users", "--file", str(path), "--apply") == 0  # sin cambios

    assert _actions(auth_env.store) == [
        ("importar_crear", "ventas@mh.com"),
        ("importar_crear", "ceo@mh.com"),
    ]


def test_audit_log_command_shows_recent_changes(auth_env, make_user, admin):
    make_user("ana")
    assert IO().run("set-role", "--username", "ana", "--role", "consulta") == 0
    io = IO()

    assert io.run("audit-log", "--limit", "5") == 0

    assert "cli:fernando" in io.text and "cambiar_rol" in io.text and "despues=consulta" in io.text


def test_audit_log_is_empty_at_first(auth_env):
    io = IO()
    assert io.run("audit-log") == 0
    assert "sin cambios registrados" in io.text


def test_the_audit_table_is_append_only(auth_env):
    store = auth_env.store
    store.record_admin_event("cli:x", "prueba", "ana", {"a": 1})

    for sql in ("UPDATE admin_audit SET actor = 'otro'", "DELETE FROM admin_audit"):
        with pytest.raises(DB_ERRORS):
            with store.connection() as conn:
                conn.execute(sql)
    assert [e.actor for e in store.list_admin_events()] == ["cli:x"]


def test_actor_falls_back_to_the_system_user(auth_env, make_user, monkeypatch):
    monkeypatch.delenv("MHIA_ADMIN", raising=False)
    make_user("ana")

    assert IO().run("unlock", "--username", "ana") == 0
    assert auth_env.store.list_admin_events()[0].actor.startswith("cli:")


# ── Exportar e importar ──────────────────────────────────────────────────


def _populate(store, make_user):
    ana = make_user("ana")
    store.set_memberships(ana.id, {Area.COMERCIAL: C})
    store.record_admin_event("cli:x", "crear_usuario", "ana", {"rol": "lider"})
    store.create_session("h" * 64, ana.id, mfa=False, now=1.0, absolute_s=100)
    return ana


def test_export_and_import_round_trip(auth_env, make_user, tmp_path):
    store = auth_env.store
    ana = _populate(store, make_user)
    data = store.export_data()

    assert set(data) == {"format", "schema_version", "users", "memberships", "admin_audit"}
    target = SqliteAuthStore(tmp_path / "copia.db")
    assert target.import_data(data, replace=False) == {
        "users": 1,
        "memberships": 1,
        "admin_audit": 1,
    }

    assert target.get_user(ana.id) == store.get_user(ana.id)
    assert target.get_memberships(ana.id) == {Area.COMERCIAL: C}
    assert [e.action for e in target.list_admin_events()] == ["crear_usuario"]
    assert db_count(target, "sessions") == 0  # las sesiones no viajan


def test_import_with_replace_wipes_everything_first_and_keeps_audit_ids(auth_env, make_user):
    store = auth_env.store
    _populate(store, make_user)
    data = store.export_data()
    make_user("beto")
    store.record_admin_event("cli:x", "otro", "beto")

    assert store.import_data(data, replace=True)["users"] == 1

    assert [u.username for u in store.list_users()] == ["ana"]
    assert db_count(store, "sessions") == 0
    assert [e.action for e in store.list_admin_events()] == ["crear_usuario"]
    store.record_admin_event("cli:x", "despues", None)  # la numeración sigue sin chocar
    assert [e.action for e in store.list_admin_events()] == ["despues", "crear_usuario"]


def test_import_without_replace_refuses_a_database_with_users(auth_env, make_user):
    store = auth_env.store
    _populate(store, make_user)

    with pytest.raises(ImportDataError, match="ya tiene usuarios"):
        store.import_data(store.export_data(), replace=False)


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"format": 99},
        {"format": 1, "users": "nada"},
        {"format": 1, "users": [{"id": "x"}]},
        {"format": 1, "memberships": [{"user_id": "x", "area": "rh", "level": 3, "extra": 1}]},
    ],
)
def test_import_rejects_malformed_data(auth_env, data):
    with pytest.raises(ImportDataError):
        auth_env.store.import_data(data, replace=True)


# ── Migración automática desde el auth.db anterior ───────────────────────


def _only_postgres(env):
    if env.backend != "postgres":
        pytest.skip("la migración automática es de Postgres")


def _legacy_sqlite(path):
    from pipeline_ai.security import clock, passwords

    legacy = SqliteAuthStore(path)
    user = legacy.create_user("ana", passwords.hash_password(PASSWORD), "lider", now=clock.now())
    legacy.set_memberships(user.id, {Area.TALLER: C})
    legacy.record_admin_event("cli:viejo", "crear_usuario", "ana")
    legacy.create_session("h" * 64, user.id, mfa=False, now=clock.now(), absolute_s=100)
    return user


def test_postgres_imports_the_old_sqlite_once_and_archives_it(auth_env, tmp_path):
    from pipeline_ai.config import settings

    _only_postgres(auth_env)
    close_store()
    legacy_path = tmp_path / "legacy.db"
    user = _legacy_sqlite(legacy_path)
    settings.auth_db_path = str(legacy_path)  # monkeypatch de auth_env lo restaura

    store = get_store()

    assert store.backend == "postgres"
    assert store.get_memberships(user.id) == {Area.TALLER: C}
    assert auth.authenticate("ana", PASSWORD).status == "ok"  # misma contraseña
    assert db_count(store, "sessions") == 1  # solo la del login de arriba
    actions = [e.action for e in reversed(store.list_admin_events())]
    assert actions == ["crear_usuario", "migracion_desde_sqlite"]
    assert not legacy_path.exists()
    assert len(list(tmp_path.glob("legacy.db.migrado-*"))) == 1

    close_store()  # reabrir no vuelve a importar
    assert [u.username for u in get_store().list_users()] == ["ana"]


def test_postgres_with_users_never_imports_the_old_file(auth_env, tmp_path, make_user):
    from pipeline_ai.config import settings

    _only_postgres(auth_env)
    make_user("beto")
    close_store()
    legacy_path = tmp_path / "legacy.db"
    _legacy_sqlite(legacy_path)
    settings.auth_db_path = str(legacy_path)

    store = get_store()

    assert [u.username for u in store.list_users()] == ["beto"]
    assert legacy_path.exists()  # se deja para revisarlo a mano


def test_cli_reports_a_database_outage_without_a_traceback(auth_env, monkeypatch, capsys):
    import psycopg

    def boom(*_a, **_k):
        raise psycopg.OperationalError("conexión rechazada a 10.0.0.5")

    monkeypatch.setattr(type(auth_env.store), "list_users", boom)

    assert IO().run("list-users") == 1
    err = capsys.readouterr().err
    assert "base de usuarios" in err and "10.0.0.5" not in err


def test_the_api_opens_the_user_database_at_startup(auth_env):
    from fastapi.testclient import TestClient

    from pipeline_ai.api.main import create_app

    close_store()
    with TestClient(create_app(allowed_hosts=["testserver"])) as client:
        assert client.get("/health").status_code == 200
        assert get_store().backend == auth_env.backend


def test_the_store_recovers_after_postgres_drops_its_connections(auth_env, make_user):
    """Reinicio de Postgres simulado: se matan las conexiones del pool y la siguiente
    operación debe funcionar sin error (el pool valida cada conexión al sacarla)."""
    if auth_env.backend != "postgres":
        pytest.skip("solo aplica a Postgres")
    import psycopg

    from tests.conftest import PG_TEST

    make_user("ana")
    with psycopg.connect(**PG_TEST, autocommit=True) as admin:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
            " WHERE application_name = 'mhia-auth' AND pid <> pg_backend_pid()"
        )

    assert [u.username for u in auth_env.store.list_users()] == ["ana"]
