"""Administración del acceso por área: migración de la base, membresías en sesión, CLI
(create-user --areas, set-areas, import-users) y presets por puesto."""

import csv
import sqlite3

import pytest

from pipeline_ai.nodes.outbound_node import sensitivity_for_state
from pipeline_ai.privacy.sensitivity import Sensitivity
from pipeline_ai.security import auth
from pipeline_ai.security.areas import Area
from pipeline_ai.security.auth_store import LEGACY_ROLE_MEMBERSHIPS, SCHEMA_VERSION, AuthStore
from pipeline_ai.security.presets import PRESETS, preset_for
from pipeline_ai.security.roles import Role
from tests.conftest import PASSWORD
from tests.test_auth_cli import IO

I, C, R = Sensitivity.INTERNAL, Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED  # noqa: E741


# ── Migración 0 -> 1 ─────────────────────────────────────────────────────


def _legacy_db(path):
    """Base con el esquema anterior (sin tabla de membresías, user_version 0)."""
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE users (
                id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
                role TEXT NOT NULL, totp_secret_enc TEXT, totp_enabled INTEGER NOT NULL DEFAULT 0,
                totp_last_step INTEGER, active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL, password_changed_at REAL NOT NULL);
            """)
        for uid, role in (("u1", "consulta"), ("u2", "lider"), ("u3", "control_total")):
            conn.execute(
                "INSERT INTO users (id, username, password_hash, role, created_at,"
                " password_changed_at) VALUES (?, ?, 'x', ?, 0, 0)",
                (uid, f"{role}@mh", role),
            )


def test_migration_gives_existing_users_what_their_old_role_could_see(tmp_path):
    path = tmp_path / "auth.db"
    _legacy_db(path)

    store = AuthStore(path)

    assert store.get_memberships("u1") == {Area.GENERAL: I}
    assert store.get_memberships("u2") == LEGACY_ROLE_MEMBERSHIPS["lider"]
    assert store.get_memberships("u3") == {}  # control_total ya no lee datos por su rol
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_migration_runs_once_and_does_not_undo_later_changes(tmp_path):
    path = tmp_path / "auth.db"
    _legacy_db(path)
    store = AuthStore(path)
    store.set_memberships("u1", {Area.MARKETING: C})

    reopened = AuthStore(path)

    assert reopened.get_memberships("u1") == {Area.MARKETING: C}


def test_a_new_database_starts_at_the_current_version_without_memberships(auth_env, make_user):
    user = make_user()

    assert auth_env.store.get_memberships(user.id) == {}
    with sqlite3.connect(auth_env.store.path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


# ── Membresías en la base y en la sesión ─────────────────────────────────


def test_set_memberships_replaces_and_cascades(auth_env, make_user):
    store = auth_env.store
    user = make_user()
    store.set_memberships(user.id, {Area.COMERCIAL: C, Area.GENERAL: I})
    store.set_memberships(user.id, {Area.RH: R})

    assert store.get_memberships(user.id) == {Area.RH: R}

    with sqlite3.connect(store.path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("DELETE FROM users WHERE id = ?", (user.id,))
        assert conn.execute("SELECT COUNT(*) FROM memberships").fetchone()[0] == 0


def test_corrupt_rows_in_the_database_are_ignored(auth_env, make_user):
    store = auth_env.store
    user = make_user()
    with sqlite3.connect(store.path) as conn:
        conn.executemany(
            "INSERT INTO memberships (user_id, area, level) VALUES (?, ?, ?)",
            [(user.id, "ventas", 2), (user.id, "rh", 9), (user.id, "taller", 2)],
        )

    assert store.get_memberships(user.id) == {Area.TALLER: C}


def test_the_session_carries_the_memberships_from_the_database(auth_env, make_user):
    user = make_user()
    auth_env.store.set_memberships(user.id, {Area.LOGISTICA: C})

    login = auth.authenticate("ana", PASSWORD)
    info = auth.resolve_session(login.token)

    assert info.memberships == {Area.LOGISTICA: C}


# ── CLI ──────────────────────────────────────────────────────────────────


def test_create_user_with_areas(auth_env):
    io = IO(passwords=[PASSWORD, PASSWORD])

    assert io.run("create-user", "--username", "beto", "--role", "lider", "--areas",
                  "comercial:CONFIDENTIAL, inventario:INTERNAL") == 0  # fmt: skip

    user = auth_env.store.get_user_by_username("beto")
    assert auth_env.store.get_memberships(user.id) == {Area.COMERCIAL: C, Area.INVENTARIO: I}
    assert "no alcanza el mínimo" in io.text and "inventario" in io.text


def test_create_user_rejects_invalid_areas_before_asking_for_a_password(auth_env, capsys):
    io = IO()  # sin contraseñas guionadas: si las pidiera, fallaría con IndexError

    assert (
        io.run("create-user", "--username", "beto", "--role", "lider", "--areas", "ventas:C") == 1
    )
    assert auth_env.store.get_user_by_username("beto") is None


def test_set_areas_replaces_them_and_closes_sessions(auth_env, make_user):
    user = make_user()
    auth_env.store.set_memberships(user.id, {Area.COMERCIAL: C})
    token = auth.authenticate("ana", PASSWORD).token

    assert IO().run("set-areas", "--username", "ana", "--areas", "rh:RESTRICTED") == 0

    assert auth.resolve_session(token) is None
    assert auth_env.store.get_memberships(user.id) == {Area.RH: R}


def test_set_areas_with_an_empty_list_removes_all_access(auth_env, make_user):
    user = make_user()
    auth_env.store.set_memberships(user.id, {Area.COMERCIAL: C})
    io = IO()

    assert io.run("set-areas", "--username", "ana", "--areas", "") == 0

    assert auth_env.store.get_memberships(user.id) == {}
    assert "no verá ningún documento" in io.text


def test_list_users_shows_areas(auth_env, make_user):
    user = make_user()
    auth_env.store.set_memberships(user.id, {Area.TALLER: C})
    io = IO()

    assert io.run("list-users") == 0
    assert "taller:CONFIDENTIAL" in io.text


# ── Importación desde la lista de personas ───────────────────────────────

ROWS = [
    ["Lista de personal"],  # título encima de la tabla: se tolera
    [],
    ["Nombre", "Correo", "Puesto"],
    ["Persona Uno", "Jefa.Ventas@MH.com", "Jefa de Ventas"],
    ["Persona Dos", "asistente@mh.com", "Asistente del CEO"],
    ["Persona Tres", "ceo@mh.com", "CEO"],
    ["Persona Cuatro", "sin-arroba", "Comercial"],
    ["Persona Cinco", "jefa.ventas@mh.com", "Ventas"],
    ["Persona Seis", "chef@mh.com", "Chef"],
    ["", "", ""],
]


def _write_csv(path, rows=ROWS):
    with path.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(rows)
    return path


def _write_xlsx(path, rows=ROWS):
    from openpyxl import Workbook

    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    workbook.save(path)
    return path


@pytest.fixture
def data_dir(auth_env, tmp_path):
    return tmp_path  # auth.db vive aquí: es el directorio de datos


@pytest.mark.parametrize("writer", [_write_csv, _write_xlsx], ids=["csv", "xlsx"])
def test_import_is_a_dry_run_by_default(auth_env, data_dir, writer):
    path = writer(data_dir / f"personas.{'csv' if writer is _write_csv else 'xlsx'}")
    io = IO()

    assert io.run("import-users", "--file", str(path)) == 0

    assert auth_env.store.list_users() == []
    assert "Nada cambió" in io.text
    assert "jefa.ventas@mh.com" in io.text and "crear" in io.text
    assert "correo inválido" in io.text and "repetido" in io.text and "sin preset" in io.text
    assert "Persona" not in io.text  # el nombre ni se lee


@pytest.mark.parametrize("writer", [_write_csv, _write_xlsx], ids=["csv", "xlsx"])
def test_import_apply_creates_disabled_users_with_their_preset(auth_env, data_dir, writer):
    path = writer(data_dir / f"personas.{'csv' if writer is _write_csv else 'xlsx'}")

    assert IO().run("import-users", "--file", str(path), "--apply") == 0

    store = auth_env.store
    users = {u.username: u for u in store.list_users()}
    assert set(users) == {"jefa.ventas@mh.com", "asistente@mh.com", "ceo@mh.com"}
    assert all(not u.active for u in users.values())
    ventas = users["jefa.ventas@mh.com"]
    assert ventas.role == "lider"
    assert store.get_memberships(ventas.id) == {Area.GENERAL: I, Area.COMERCIAL: C,
                                                Area.INVENTARIO: C}  # fmt: skip
    assert users["asistente@mh.com"].role == "consulta"
    assert users["ceo@mh.com"].role == "direccion"
    # Nadie conoce la contraseña: no puede entrar hasta reset-password + enable-user.
    assert auth.authenticate("ceo@mh.com", PASSWORD).status == "invalid"
    # El nombre no queda en la base.
    assert b"Persona" not in store.path.read_bytes()


def test_reimport_updates_changed_people_and_closes_their_sessions(auth_env, data_dir, make_user):
    user = make_user("jefa.ventas@mh.com", role="consulta")
    token = auth.authenticate("jefa.ventas@mh.com", PASSWORD).token
    make_user("exempleado@mh.com")
    path = _write_csv(data_dir / "personas.csv")
    io = IO()

    assert io.run("import-users", "--file", str(path), "--apply") == 0

    assert auth_env.store.get_user(user.id).role == "lider"
    assert auth_env.store.get_user(user.id).active is True  # el estado no se toca
    assert auth.resolve_session(token) is None
    assert auth.authenticate("jefa.ventas@mh.com", PASSWORD).status == "ok"  # ni la contraseña
    assert "no se toca" in io.text and "exempleado@mh.com" in io.text
    assert auth_env.store.get_user_by_username("exempleado@mh.com") is not None

    again = IO()
    assert again.run("import-users", "--file", str(path)) == 0
    assert "sin cambios" in again.text and "crear" not in again.text.replace("creados", "")


def test_import_refuses_files_outside_the_data_directory(auth_env, tmp_path, capsys):
    outside = tmp_path.parent / "fuera.csv"
    _write_csv(outside)
    try:
        assert IO().run("import-users", "--file", str(outside)) == 1
        assert "directorio de datos" in capsys.readouterr().err
    finally:
        outside.unlink()


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("personas.txt", "correo,puesto\n", "Formato no soportado"),
        ("vacio.csv", "Nombre,Algo\nx,y\n", "No encontré columnas"),
    ],
)
def test_import_rejects_bad_files(auth_env, data_dir, capsys, name, content, message):
    (data_dir / name).write_text(content, encoding="utf-8")

    assert IO().run("import-users", "--file", str(data_dir / name)) == 1
    assert message in capsys.readouterr().err


def test_import_rejects_a_missing_file(auth_env, data_dir, capsys):
    assert IO().run("import-users", "--file", str(data_dir / "no-existe.csv")) == 1
    assert "No existe" in capsys.readouterr().err


# ── Presets por puesto ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("puesto", "preset"),
    [
        ("Asistente del CEO", "asistente_direccion"),  # antes que CEO
        ("CEO", "direccion"),
        ("Directora General", "direccion"),
        ("Jefe de Sistemas", "sistemas"),
        ("Gerente de TI", "sistemas"),
        ("Recursos Humanos", "rh"),
        ("Contadora", "finanzas"),
        ("Administración y Finanzas", "finanzas"),
        ("Ejecutivo Comercial", "comercial"),
        ("Coordinador de Logística", "logistica"),
        ("Compras", "sourcing"),
        ("Mercadotecnia", "marketing"),
        ("Líder de Proyectos", "proyectos"),
        ("Jefe de Taller", "taller"),
    ],
)
def test_presets_by_job_title(puesto, preset):
    assert preset_for(puesto).name == preset


@pytest.mark.parametrize("puesto", ["", "Chef", "Tienda", "Asistencia", "Practicante"])
def test_unknown_job_titles_have_no_preset(puesto):
    assert preset_for(puesto) is None


def test_whole_words_only():
    # "ti" no debe coincidir dentro de "Logística" ni "tienda"; "rh" no dentro de otra palabra.
    assert preset_for("Logística").name == "logistica"
    assert preset_for("Tienda") is None


def test_presets_respect_least_privilege():
    for _keys, preset in PRESETS:
        if preset.role is Role.CONTROL_TOTAL:
            assert preset.memberships == {}
        if preset.role is Role.LIDER:
            assert preset.memberships[Area.GENERAL] is I
            assert Area.DIRECCION not in preset.memberships
        if preset.role is not Role.DIRECCION:
            restricted = {a for a, lvl in preset.memberships.items() if lvl is R}
            assert restricted <= {Area.RH, Area.FINANZAS}
            assert len(restricted) <= 1


# ── Pasarela: la sensibilidad del contenido manda ────────────────────────


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"data_domain": "general", "content_sensitivity": 3}, R),
        ({"data_domain": "general", "content_sensitivity": 1}, I),
        ({"data_domain": "proveedores", "content_sensitivity": 1}, C),  # nunca baja
        ({"data_domain": "general", "content_sensitivity": "basura"}, R),
        ({"data_domain": "general", "content_sensitivity": 99}, R),
    ],
)
def test_outbound_sensitivity_includes_the_content(state, expected):
    assert sensitivity_for_state(state) is expected


def test_import_accepts_semicolon_separated_csv(auth_env, data_dir):
    # Excel en español exporta CSV con ';'.
    path = data_dir / "personas.csv"
    path.write_text("Correo;Puesto\njefa.ventas@mh.com;Ventas\n", encoding="utf-8-sig")
    io = IO()

    assert io.run("import-users", "--file", str(path)) == 0
    assert "jefa.ventas@mh.com" in io.text and "comercial" in io.text
