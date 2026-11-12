"""Registro de auditoría con hash encadenado: integridad, migración desde `admin_audit`,
respaldos y verificación desde la CLI."""

import json
import sqlite3
import threading

import psycopg
import pytest

from pipeline_ai.security.auth_store import (
    AUDIT_CATEGORIES,
    GENESIS_HASH,
    ImportDataError,
    PostgresAuthStore,
    SqliteAuthStore,
    audit_row_hash,
)
from tests.conftest import BACKENDS, PG_TEST
from tests.test_auth_cli import IO


def _fill(store, n=3):
    for i in range(n):
        store.record_event("consulta", f"user:u{i}", "consulta", f"s{i}", {"n": i, "texto": "ñ"})


def _tamper(store, sql):
    """Altera la tabla saltándose los disparadores, como haría quien administra la base."""
    with store.connection() as conn:
        if store.backend == "sqlite":
            conn.execute("DROP TRIGGER audit_log_no_update")
            conn.execute("DROP TRIGGER audit_log_no_delete")
        else:
            conn.execute("ALTER TABLE audit_log DISABLE TRIGGER USER")
        conn.execute(sql)


# -- La cadena ----------------------------------------------------------------------------


def test_each_row_is_chained_to_the_previous_one(auth_env):
    store = auth_env.store
    _fill(store, 3)

    with store.connection() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()]
    assert [r["id"] for r in rows] == [1, 2, 3]
    assert rows[0]["prev_hash"] == GENESIS_HASH
    for prev, row in zip(rows, rows[1:], strict=False):
        assert row["prev_hash"] == prev["row_hash"]
    first = rows[0]
    assert first["row_hash"] == audit_row_hash(
        GENESIS_HASH,
        1,
        first["at"],
        "consulta",
        "user:u0",
        "consulta",
        "s0",
        first["detail"],
    )
    check = store.verify_audit_chain()
    assert check.ok and check.rows == 3 and check.head_id == 3
    assert check.head_hash == rows[-1]["row_hash"]


def test_an_empty_log_is_intact(auth_env):
    check = auth_env.store.verify_audit_chain()
    assert check.ok and check.rows == 0 and check.head_hash == GENESIS_HASH


@pytest.mark.parametrize(
    ("sql", "broken"),
    [
        ("UPDATE audit_log SET actor = 'otro' WHERE id = 2", 2),
        ("UPDATE audit_log SET detail = '{}' WHERE id = 3", 3),
        ("DELETE FROM audit_log WHERE id = 2", 3),
    ],
)
def test_altering_or_deleting_a_row_breaks_the_chain(auth_env, sql, broken):
    store = auth_env.store
    _fill(store, 4)

    _tamper(store, sql)

    check = store.verify_audit_chain()
    assert not check.ok and check.broken_at == broken


def test_verification_works_across_batches(auth_env):
    store = auth_env.store
    _fill(store, 7)
    assert store.verify_audit_chain(batch=2).rows == 7
    _tamper(store, "UPDATE audit_log SET action = 'x' WHERE id = 6")
    assert store.verify_audit_chain(batch=2).broken_at == 6


def test_unknown_categories_are_rejected(auth_env):
    with pytest.raises(ValueError, match="categoría"):
        auth_env.store.record_event("otra", "x", "y")
    assert set(AUDIT_CATEGORIES) == {"admin", "acceso", "consulta"}


def test_concurrent_writers_keep_the_chain_intact(auth_env):
    store = auth_env.store
    errors = []

    def writer(k):
        try:
            for i in range(10):
                store.record_event("acceso", f"user:{k}", "login_ok", None, {"i": i})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    check = store.verify_audit_chain()
    assert check.ok and check.rows == 40


def test_events_can_be_listed_by_category(auth_env):
    store = auth_env.store
    store.record_admin_event("cli:x", "crear_usuario", "ana")
    store.record_event("acceso", "user:1", "login_ok")
    store.record_event("consulta", "user:1", "consulta", "s1")

    assert [e.category for e in store.list_events()] == ["consulta", "acceso", "admin"]
    assert [e.action for e in store.list_events(category="acceso")] == ["login_ok"]
    assert [e.action for e in store.list_admin_events()] == ["crear_usuario"]


# -- Respaldos ------------------------------------------------------------------------------


def test_export_carries_the_head_and_import_checks_the_chain(auth_env, tmp_path):
    store = auth_env.store
    _fill(store, 3)
    data = store.export_data()
    check = store.verify_audit_chain()
    assert data["audit_head"] == {"id": 3, "hash": check.head_hash}

    copy = SqliteAuthStore(tmp_path / "ok.db")
    assert copy.import_data(data, replace=False)["audit_log"] == 3
    assert copy.verify_audit_chain().head_hash == check.head_hash


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["audit_log"][1].update(actor="otro"),
        lambda d: d["audit_log"].pop(1),
        lambda d: d["audit_log"].pop(),  # cadena válida pero más corta que la cabeza
        lambda d: d.update(audit_head={"id": 3, "hash": "f" * 64}),
    ],
)
def test_a_tampered_backup_is_refused(auth_env, tmp_path, mutate):
    store = auth_env.store
    _fill(store, 3)
    data = json.loads(json.dumps(store.export_data()))
    mutate(data)

    with pytest.raises(ImportDataError, match="auditoría"):
        SqliteAuthStore(tmp_path / "x.db").import_data(data, replace=False)


def test_legacy_rows_with_gaps_in_their_ids_are_renumbered(tmp_path):
    # La secuencia de Postgres deja huecos si una transacción se revierte.
    legacy = {
        "format": 1,
        "users": [],
        "memberships": [],
        "admin_audit": [
            {"id": 7, "at": 3.0, "actor": "a", "action": "tres", "target": None, "detail": "{}"},
            {"id": 2, "at": 1.0, "actor": "a", "action": "uno", "target": None, "detail": "{}"},
            {"id": 4, "at": 2.0, "actor": "a", "action": "dos", "target": None, "detail": "{}"},
        ],
    }
    store = SqliteAuthStore(tmp_path / "x.db")
    store.import_data(legacy, replace=False)

    assert [(e.id, e.action) for e in reversed(store.list_events())] == [
        (1, "uno"),
        (2, "dos"),
        (3, "tres"),
    ]
    assert store.verify_audit_chain().ok


def test_a_format_1_backup_is_imported_and_chained(tmp_path):
    legacy = {
        "format": 1,
        "schema_version": 2,
        "users": [],
        "memberships": [],
        "admin_audit": [
            {
                "id": 1,
                "at": 1.5,
                "actor": "cli:f",
                "action": "crear_usuario",
                "target": "ana",
                "detail": '{"rol": "lider"}',
            },
            {
                "id": 2,
                "at": 2.5,
                "actor": "cli:f",
                "action": "activar",
                "target": "ana",
                "detail": "{}",
            },
        ],
    }
    store = SqliteAuthStore(tmp_path / "x.db")

    assert store.import_data(legacy, replace=False)["audit_log"] == 2
    assert store.verify_audit_chain().ok
    assert [e.category for e in store.list_events()] == ["admin", "admin"]


# -- Migración desde admin_audit (esquema 2) --------------------------------------------------

_V2_SQLITE = """
CREATE TABLE admin_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, actor TEXT NOT NULL,
    action TEXT NOT NULL, target TEXT, detail TEXT NOT NULL DEFAULT '{}');
CREATE TRIGGER admin_audit_no_delete BEFORE DELETE ON admin_audit
BEGIN SELECT RAISE(ABORT, 'admin_audit es de solo insercion'); END;
INSERT INTO admin_audit (at, actor, action, target, detail)
    VALUES (10.0, 'cli:f', 'crear_usuario', 'ana', '{"rol": "lider"}'),
           (20.0, 'cli:f', 'cambiar_rol', 'ana', '{}');
PRAGMA user_version = 2;
"""


def test_a_schema_2_sqlite_database_is_migrated_to_the_chained_log(tmp_path):
    path = tmp_path / "auth.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(_V2_SQLITE)

    store = SqliteAuthStore(path)

    events = store.list_admin_events()
    assert [(e.id, e.action) for e in events] == [(2, "cambiar_rol"), (1, "crear_usuario")]
    assert store.verify_audit_chain().ok
    with store.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()}
    assert "admin_audit" not in tables
    store.record_event("acceso", "user:1", "login_ok")
    assert store.verify_audit_chain().rows == 3


@pytest.mark.skipif("postgres" not in BACKENDS, reason="sin Postgres de pruebas")
def test_a_schema_2_postgres_database_is_migrated_to_the_chained_log(pg_schema):
    with psycopg.connect(**PG_TEST, autocommit=True) as conn:
        conn.execute(f"SET search_path TO {pg_schema}")
        conn.execute("CREATE TABLE schema_meta (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_meta VALUES (2)")
        conn.execute(
            "CREATE TABLE admin_audit (id BIGSERIAL PRIMARY KEY, at DOUBLE PRECISION NOT NULL,"
            " actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT,"
            " detail TEXT NOT NULL DEFAULT '{}')"
        )
        conn.execute(
            "INSERT INTO admin_audit (id, at, actor, action, target) VALUES"
            " (1, 10.0, 'cli:f', 'crear_usuario', 'ana'), (5, 20.0, 'cli:f', 'activar', 'ana')"
        )  # con un hueco, como deja una transacción revertida

    store = PostgresAuthStore(**PG_TEST, schema=pg_schema)
    try:
        assert [e.action for e in store.list_admin_events()] == ["activar", "crear_usuario"]
        assert store.verify_audit_chain().ok
        with store.connection() as conn:
            assert conn.execute("SELECT to_regclass('admin_audit') AS t").fetchone()["t"] is None
            assert conn.execute("SELECT version FROM schema_meta").fetchone()["version"] == 3
    finally:
        store.close()


# -- CLI ----------------------------------------------------------------------------------


def test_audit_verify_reports_an_intact_log(auth_env):
    _fill(auth_env.store, 2)
    io = IO()
    assert io.run("audit-verify") == 0
    assert "íntegro: 2 filas" in io.text


def test_audit_verify_fails_on_a_tampered_log(auth_env, capsys):
    store = auth_env.store
    _fill(store, 3)
    _tamper(store, "UPDATE audit_log SET target = 'x' WHERE id = 2")

    assert IO().run("audit-verify") == 1
    assert "fila 2" in capsys.readouterr().err


def test_audit_log_can_filter_by_category(auth_env):
    store = auth_env.store
    store.record_admin_event("cli:x", "crear_usuario", "ana")
    store.record_event("acceso", "user:1", "login_ok")
    io = IO()

    assert io.run("audit-log", "--categoria", "acceso") == 0
    assert "login_ok" in io.text and "crear_usuario" not in io.text


# -- audit-verify contra un respaldo ---------------------------------------------------------


def _fake_backup(root, name, head):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(
        json.dumps({"format_version": 1, "auth": {"audit_head": head}}), encoding="utf-8"
    )
    return folder


def test_audit_verify_matches_the_head_saved_by_a_backup(auth_env, tmp_path):
    store = auth_env.store
    _fill(store, 3)
    head = store.export_data()["audit_head"]
    _fake_backup(tmp_path / "b", "mhia-20261101T000000Z", {"id": 1, "hash": "x"})
    _fake_backup(tmp_path / "b", "mhia-20261112T000000Z", head)
    _fill(store, 2)  # lo posterior al respaldo no estorba
    io = IO()

    assert io.run("audit-verify", "--respaldo", str(tmp_path / "b")) == 0  # usa el último
    assert "Coincide con mhia-20261112T000000Z: la fila 3" in io.text


def test_audit_verify_detects_a_chain_rebuilt_after_the_backup(auth_env, tmp_path, capsys):
    store = auth_env.store
    _fill(store, 3)
    head = store.export_data()["audit_head"]
    folder = _fake_backup(tmp_path, "mhia-20261112T000000Z", head)
    # Quien administra la base vacía el registro y lo vuelve a llenar con una cadena válida.
    _tamper(store, "DELETE FROM audit_log")
    _fill(store, 4)
    assert store.verify_audit_chain().ok

    assert IO().run("audit-verify", "--respaldo", str(folder)) == 1
    assert "rehecho o recortado" in capsys.readouterr().err


def test_audit_verify_detects_a_truncated_tail(auth_env, tmp_path, capsys):
    store = auth_env.store
    _fill(store, 3)
    folder = _fake_backup(tmp_path, "mhia-20261112T000000Z", store.export_data()["audit_head"])
    _tamper(store, "DELETE FROM audit_log WHERE id = 3")

    assert store.verify_audit_chain().ok  # la cadena recortada sigue siendo válida...
    assert IO().run("audit-verify", "--respaldo", str(folder)) == 1  # ...pero no cuadra
    assert "fila 3" in capsys.readouterr().err


@pytest.mark.parametrize("head", [None, {"id": "x"}])
def test_audit_verify_refuses_backups_without_a_head(auth_env, tmp_path, capsys, head):
    folder = _fake_backup(tmp_path, "mhia-20261112T000000Z", head)
    assert IO().run("audit-verify", "--respaldo", str(folder)) == 1
    assert "cabeza" in capsys.readouterr().err


def test_audit_verify_reports_an_unreadable_backup_folder(auth_env, tmp_path, capsys):
    assert IO().run("audit-verify", "--respaldo", str(tmp_path / "nada")) == 1
    assert "no se pudo leer el respaldo" in capsys.readouterr().err
